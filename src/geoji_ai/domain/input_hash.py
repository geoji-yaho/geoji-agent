"""사전 준비 입력 hash(05 §3.2)와 부분 키(9/14 D-27, 05 §3.3).

`input_hash(snapshot)` = sha256(canonical(스냅샷의 post 필드 · audience · room_snapshots ·
privacy_versions · intake_result)). 같은 사건·같은 공개 범위·같은 epoch 면 같은 값이고, 그중
하나라도 바뀌면 `trial_prep` 새 행이 된다. `jury`·`verdict_final`·`comment` 는 넣지 않는다.

D-27 부분 키. 입력이 바뀌면 바뀐 부분만 다시 만든다.

- `dossier_key(snapshot)` = sha256(canonical(post 필드 · intake_result · 방별 규칙 버전
  `[(room_id, rule_version)]`)). 방 강도·audience_version·privacy_versions·jury 는 넣지 않는다.
  epoch 는 dossier `privacy_versions` 동등 비교로 따로 본다(epoch 가 바뀌면 재사용 없음).
  기억 recall 결과도 넣지 않는다 — 시간에 따라 바뀌어 넣으면 재사용이 거의 일어나지 않는다
  (코디네이터 해석 9/14, 트레이드오프: 새 과거 기록이 생겨도 규칙·게시물이 같으면 이전 조서를 쓴다)
- `banter_key(dossier_id, intensity, prompt_version)` = 그 조서로 만든 그 강도의 드립 후보 키

canonical 규칙은 `draft_hash` 와 같다.

1. pydantic 모델은 `model_dump(mode="json", by_alias=True)` 로 편다
2. 모든 문자열(키와 값)을 유니코드 NFC 로 정규화한다. 배열 순서는 유지한다
   (조서 키의 방 규칙 목록만 room_id 순)
3. `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
4. UTF-8 바이트의 sha256 소문자 hex
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from typing import Any

__all__ = ["POST_FIELDS", "banter_key", "canonical_input", "dossier_key", "input_hash"]

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


def _sha256(payload: Mapping[str, Any]) -> str:
    text = json.dumps(_nfc(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_input(snapshot: Any) -> bytes:
    """`CaseSnapshot`(또는 같은 모양의 dict) → canonical JSON UTF-8 바이트."""
    data = _plain(snapshot)
    payload = {name: data.get(name) for name in (*POST_FIELDS, *_OTHER_FIELDS)}
    text = json.dumps(_nfc(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def input_hash(snapshot: Any) -> str:
    """`canonical_input` 의 sha256 hex."""
    return hashlib.sha256(canonical_input(snapshot)).hexdigest()


def dossier_key(snapshot: Any) -> str:
    """조서 키(D-27). post 필드 · intake_result · 방별 규칙 버전.

    강도·epoch·recall 은 넣지 않는다."""
    data = _plain(snapshot)
    rooms = sorted(
        (
            [str(room["room_id"]), int(room["rule_version"])]
            for room in data.get("room_snapshots") or []
        ),
        key=lambda pair: pair[0],
    )
    payload = {name: data.get(name) for name in POST_FIELDS}
    payload["intake_result"] = data.get("intake_result")
    payload["room_rule_versions"] = rooms
    return _sha256(payload)


def banter_key(dossier_id: str, intensity: Any, prompt_version: str) -> str:
    """드립 키(D-27). 조서 id + 강도 + prompt_version. `intensity` 는 `Intensity` 또는 값 문자열."""
    return _sha256(
        {
            "dossier_id": str(dossier_id),
            "intensity": str(getattr(intensity, "value", intensity)),
            "prompt_version": str(prompt_version),
        }
    )
