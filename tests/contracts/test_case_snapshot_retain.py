"""`CaseSnapshot` RETAIN 확장 필드(`verdict_final`·`comment`, 10 §4.1 제안).

선택 필드라 기존 스냅샷은 그대로 유효해야 한다. pydantic 미러와 정본 JSON Schema 양쪽으로 단언한다.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from geoji_ai.contracts.case import CaseSnapshot
from tests.conftest import load_fixture, load_schema

VERDICT_FINAL: dict[str, Any] = {
    "sentence": "oneDay",
    "sentence_source": "AI",
    "sentencing_reason": "늦잠은 사유가 아니다",
    "reason_source": "AI",
    "applied_intensity": "spicy",
    "banter_strategy": "EXCUSE_STRIPPING",
}

COMMENT: dict[str, Any] = {
    "comment_id": "comment-01",
    "version": 1,
    "room_id": "room-ddegeoji-01",
    "post_id": "post-taxi-20260907-0852",
    "post_status": "judged",
    "author_id": "user-01H8Z9QL",
    "content": "버스 타라",
    "created_at": "2026-09-07T09:40:00Z",
}


def _validator() -> Draft202012Validator:
    return Draft202012Validator(load_schema("case-snapshot"))


def _base() -> dict[str, Any]:
    return load_fixture("case-snapshot-taxi")


def _filled() -> dict[str, Any]:
    snapshot = _base()
    snapshot["verdict_final"] = copy.deepcopy(VERDICT_FINAL)
    snapshot["comment"] = copy.deepcopy(COMMENT)
    return snapshot


def _assert_valid(snapshot: dict[str, Any]) -> None:
    CaseSnapshot.model_validate(snapshot)
    errors = list(_validator().iter_errors(snapshot))
    assert errors == [], [e.message for e in errors]


def _assert_invalid(snapshot: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CaseSnapshot.model_validate(snapshot)
    assert not _validator().is_valid(snapshot)


def test_기존_스냅샷은_새_키가_없어도_유효하다() -> None:
    snapshot = _base()
    assert "verdict_final" not in snapshot
    assert "comment" not in snapshot

    _assert_valid(snapshot)
    parsed = CaseSnapshot.model_validate(snapshot)
    assert parsed.verdict_final is None
    assert parsed.comment is None


def test_새_필드는_required_에_없다() -> None:
    required = load_schema("case-snapshot")["$defs"]["CaseSnapshot"]["required"]
    assert "verdict_final" not in required
    assert "comment" not in required


def test_verdict_final_과_comment_를_채운_스냅샷은_유효하다() -> None:
    _assert_valid(_filled())


def test_무죄_판결은_형량_관련_필드가_null_이어도_유효하다() -> None:
    snapshot = _filled()
    snapshot["verdict_final"].update(
        sentence=None,
        sentence_source=None,
        sentencing_reason=None,
        reason_source=None,
        banter_strategy=None,
    )
    _assert_valid(snapshot)


def test_verdict_final_에_알_수_없는_필드는_거부한다() -> None:
    snapshot = _filled()
    snapshot["verdict_final"]["judge_name"] = "심판관"
    _assert_invalid(snapshot)


def test_reason_source_HUMAN_은_거부한다() -> None:
    snapshot = _filled()
    snapshot["verdict_final"]["reason_source"] = "HUMAN"
    _assert_invalid(snapshot)


def test_comment_version_0_은_거부한다() -> None:
    snapshot = _filled()
    snapshot["comment"]["version"] = 0
    _assert_invalid(snapshot)


def test_comment_content_1000자는_허용한다() -> None:
    snapshot = _filled()
    snapshot["comment"]["content"] = "가" * 1000
    _assert_valid(snapshot)


def test_comment_content_1001자는_거부한다() -> None:
    snapshot = _filled()
    snapshot["comment"]["content"] = "가" * 1001
    _assert_invalid(snapshot)
