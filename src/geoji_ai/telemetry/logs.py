"""노드 로그 필드(08 §3.3 로그).

§3.3 이 적은 필드만 로그로 내보낸다. 원문이 될 수 있는 필드(사유·댓글·항목·진술·헤드라인)와
인증 값(헤더·API 키·토큰)은 값 대신 `sha256` 앞 12자(`*_hash`)와 길이(`*_len`)만 남긴다.
목록에 없는 필드는 버리고 이름만 `dropped_fields` 로 남긴다 — 새 필드는 이 목록에 먼저 올린다.

로거는 `core/logging` 의 structlog 설정을 그대로 쓴다. 호출마다 `get_logger()` 로 새로 받는다
(모듈 전역에 잡아 두면 첫 사용 때의 stdout 에 묶여 캡처가 어긋난다).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from geoji_ai.core.logging import get_logger

__all__ = ["HASH_PREFIX_LEN", "LOG_FIELDS", "RAW_FIELDS", "log_node", "sanitize_fields"]

#: §3.3 로그 목록. `graph_name/version` 은 두 필드로,
#: "조회 결과 개수" 는 `result_count`,
#: "위반 코드" 는 `violation_codes`, "폴백 원인" 은 `fallback_reason`(코드),
#: "토큰" 은 두 필드로 푼다.
LOG_FIELDS: frozenset[str] = frozenset(
    {
        "trace_id",
        "job_id",
        "generation_id",
        "dossier_id",
        "graph_name",
        "graph_version",
        "prompt_bundle_version",
        "guardrail_policy_version",
        "vendor",
        "model_id",
        "node",
        "latency_ms",
        "ok",
        "result_count",
        "recall_memory_ids",
        "evidence_labels",
        "banter_strategy",
        "attack_angle",
        "violation_codes",
        "repair_count",
        "fallback_reason",
        "prompt_tokens",
        "completion_tokens",
        "micro_usd",
    }
)

#: 원문·인증 값. 이름이 정확히 같거나 아래 접미사로 끝나면 hash·len 으로 바꾼다.
RAW_FIELDS: frozenset[str] = frozenset(
    {
        "reason",
        "content",
        "comment",
        "comments",
        "item",
        "statement",
        "headline",
        "text",
        "authorization",
        "api_key",
        "token",
    }
)
_RAW_SUFFIXES: tuple[str, ...] = ("_api_key", "_token", "_secret", "_password", "_authorization")

#: `*_hash` 에 남기는 sha256 16진 앞자리 수.
HASH_PREFIX_LEN = 12


def _is_raw(name: str) -> bool:
    lowered = name.lower()
    return lowered in RAW_FIELDS or lowered.endswith(_RAW_SUFFIXES)


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _length(value: Any) -> int:
    if isinstance(value, (list, tuple, dict, set, frozenset)):
        return len(value)
    return len(_as_text(value))


def sanitize_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """로그로 내보낼 필드만 남긴다. 원문은 hash·len, 목록 밖은 버린다."""
    out: dict[str, Any] = {}
    dropped: list[str] = []
    for name, value in fields.items():
        if _is_raw(name):
            digest = hashlib.sha256(_as_text(value).encode("utf-8")).hexdigest()
            out[f"{name}_hash"] = digest[:HASH_PREFIX_LEN]
            out[f"{name}_len"] = _length(value)
        elif name in LOG_FIELDS:
            out[name] = value
        else:
            dropped.append(name)
    if dropped:
        out["dropped_fields"] = sorted(dropped)
    return out


def log_node(event: str, **fields: Any) -> dict[str, Any]:
    """노드 로그 한 줄. 내보낸 필드를 돌려준다(테스트·호출자 확인용)."""
    safe = sanitize_fields(fields)
    get_logger("geoji_ai.telemetry").info(event, **safe)
    return safe
