"""새 카드 생성 규격과 기존 저장 판결문 계약의 호환성."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from geoji_ai.contracts.llm_schemas import writer_schema
from geoji_ai.contracts.writer import CardTextDraft, TextDraft
from tests.conftest import load_fixture


def card_text() -> dict:
    text = deepcopy(load_fixture("writer-draft-taxi")["texts"][1])
    text["statement"] = [text["statement"][0]]
    return text


def test_card_exact_length_boundaries_preserve_metadata() -> None:
    text = card_text()
    text["headline"] = "가" * 20
    text["statement"][0]["text"] = "나" * 100
    assert CardTextDraft.model_validate(text).model_dump(mode="json") == text


@pytest.mark.parametrize("field", ["headline", "statement"])
@pytest.mark.parametrize("value", ["", "   ", "\t", "앞\n뒤", "앞\r뒤", "앞\u2028뒤", "끝\n"])
def test_card_rejects_blank_or_multiline_text(field: str, value: str) -> None:
    text = card_text()
    if field == "headline":
        text[field] = value
    else:
        text[field][0]["text"] = value
    with pytest.raises(ValidationError):
        CardTextDraft.model_validate(text)


@pytest.mark.parametrize(("field", "limit"), [("headline", 20), ("statement", 100)])
def test_card_rejects_overlength_text(field: str, limit: int) -> None:
    text = card_text()
    if field == "headline":
        text[field] = "가" * (limit + 1)
    else:
        text[field][0]["text"] = "나" * (limit + 1)
    with pytest.raises(ValidationError):
        CardTextDraft.model_validate(text)


@pytest.mark.parametrize("count", [0, 2, 4])
def test_card_requires_exactly_one_statement(count: int) -> None:
    text = card_text()
    text["statement"] *= count
    with pytest.raises(ValidationError):
        CardTextDraft.model_validate(text)


def test_persisted_contract_accepts_new_and_historical_text() -> None:
    TextDraft.model_validate(card_text())
    for text in load_fixture("writer-draft-taxi")["texts"]:
        TextDraft.model_validate(text)


def test_writer_schema_enforces_card_limits_and_keeps_metadata() -> None:
    props = writer_schema(["spicy"], ["CONVERSION"])["properties"]
    assert props["headline"]["minLength"] == 1
    assert props["headline"]["maxLength"] == 20
    assert props["statement"]["minItems"] == props["statement"]["maxItems"] == 1
    body = props["statement"]["items"]["properties"]
    assert body["text"]["maxLength"] == 100
    assert body["text"]["minLength"] == 1
    assert {"kind", "evidence_labels"} <= body.keys()
    assert {"banter_strategy", "selected_candidate_id", "meme_tag", "meme_hints"} <= props.keys()
