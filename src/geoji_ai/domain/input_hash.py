"""사전 준비 입력 hash(05 §3.2).

`input_hash(snapshot)` = sha256(canonical(스냅샷의 post 필드 · audience · room_snapshots ·
privacy_versions · intake_result)). 같은 사건·같은 공개 범위·같은 epoch 면 같은 값이고, 그중
하나라도 바뀌면 `trial_prep` 새 행이 된다. `jury`·`verdict_final`·`comment` 는 넣지 않는다.

canonical 규칙은 `draft_hash` 와 같다.

1. pydantic 모델은 `model_dump(mode="json", by_alias=True)` 로 편다
2. 모든 문자열(키와 값)을 유니코드 NFC 로 정규화한다. 배열 순서는 유지한다
3. `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
4. UTF-8 바이트의 sha256 소문자 hex
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from typing import Any

__all__ = ["POST_FIELDS", "canonical_input", "input_hash"]

#: 스냅샷 최상위의 post 필드(01 §3.2 평면 구조).
POST_FIELDS: tuple[str, ...] = (
    "post_id",
    "author_id",
    "post_version",
    "item",
    "reason",
    "amount_krw",
    "category",
    "post_type",
    "created_at",
)

_OTHER_FIELDS: tuple[str, ...] = ("audience", "room_snapshots", "privacy_versions", "intake_result")


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        return {_nfc(str(key)): _nfc(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_nfc(item) for item in value]
    return value


def _plain(snapshot: Any) -> Mapping[str, Any]:
    if hasattr(snapshot, "model_dump"):
        return snapshot.model_dump(mode="json", by_alias=True)
    return snapshot


def canonical_input(snapshot: Any) -> bytes:
    """`CaseSnapshot`(또는 같은 모양의 dict) → canonical JSON UTF-8 바이트."""
    data = _plain(snapshot)
    payload = {name: data.get(name) for name in (*POST_FIELDS, *_OTHER_FIELDS)}
    text = json.dumps(_nfc(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def input_hash(snapshot: Any) -> str:
    """`canonical_input` 의 sha256 hex."""
    return hashlib.sha256(canonical_input(snapshot)).hexdigest()
