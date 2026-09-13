"""draft hash canonical 규칙(05 §3.4, 스펙 케이스 ⑤⑥)."""

from __future__ import annotations

import copy
import json
import unicodedata
from typing import Any

from geoji_ai.contracts.sentencing import SentencingDecision
from geoji_ai.contracts.writer import WriterDraft
from geoji_ai.domain.draft_hash import canonical, draft_hash


def _draft() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "texts": [
            {
                "intensity": "spicy",
                "headline": "택시가 출근 수단이 된 사건",
                "statement": [
                    {
                        "text": "일주일에 세 번, 택시가 출근 수단이 됐습니다.",
                        "kind": "fact",
                        "evidence_labels": ["F1"],
                    },
                    {
                        "text": "지하철은 오늘도 정시에 왔습니다.",
                        "kind": "opinion",
                        "evidence_labels": [],
                    },
                ],
                "banter_strategy": "EXCUSE_STRIPPING",
                "selected_candidate_id": None,
                "attack_angle": "CONVERSION",
                "source": "AI",
            }
        ],
        "meme_tag": "GUILTY_LIGHT",
        "meme_hints": None,
    }


def _sentencing() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "sentence": "oneDay",
        "sentencing_reason": "유죄율 75%에 이번 주 세 번째라 징역 1일로 둔다.",
        "reason_source": "AI",
        "evidence_labels": ["F1"],
        "aggravating": ["주 3회"],
        "mitigating": [],
    }


def _reverse_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _reverse_keys(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [_reverse_keys(item) for item in value]
    return value


def _nfd(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFD", value)
    if isinstance(value, dict):
        return {key: _nfd(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_nfd(item) for item in value]
    return value


def test_same_hash_across_key_order_whitespace_and_normalization() -> None:
    """⑤ 키 순서·공백·NFC/NFD 차이에도 같은 hash."""
    base = draft_hash(_draft(), _sentencing())

    assert draft_hash(_reverse_keys(_draft()), _reverse_keys(_sentencing())) == base

    # 입력 직렬화의 공백은 canonical 에 남지 않는다.
    # 들여쓴 JSON 을 거쳐도 바이트가 같고 구분자 뒤 공백이 없다
    pretty_text = json.dumps(_draft(), indent=2, ensure_ascii=True)
    assert "\n" in pretty_text
    raw = canonical(json.loads(pretty_text), _sentencing())
    assert raw == canonical(_draft(), _sentencing())
    assert b"\n" not in raw and b'": ' not in raw and b'", "' not in raw

    nfd_draft = _nfd(_draft())
    assert nfd_draft != _draft()
    assert draft_hash(nfd_draft, _nfd(_sentencing())) == base

    models = (
        WriterDraft.model_validate(_draft()),
        SentencingDecision.model_validate(_sentencing()),
    )
    assert draft_hash(*models) == base


def test_canonical_is_sorted_compact_utf8() -> None:
    raw = canonical(_draft(), None)
    text = raw.decode("utf-8")
    assert text.startswith('{"draft":{"meme_hints":null,"meme_tag":"GUILTY_LIGHT",')
    assert text.endswith(',"sentencing":null}')
    assert ": " not in text and ", " not in text.replace("세 번, 택시", "")
    assert "택시" in text


def test_one_character_change_changes_hash() -> None:
    """⑥ 문구 1글자 변경 → 다른 hash."""
    base = draft_hash(_draft(), _sentencing())
    changed = copy.deepcopy(_draft())
    changed["texts"][0]["statement"][1]["text"] = "지하철은 오늘도 정시에 왔습니까."
    assert draft_hash(changed, _sentencing()) != base

    reason = _sentencing()
    reason["sentencing_reason"] = reason["sentencing_reason"].replace("1일", "2일")
    assert draft_hash(_draft(), reason) != base
