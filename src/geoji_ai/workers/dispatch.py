"""dispatch 표와 큐 규약 상수(02 §3.3·§3.4).

`kind → handler` 표와 이벤트별 규약(`priority`·`max_attempts`·`deadline`·`dedupe_key`)을
한곳에 둔다. 02 §3.4 표가 RETAIN 을 두 행(`sentence.finalized`·`comment.approved`)으로
나누므로 규약의 키는 kind 가 아니라 **`event_type`** 이다.

작업 2 는 4 kind 모두 `NotImplementedHandler` 다(즉시 `fail("NOT_IMPLEMENTED", 60)`).
작업 3·4·5 가 `HANDLERS` 를 갈아끼운다.

payload 는 `contracts/jobs.py` 의 4형 그대로다(`extra="forbid"`). 10 §3 의
`intensities[]` 와 RETAIN 의 `verdict_version` 은 백엔드 미채택이라 넣지 않는다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from geoji_ai.contracts.jobs import Job, JobKind
from geoji_ai.core.config import Settings
from geoji_ai.ports.jobs import JobsPort

__all__ = [
    "DEFAULT_ROUTE_BY_KIND",
    "HANDLERS",
    "JOB_ROUTES",
    "ROUTES_BY_KIND",
    "SLOT_KINDS",
    "Handler",
    "HandlerContext",
    "JobRoute",
    "NotImplementedHandler",
    "build_dedupe_key",
    "handler_for",
]


@dataclass(frozen=True)
class JobRoute:
    """02 §3.4 표 한 행. 백엔드 INSERT 와 `scripts/enqueue_job.py` 가 같은 값을 쓴다."""

    event_type: str
    kind: JobKind
    priority: int
    max_attempts: int
    #: `None` 이면 `deadline_at` 은 NULL 이다.
    deadline_after_s: float | None
    dedupe_template: str
    #: payload 안의 (aggregate_id 필드, aggregate_version 필드).
    aggregate_fields: tuple[str, str]


#: 02 §3.4 표 5행. key 는 `event_type` 이다.
JOB_ROUTES: dict[str, JobRoute] = {
    "post.created": JobRoute(
        event_type="post.created",
        kind="PREPARE",
        priority=30,
        max_attempts=2,
        deadline_after_s=None,
        dedupe_template="prepare:{post_id}:{post_version}:{audience_version}",
        aggregate_fields=("post_id", "post_version"),
    ),
    "verdict.confirmed": JobRoute(
        event_type="verdict.confirmed",
        kind="SENTENCE",
        priority=100,
        max_attempts=2,
        deadline_after_s=10,
        dedupe_template="sentence:{verdict_id}:{verdict_version}",
        aggregate_fields=("verdict_id", "verdict_version"),
    ),
    "sentence.finalized": JobRoute(
        event_type="sentence.finalized",
        kind="RETAIN",
        priority=10,
        max_attempts=5,
        deadline_after_s=None,
        dedupe_template="retain:verdict:{verdict_id}:{version}",
        aggregate_fields=("verdict_id", "version"),
    ),
    "verdict.text_retry": JobRoute(
        event_type="verdict.text_retry",
        kind="TEXT_RETRY",
        priority=50,
        max_attempts=1,
        deadline_after_s=20,
        dedupe_template="text-retry:{verdict_id}:{verdict_version}:{round}",
        aggregate_fields=("verdict_id", "verdict_version"),
    ),
    "comment.approved": JobRoute(
        event_type="comment.approved",
        kind="RETAIN",
        priority=10,
        max_attempts=5,
        deadline_after_s=None,
        dedupe_template="retain:comment:{comment_id}:{version}",
        aggregate_fields=("comment_id", "version"),
    ),
}


def _routes_by_kind() -> dict[str, tuple[JobRoute, ...]]:
    grouped: dict[str, list[JobRoute]] = {}
    for route in JOB_ROUTES.values():
        grouped.setdefault(route.kind, []).append(route)
    return {kind: tuple(routes) for kind, routes in grouped.items()}


#: kind 하나에 event_type 이 여럿일 수 있다(RETAIN 은 2개).
ROUTES_BY_KIND: dict[str, tuple[JobRoute, ...]] = _routes_by_kind()

#: `event_type` 을 주지 않았을 때의 기본 규약. RETAIN 은 `sentence.finalized` 다.
DEFAULT_ROUTE_BY_KIND: dict[str, JobRoute] = {
    kind: routes[0] for kind, routes in ROUTES_BY_KIND.items()
}


def build_dedupe_key(route: JobRoute, payload: Mapping[str, Any]) -> str:
    """02 §3.4 의 `dedupe_key` 규약. payload 필드를 템플릿에 끼운다."""
    return route.dedupe_template.format(**payload)


#: 02 §3.3 슬롯 3종. 슬롯마다 독립 claim loop 이 돌고 이 `kinds` 로 필터한다.
SLOT_KINDS: dict[str, tuple[str, ...]] = {
    "SENTENCE": ("SENTENCE",),
    "PREPARE": ("PREPARE",),
    "BACKGROUND": ("TEXT_RETRY", "RETAIN"),
}


@dataclass
class HandlerContext:
    """핸들러가 받는 실행 문맥. 작업 3·4·5 가 이 자리를 그대로 쓴다."""

    jobs: JobsPort
    #: 프로세스 전역 세마포어(`MODEL_CONCURRENCY_LIMIT`). 모델 호출이 이것을 지난다.
    semaphore: asyncio.Semaphore
    settings: Settings
    generation_id: str
    worker_id: str


class Handler(Protocol):
    async def __call__(self, job: Job, ctx: HandlerContext) -> None: ...


class NotImplementedHandler:
    """작업 3·4·5 가 갈아끼운다. 지금은 즉시 `fail` 로 되돌린다."""

    async def __call__(self, job: Job, ctx: HandlerContext) -> None:
        await ctx.jobs.fail(
            job.id,
            ctx.worker_id,
            ctx.generation_id,
            error_code="NOT_IMPLEMENTED",
            retry_after_s=60,
        )


_not_implemented = NotImplementedHandler()

#: 작업 2 는 4 kind 전부 스텁이다.
HANDLERS: dict[str, Handler] = {
    "PREPARE": _not_implemented,
    "SENTENCE": _not_implemented,
    "TEXT_RETRY": _not_implemented,
    "RETAIN": _not_implemented,
}


def handler_for(kind: str) -> Handler:
    """kind 에 맞는 핸들러. 표에 없는 kind 는 `KeyError` 다(DDL CHECK 가 먼저 막는다)."""
    return HANDLERS[kind]
