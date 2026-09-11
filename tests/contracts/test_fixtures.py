"""fixture 12개와 그 밖의 fixture 파일 전부를 해당 스키마로 검증한다(01 §4.1, §3.4).

부록 A.2 원문은 바이트 그대로 보존되어야 한다.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from geoji_ai.contracts.llm_schemas import context_schema
from geoji_ai.contracts.writer import BanterStrategy
from geoji_ai.domain.intensity import ALL_INTENSITIES
from tests.conftest import FIXTURES_DIR, load_fixture, load_schema

# 01 §3.4 의 11개 + `writer-draft-taxi`(12번째, 게이트 답 2)
FIXTURE_NAMES = (
    "taxi-hell-input",
    "taxi-hell-requested-output",
    "taxi-hell-expected-evaluation.guardrail-v1",
    "taxi-hell-expected-evaluation.guardrail-v2",
    "case-snapshot-taxi",
    "jury-guilty-75",
    "jury-rejected",
    "jury-not-guilty",
    "dossier-taxi",
    "banter-taxi",
    "templates-v1",
    "writer-draft-taxi",
)

# 부록 A.2 원문. 한 글자도 바꾸지 않는다.
APPENDIX_A2_TEXT = (
    "어휴 이새X어쩌지; 생체리듬이고 양심이고 의지라곤 눈꼽만큼도 없는 노답. "
    "내가 더 어떻게 해야 들어쳐먹을래?"
)

# 파일명 접두 → (스키마 파일, `$defs` 이름)
SCHEMA_BY_PREFIX = (
    ("case-snapshot-", ("case-snapshot", "CaseSnapshot")),
    ("jury-", ("case-snapshot", "JurySnapshot")),
    ("intake-", ("intake", "IntakeResult")),
    ("sentencing-", ("sentencing", "SentencingDecision")),
    ("writer-draft-", ("writer-draft", "WriterDraft")),
    ("evaluation-", ("evaluation", "EvaluationReport")),
    ("taxi-hell-expected-evaluation.", ("evaluation", "EvaluationReport")),
    ("finalize-", ("finalize", "FinalizeRequest")),
    ("verdict-view-", ("verdict-view", "VerdictView")),
)

# 스키마가 없는 fixture — 구조만 본다.
NO_SCHEMA = ("dossier-", "banter-", "context-", "templates-")
RAW_FILES = ("taxi-hell-input", "taxi-hell-requested-output")


def fixture_files() -> list[Path]:
    return sorted(FIXTURES_DIR.glob("*.json"))


def validator_for(schema_name: str, defs_name: str) -> Draft202012Validator:
    document = load_schema(schema_name)
    sub = {
        "$schema": document["$schema"],
        "$id": document["$id"],
        "$defs": document["$defs"],
        "$ref": f"#/$defs/{defs_name}",
    }
    return Draft202012Validator(sub, format_checker=Draft202012Validator.FORMAT_CHECKER)


def test_twelve_fixtures_exist() -> None:
    missing = [name for name in FIXTURE_NAMES if not (FIXTURES_DIR / f"{name}.json").exists()]
    assert not missing, f"없는 fixture: {missing}"
    assert len(FIXTURE_NAMES) == 12


@pytest.mark.parametrize("path", fixture_files(), ids=lambda p: p.stem)
def test_every_fixture_validates(path: Path) -> None:
    name = path.stem
    payload: Any = json.loads(path.read_text(encoding="utf-8"))

    if name in RAW_FILES:
        assert isinstance(payload, dict)
        return

    for prefix in NO_SCHEMA:
        if name.startswith(prefix):
            if prefix == "context-":
                Draft202012Validator(context_schema()).validate(payload)
            else:
                assert isinstance(payload, dict)
            return

    for prefix, (schema_name, defs_name) in SCHEMA_BY_PREFIX:
        if name.startswith(prefix):
            body = payload["expected"] if "expected" in payload else payload
            validator_for(schema_name, defs_name).validate(body)
            return

    pytest.fail(f"{name}: 파일명 → 스키마 규약에 없는 fixture 다")


def test_requested_output_is_byte_identical_to_appendix_a2() -> None:
    text = load_fixture("taxi-hell-requested-output")["text"]

    assert text.encode("utf-8") == APPENDIX_A2_TEXT.encode("utf-8")


def test_input_fixture_keeps_appendix_a1_values() -> None:
    payload = load_fixture("taxi-hell-input")

    assert payload == {
        "reason": "늦잠자서 출근할 때 택시 탐 9200",
        "amount_krw": 9200,
        "intensity": "hell",
    }


def test_guardrail_v1_expectation() -> None:
    report = load_fixture("taxi-hell-expected-evaluation.guardrail-v1")
    text = report["texts"][0]

    assert report["policy_version"] == "guardrail-v1"
    assert text["intensity"] == "hell"
    assert text["pass"] is False
    assert [v["code"] for v in text["violations"]] == ["PERSONAL_ATTACK"]
    assert text["violations"][0]["path"] == "texts[0].statement[0].text"
    assert text["violations"][0]["evidence_labels"] == []
    assert text["problem_sentences"] == [APPENDIX_A2_TEXT]


def test_guardrail_v2_expectation_is_open() -> None:
    payload = load_fixture("taxi-hell-expected-evaluation.guardrail-v2")
    report = payload["expected"]
    text = report["texts"][0]

    assert report["policy_version"] == "guardrail-v2"
    assert text["pass"] is True
    assert text["violations"] == []
    assert payload["open_questions"], "PROFANITY_OUT_OF_LIST 미확정은 열어 둔다"
    assert any("PROFANITY_OUT_OF_LIST" in q for q in payload["open_questions"])


def test_case_snapshot_jury_equals_jury_fixture() -> None:
    snapshot = load_fixture("case-snapshot-taxi")

    assert snapshot["jury"] == load_fixture("jury-guilty-75")
    assert snapshot["amount_krw"] == 12000
    assert snapshot["category"] == "교통/택시"
    assert snapshot["post_type"] == "spent"
    assert snapshot["intake_result"] is None


def test_jury_fixtures_cover_three_results() -> None:
    results = {
        "jury-guilty-75": "guilty",
        "jury-rejected": "disagree",
        "jury-not-guilty": "notGuilty",
    }
    for name, result in results.items():
        jury = load_fixture(name)
        assert jury["result"] == result
        assert jury["policy"]["allowed_sentences"] == [
            {"code": "probation", "rank": 1},
            {"code": "oneDay", "rank": 2},
        ]
        assert jury["policy"]["fallback_sentence"] == "oneDay"

    assert load_fixture("jury-guilty-75")["guilty_ratio"] == 0.75


def test_dossier_labels_match_label_map() -> None:
    dossier = load_fixture("dossier-taxi")
    labels = [fact["label"] for fact in dossier["facts"]]

    assert labels == [f"F{i}" for i in range(7)]
    assert sorted(dossier["label_map"]) == sorted(labels)
    assert uuid.UUID(dossier["dossier_id"])
    for fact in dossier["facts"]:
        assert fact["kind"] and fact["text"]


def test_banter_candidates() -> None:
    candidates = load_fixture("banter-taxi")["candidates"]
    intensity_values = {i.value for i in ALL_INTENSITIES}
    strategies = {s.value for s in BanterStrategy}
    labels = {f"F{i}" for i in range(7)}

    assert len(candidates) == 4
    for candidate in candidates:
        assert uuid.UUID(candidate["id"])
        assert candidate["strategy"] in strategies
        assert candidate["intensity"] in intensity_values
        assert set(candidate["evidence_labels"]) <= labels


def test_templates_cover_four_results() -> None:
    templates = load_fixture("templates-v1")

    assert sorted(templates["results"]) == sorted(["guilty", "notGuilty", "agree", "disagree"])
    assert templates["sentence_labels"] == {
        "probation": "집행유예",
        "oneDay": "징역 1일 (내일 하루 무지출)",
        "life": "무기징역 (3일 무지출)",
    }
    for result, body in templates["results"].items():
        assert body["headline"]
        assert len(body["statement"]) == 1
        if result == "guilty":
            assert body["sentencing_reason_template"] == "형량: {형량 라벨}"
        else:
            assert body["sentencing_reason_template"] is None


def test_writer_draft_fixture_covers_three_intensities() -> None:
    draft = load_fixture("writer-draft-taxi")
    candidate_ids = {c["id"] for c in load_fixture("banter-taxi")["candidates"]}
    labels = {f"F{i}" for i in range(7)}

    assert [t["intensity"] for t in draft["texts"]] == ["mild", "spicy", "hell"]
    assert draft["meme_tag"] == "GUILTY_LIGHT"
    for text in draft["texts"]:
        assert text["source"] == "AI"
        assert text["attack_angle"] == "CONVERSION"
        assert text["selected_candidate_id"] in candidate_ids | {None}
        assert sum(len(s["text"]) for s in text["statement"]) <= 300
        for statement in text["statement"]:
            assert set(statement["evidence_labels"]) <= labels
