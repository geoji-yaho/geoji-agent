"""structlog 설정(01 §3.7).

JSON 렌더러 + `contextvars` 로 `trace_id` 를 묶는다. 한 판결의 모든 로그가 같은
`trace_id` 를 달고 나가야 실패를 되짚을 수 있다(08).

비밀값은 로그에 남기지 않는다. 설정을 찍을 때는 `config.redact()` 를 거치고,
그래도 새는 것을 막으려고 렌더 직전에 비밀값 키를 한 번 더 가린다.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import structlog

from geoji_ai.core.config import REDACTED, SECRET_FIELDS

__all__ = ["bind_trace_id", "configure_logging", "get_logger", "new_trace_id"]


def _mask_secrets(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """이벤트에 비밀값 키가 있으면 값을 가린다."""
    for key, value in event_dict.items():
        if key.upper() in SECRET_FIELDS and value:
            event_dict[key] = REDACTED
    return event_dict


_STDLIB_HANDLER_MARK = "_geoji_structlog_bridge"


def _bridge_stdlib(numeric: int) -> None:
    """stdlib `logging` 을 같은 JSON 렌더러로 보낸다(9/16).

    그래프(`graphs/*.py`)의 결정 로그는 `logging.getLogger(__name__)` 이라 stdlib 로 나간다.
    이 연결이 없으면 INFO 는 버려지고 WARNING 만 `lastResort` 로 stderr 에 평문으로 남아
    "어느 역할이 왜 폴백했나" 를 운영 로그에서 볼 수 없었다. 여러 번 불러도 핸들러는 하나다.
    """
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        foreign_pre_chain=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.ExtraAdder(),
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _mask_secrets,
        ],
    )
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _STDLIB_HANDLER_MARK, False):
            root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    setattr(handler, _STDLIB_HANDLER_MARK, True)
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > numeric:
        root.setLevel(numeric)


def configure_logging(level: str = "INFO") -> None:
    """JSON 로그로 설정한다. 여러 번 불러도 된다. stdlib 로그도 같은 형식으로 묶는다."""
    numeric = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    _bridge_stdlib(numeric)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _mask_secrets,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def new_trace_id() -> str:
    return str(uuid.uuid4())


def bind_trace_id(trace_id: str) -> None:
    """이 실행 문맥의 로그에 `trace_id` 를 붙인다."""
    structlog.contextvars.bind_contextvars(trace_id=trace_id)


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
