"""실제 Spring 공개 DTO의 camelCase·null을 계약 정본과 함께 검증한다."""

import copy

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from geoji_ai.contracts.verdict_view import VerdictView
from tests.conftest import load_fixture, load_schema


@pytest.mark.parametrize("case", ["pending", "not-guilty", "with-meme"])
def test_public_response_validates_and_roundtrips(case):
    payload = load_fixture(f"verdict-view-{case}")
    Draft202012Validator(load_schema("verdict-view")).validate(payload)
    assert VerdictView.model_validate(payload).model_dump(mode="json", by_alias=True) == payload


def test_public_contract_rejects_internal_field_names():
    payload = load_fixture("verdict-view-pending")
    payload["post_id"] = payload.pop("postId")
    with pytest.raises(ValidationError):
        VerdictView.model_validate(payload)


def test_nullable_fields_still_have_to_be_present():
    payload = copy.deepcopy(load_fixture("verdict-view-not-guilty"))
    del payload["view"]["sentence"]
    with pytest.raises(ValidationError):
        VerdictView.model_validate(payload)
