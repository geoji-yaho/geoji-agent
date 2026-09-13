"""검수 대상과 finalize payload 를 묶는 hash(05 §3.4, 10 §5).

`canonical(draft, sentencing)`:

1. `{"draft": <WriterDraft>, "sentencing": <SentencingDecision 또는 null>}` 객체를 만든다.
   pydantic 모델은 `model_dump(mode="json", by_alias=True)` 로 편다
2. 모든 문자열(키와 값)을 유니코드 NFC 로 정규화한다. 배열 순서는 유지한다
3. `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)` — 키 코드포인트 순 정렬,
   공백 없음, 비 ASCII 는 이스케이프하지 않는다
4. UTF-8 바이트

`draft_hash` = 그 바이트의 sha256 소문자 hex. 백엔드 finalize 는 같은 규칙으로 재계산한다.
검수 뒤에 문구를 한 글자라도 고치면 hash 가 바뀌어 finalize 가 거부한다.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

__all__ = ["canonical", "draft_hash"]


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    return value


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, BaseModel):
        return _nfc(_plain(value))
    if isinstance(value, Mapping):
        return {_nfc(str(key)): _nfc(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_nfc(item) for item in value]
    return value


def canonical(
    draft: BaseModel | Mapping[str, Any], sentencing: BaseModel | Mapping[str, Any] | None
) -> bytes:
    """키 정렬·공백 없음·NFC 정규화 JSON 의 UTF-8 바이트."""
    payload = _nfc({"draft": _plain(draft), "sentencing": _plain(sentencing)})
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def draft_hash(
    draft: BaseModel | Mapping[str, Any], sentencing: BaseModel | Mapping[str, Any] | None
) -> str:
    """`canonical` 의 sha256 hex."""
    return hashlib.sha256(canonical(draft, sentencing)).hexdigest()
