"""백엔드 없이 `ai.jobs` 에 job 하나를 넣는다(02 §3.6·§4.3).

INSERT 는 원래 백엔드 몫이다(`ai_worker` 에는 INSERT 권한이 없다). 이 스크립트는
로컬에서 워커를 돌려 보려고 그 자리를 대신한다. 규약(`priority`·`max_attempts`·
`deadline_at`·`dedupe_key`·`aggregate_*`)은 전부 `workers.dispatch.JOB_ROUTES` 에서
온다 — 여기서 손으로 받지 않는다.

    uv run scripts/enqueue_job.py --kind PREPARE    --post p1 --version 1 --audience 1
    uv run scripts/enqueue_job.py --kind SENTENCE   --verdict v1 --version 1 --post p1
    uv run scripts/enqueue_job.py --kind SENTENCE   --verdict v1 --version 1 --post p1 --no-wait-prepare
    uv run scripts/enqueue_job.py --kind TEXT_RETRY --verdict v1 --version 1 --round 1
    uv run scripts/enqueue_job.py --kind RETAIN --event sentence.finalized --verdict v1 --version 1
    uv run scripts/enqueue_job.py --kind RETAIN --event comment.approved  --comment c1 --version 1

SENTENCE 게이트(9/14 D-24, 10 §3): SENTENCE 는 기본으로 같은 post 의 PREPARE 가 `QUEUED`·`RUNNING`
이면 끝날 때까지(최대 `--prepare-wait-seconds`, 기본 30초) 기다린 뒤 넣는다. 마감은 INSERT 시각 +
10초다. `--no-wait-prepare` 면 바로 넣는다.

접속은 `--url` 또는 `DATABASE_URL`(`.env` 포함)이다. 접속 문자열은 출력하지 않는다.
프로젝트 venv 에서 돈다(`uv run`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.postgres_jobs import make_engine
from geoji_ai.contracts.jobs import (
    PreparePayload,
    RetainPayload,
    SentencePayload,
    TextRetryPayload,
)
from geoji_ai.core.config import get_settings, secret_value
from geoji_ai.workers.dispatch import DEFAULT_ROUTE_BY_KIND, JOB_ROUTES, JobRoute, build_dedupe_key

KINDS = ("PREPARE", "SENTENCE", "TEXT_RETRY", "RETAIN")

#: 10 §3 게이트 최대 대기 `confirmed_at + 30s`(D-24). 이 스크립트는 실행 시각을 확정 시각으로 본다.
PREPARE_WAIT_SECONDS = 30.0
#: 대기 중 PREPARE 상태를 다시 보는 간격. 10 §3 이 게이트를 합쳐도 된다고 한 watchdog 주기(§6 250ms).
PREPARE_POLL_SECONDS = 0.25

_INSERT_TEMPLATE = """
INSERT INTO ai.jobs (
    id, event_id, event_type, kind, dedupe_key,
    aggregate_id, aggregate_version, schema_version, payload,
    priority, max_attempts, deadline_at, trace_id
) VALUES (
    :id, :event_id, :event_type, :kind, :dedupe_key,
    :aggregate_id, :aggregate_version, 1, CAST(:payload AS jsonb),
    :priority, :max_attempts, {deadline}, :trace_id
)
ON CONFLICT (dedupe_key) DO NOTHING
RETURNING id
"""

_PREPARE_ACTIVE_SQL = """
SELECT EXISTS (
    SELECT 1 FROM ai.jobs
    WHERE kind = 'PREPARE' AND payload->>'post_id' = :post_id
      AND status IN ('QUEUED', 'RUNNING')
)
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ai.jobs 에 job 하나를 넣는다(로컬 확인용).")
    parser.add_argument("--kind", required=True, choices=KINDS)
    parser.add_argument(
        "--event",
        choices=sorted(JOB_ROUTES),
        help="event_type. RETAIN 처럼 kind 하나에 둘이면 골라야 한다.",
    )
    parser.add_argument("--post", help="post_id")
    parser.add_argument("--verdict", help="verdict_id")
    parser.add_argument("--comment", help="comment_id")
    parser.add_argument("--version", type=int, required=True, help="aggregate version")
    parser.add_argument("--audience", type=int, help="audience_version (PREPARE)")
    parser.add_argument("--round", type=int, help="round (TEXT_RETRY)")
    parser.add_argument(
        "--wait-prepare",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="SENTENCE 일 때 같은 post 의 PREPARE(QUEUED·RUNNING)가 끝날 때까지 기다린다(10 §3).",
    )
    parser.add_argument(
        "--prepare-wait-seconds",
        type=float,
        default=PREPARE_WAIT_SECONDS,
        help="PREPARE 대기 상한(초). 넘으면 대기를 풀고 넣는다.",
    )
    parser.add_argument("--url", help="접속 문자열. 없으면 DATABASE_URL 을 쓴다.")
    return parser


def pick_route(parser: argparse.ArgumentParser, args: argparse.Namespace) -> JobRoute:
    if args.event is None:
        return DEFAULT_ROUTE_BY_KIND[args.kind]
    route = JOB_ROUTES[args.event]
    if route.kind != args.kind:
        parser.error(f"--event {args.event} 는 kind {route.kind} 다. --kind 와 맞지 않는다.")
    return route


def build_payload(
    parser: argparse.ArgumentParser, args: argparse.Namespace, route: JobRoute
) -> dict[str, Any]:
    """`contracts/jobs.py` 의 4형으로 만든다(`extra="forbid"`)."""
    if args.kind == "PREPARE":
        if not args.post or args.audience is None:
            parser.error("PREPARE 는 --post 와 --audience 가 필요하다.")
        payload = PreparePayload(
            post_id=args.post, post_version=args.version, audience_version=args.audience
        )
    elif args.kind == "SENTENCE":
        if not args.verdict or not args.post:
            parser.error("SENTENCE 는 --verdict 와 --post 가 필요하다.")
        payload = SentencePayload(
            verdict_id=args.verdict, verdict_version=args.version, post_id=args.post
        )
    elif args.kind == "TEXT_RETRY":
        if not args.verdict or args.round is None:
            parser.error("TEXT_RETRY 는 --verdict 와 --round 가 필요하다.")
        payload = TextRetryPayload(
            verdict_id=args.verdict, verdict_version=args.version, round=args.round
        )
    else:  # RETAIN
        if route.event_type == "sentence.finalized" and not args.verdict:
            parser.error("RETAIN sentence.finalized 는 --verdict 가 필요하다.")
        if route.event_type == "comment.approved" and not args.comment:
            parser.error("RETAIN comment.approved 는 --comment 가 필요하다.")
        payload = RetainPayload(
            event=route.event_type,  # type: ignore[arg-type]
            verdict_id=args.verdict,
            comment_id=args.comment,
            version=args.version,
        )
    return payload.model_dump()


async def wait_for_prepare(
    prepare_active: Callable[[], Awaitable[bool]],
    *,
    max_wait_s: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    poll_s: float = PREPARE_POLL_SECONDS,
) -> bool:
    """PREPARE 가 끝날 때까지 기다린다. 없거나 끝났으면 True, 대기 상한에 닿았으면 False."""
    started = clock()
    while await prepare_active():
        elapsed = clock() - started
        if elapsed >= max_wait_s:
            return False
        await sleep(min(poll_s, max_wait_s - elapsed))
    return True


async def prepare_active(engine: AsyncEngine, post_id: str) -> bool:
    """같은 post 의 PREPARE 가 `QUEUED`·`RUNNING` 인가."""
    async with engine.connect() as conn:
        result = await conn.execute(text(_PREPARE_ACTIVE_SQL), {"post_id": post_id})
        return bool(result.scalar_one())


async def _insert(engine: AsyncEngine, route: JobRoute, payload: dict[str, Any]) -> str | None:
    id_field, version_field = route.aggregate_fields
    deadline = (
        "NULL"
        if route.deadline_after_s is None
        else "now() + make_interval(secs => :deadline_after_s)"
    )
    params: dict[str, Any] = {
        "id": str(uuid4()),
        "event_id": str(uuid4()),
        "event_type": route.event_type,
        "kind": route.kind,
        "dedupe_key": build_dedupe_key(route, payload),
        "aggregate_id": str(payload[id_field]),
        "aggregate_version": int(payload[version_field]),
        "payload": json.dumps(payload, ensure_ascii=False),
        "priority": route.priority,
        "max_attempts": route.max_attempts,
        "trace_id": str(uuid4()),
    }
    if route.deadline_after_s is not None:
        params["deadline_after_s"] = route.deadline_after_s
    async with engine.begin() as conn:
        result = await conn.execute(text(_INSERT_TEMPLATE.format(deadline=deadline)), params)
        row = result.first()
    return str(row[0]) if row is not None else None


async def insert_job(url: str, route: JobRoute, payload: dict[str, Any]) -> str | None:
    """넣었으면 job id, dedupe_key 가 이미 있으면 `None`."""
    engine = make_engine(url)
    try:
        return await _insert(engine, route, payload)
    finally:
        await engine.dispose()


async def enqueue(
    url: str,
    route: JobRoute,
    payload: dict[str, Any],
    *,
    wait_prepare: bool,
    max_wait_s: float,
) -> tuple[str | None, bool | None]:
    """(job id, PREPARE 대기 결과). SENTENCE ∧ `wait_prepare` 가 아니면 두 번째는 None."""
    engine = make_engine(url)
    try:
        waited: bool | None = None
        if route.kind == "SENTENCE" and wait_prepare:
            post_id = str(payload["post_id"])
            waited = await wait_for_prepare(
                lambda: prepare_active(engine, post_id), max_wait_s=max_wait_s
            )
        return await _insert(engine, route, payload), waited
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    route = pick_route(parser, args)
    payload = build_payload(parser, args, route)

    url = args.url or secret_value(get_settings(), "DATABASE_URL")
    if not url.strip():
        # 접속 문자열은 비밀값이다. 이름만 말한다.
        print("DATABASE_URL 이 비어 있다. --url 로 주거나 .env 에 채운다.", file=sys.stderr)
        return 2

    job_id, waited = asyncio.run(
        enqueue(
            url,
            route,
            payload,
            wait_prepare=args.wait_prepare,
            max_wait_s=args.prepare_wait_seconds,
        )
    )
    if waited is False:
        print(f"PREPARE 가 {args.prepare_wait_seconds:g}초 안에 끝나지 않아 대기를 풀고 넣는다.")
    dedupe_key = build_dedupe_key(route, payload)
    if job_id is None:
        print(f"이미 있다(dedupe_key={dedupe_key}). 넣지 않았다.")
        return 0
    print(f"넣었다: id={job_id} kind={route.kind} event={route.event_type} dedupe={dedupe_key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
