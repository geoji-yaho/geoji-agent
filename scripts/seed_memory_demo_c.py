"""데모 C 메모리 시드(04 §3.6, ME-07).

같은 사용자의 지난 스타벅스 소비 2건을 `ai.memory_facts` 에 넣는다. 3번째 스타벅스 사건에서
`recall_user` → 2건, `build_evidence` → PRIOR 2 + AGGREGATE 반복 "2건" 이 나오게 하는 것이 목적이다.

    uv run scripts/seed_memory_demo_c.py --user U --room R --post-ids P1,P2 \
        [--verdict-ids V1,V2] [--now ISO8601] [--url URL]

- 사용자 `user/{U}` SPEND+VERDICT 2건(스타벅스 6,100원, `--now` 기준 −10일·−4일, 둘 다 `guilty`,
  guilty_ratio 0.8·1.0), 방 `room/{R}` VERDICT 2건. scope `ROOMS [R]`
- `source_id` 는 받은 post_id 다. 실제 값은 백엔드 시드가 만든다(10 §12)
- 행은 직접 SQL 이 아니라 `build_verdict_payload` + `PostgresMemory.retain_verdict` 로 넣는다.
  행 규칙은 `application.retain_memory` 한 곳에 있다
- event_id 는 `uuid5(NAMESPACE_URL, "demo-c:{post_id}")` 라 두 번 돌려도 행이 늘지 않는다
- `ai.privacy_epochs` 가 있으면 지금 epoch 를 읽어 스냅샷에 넣는다(없는 scope 는 0).
  테이블이 없으면(004 전 로컬 DB) epoch 검사를 하지 않는다

접속은 `--url` 또는 `DATABASE_URL`(`.env` 포함)이다. 접속 문자열은 출력하지 않는다.
프로젝트 venv 에서 돈다(`uv run`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.types import Text

from geoji_ai.adapters.postgres_jobs import make_engine
from geoji_ai.adapters.postgres_memory import EPOCHS_SQL, PostgresMemory
from geoji_ai.application.retain_memory import build_verdict_payload
from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.core.config import Settings, get_settings, secret_value

__all__ = [
    "DEMO_AMOUNT_KRW",
    "DEMO_CATEGORY",
    "DEMO_ITEM",
    "DEMO_SEEDS",
    "build_snapshot",
    "current_privacy_versions",
    "demo_event_id",
    "demo_verdict_id",
    "main",
    "seed",
]

# --- 04 §3.6 값 ------------------------------------------------------------------

DEMO_ITEM = "스타벅스"
DEMO_CATEGORY = "카페/간식"
DEMO_AMOUNT_KRW = 6_100
#: (`--now` 에서 뺄 기간, guilty_ratio). post_ids 순서와 짝이다.
DEMO_SEEDS: tuple[tuple[timedelta, float], ...] = (
    (timedelta(days=10), 0.8),
    (timedelta(days=4), 1.0),
)
DEMO_RESULT = "guilty"

#: 계획서에 없는 판결 정책 필드는 기존 fixture 값을 그대로 쓴다(새로 만들지 않는다).
_JURY_TEMPLATE = (
    Path(__file__).resolve().parents[1] / "contracts" / "fixtures" / "jury-guilty-75.json"
)


def demo_event_id(post_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"demo-c:{post_id}"))


def demo_verdict_id(post_id: str) -> str:
    """VERDICT 사실의 source_id. `--verdict-ids` 가 없을 때 post_id 에서 결정적으로 만든다."""
    return str(uuid5(NAMESPACE_URL, f"demo-c:verdict:{post_id}"))


def build_snapshot(
    *,
    user: str,
    room: str,
    post_id: str,
    created_at: datetime,
    guilty_ratio: float,
    privacy_versions: list[dict[str, Any]],
    verdict_id: str | None = None,
) -> CaseSnapshot:
    """RETAIN 확장 필드가 든 확정 사건 스냅샷 하나."""
    template = json.loads(_JURY_TEMPLATE.read_text(encoding="utf-8"))
    confirmed = created_at.isoformat()
    data: dict[str, Any] = {
        "schema_version": 1,
        "post_id": post_id,
        "author_id": user,
        "post_version": 1,
        "item": DEMO_ITEM,
        "reason": None,
        "amount_krw": DEMO_AMOUNT_KRW,
        "category": DEMO_CATEGORY,
        "post_type": "spent",
        "created_at": confirmed,
        "audience": {"room_ids": [room], "audience_version": 1, "public_share_enabled": False},
        "privacy_versions": privacy_versions,
        "room_snapshots": [],
        "intake_result": None,
        "jury": {
            "verdict_id": verdict_id or demo_verdict_id(post_id),
            "verdict_version": 1,
            "result": DEMO_RESULT,
            "vote_counts": {},
            "guilty_ratio": guilty_ratio,
            "confirmed_at": confirmed,
            "deadline_at": confirmed,
            "policy": template["policy"],
            "target_intensities": template["target_intensities"],
            "default_intensity": template["default_intensity"],
        },
        "verdict_final": {
            "sentence": template["policy"]["fallback_sentence"],
            "sentence_source": "RULE",
            "sentencing_reason": None,
            "reason_source": "TEMPLATE",
            "applied_intensity": template["default_intensity"],
            "banter_strategy": None,
        },
    }
    return CaseSnapshot.model_validate(data)


async def current_privacy_versions(engine: AsyncEngine, keys: list[str]) -> list[dict[str, Any]]:
    """지금 epoch. `ai.privacy_epochs` 가 없으면 빈 목록(검사하지 않음)."""
    async with engine.connect() as conn:
        exists = (
            await conn.execute(text("SELECT to_regclass('ai.privacy_epochs') IS NOT NULL"))
        ).scalar_one()
        if not exists:
            return []
        statement = text(EPOCHS_SQL).bindparams(bindparam("keys", type_=ARRAY(Text)))
        rows = await conn.execute(statement, {"keys": keys})
        current = {row[0]: int(row[1]) for row in rows}
    return [{"scope_key": key, "epoch": current.get(key, 0)} for key in keys]


async def seed(
    engine: AsyncEngine,
    settings: Settings,
    *,
    user: str,
    room: str,
    post_ids: list[str],
    now: datetime,
    verdict_ids: list[str] | None = None,
) -> list[tuple[str, int]]:
    """post 마다 `(post_id, 새로 넣은 행 수)`. 이미 넣은 post 는 0.

    `verdict_ids` 는 백엔드 시드의 실제 verdict_id(post_ids 와 같은 순서). 없으면 uuid5.
    """
    if len(post_ids) != len(DEMO_SEEDS):
        raise ValueError(f"post_id 는 {len(DEMO_SEEDS)}개여야 한다: {len(post_ids)}")
    if verdict_ids is not None and len(verdict_ids) != len(post_ids):
        raise ValueError(f"verdict_id 는 post_id 와 같은 수여야 한다: {len(verdict_ids)}")
    memory = PostgresMemory(engine, settings)
    privacy_versions = await current_privacy_versions(engine, [f"user:{user}", f"room:{room}"])
    written: list[tuple[str, int]] = []
    pairs = zip(post_ids, verdict_ids or [None] * len(post_ids), DEMO_SEEDS, strict=True)
    for post_id, verdict_id, (ago, ratio) in pairs:
        snapshot = build_snapshot(
            user=user,
            room=room,
            post_id=post_id,
            created_at=now - ago,
            guilty_ratio=ratio,
            privacy_versions=privacy_versions,
            verdict_id=verdict_id,
        )
        payload = build_verdict_payload(snapshot)
        assert payload is not None  # jury·verdict_final 을 채웠다
        rows = await memory.retain_verdict(demo_event_id(post_id), payload)
        written.append((post_id, rows))
    return written


def _parse_now(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--now 에 시간대가 있어야 한다(예: 2026-09-14T12:00:00Z)")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="데모 C 메모리 시드(04 §3.6).")
    parser.add_argument("--user", required=True, help="author_id(user 뱅크)")
    parser.add_argument("--room", required=True, help="room_id(room 뱅크, scope)")
    parser.add_argument("--post-ids", required=True, help="백엔드 시드 post_id 2개. P1,P2")
    parser.add_argument(
        "--verdict-ids",
        help="백엔드 시드 verdict_id 2개. V1,V2(post-ids 순서). 없으면 post_id 에서 uuid5",
    )
    parser.add_argument("--now", type=_parse_now, help="기준 시각 ISO8601. 없으면 지금")
    parser.add_argument("--url", help="접속 문자열. 없으면 DATABASE_URL 을 쓴다.")
    return parser


async def _run(
    url: str,
    settings: Settings,
    args: argparse.Namespace,
    post_ids: list[str],
    verdict_ids: list[str] | None,
):
    engine = make_engine(url)
    try:
        return await seed(
            engine,
            settings,
            user=args.user,
            room=args.room,
            post_ids=post_ids,
            now=args.now or datetime.now(UTC),
            verdict_ids=verdict_ids,
        )
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    post_ids = [p.strip() for p in args.post_ids.split(",") if p.strip()]
    if len(post_ids) != len(DEMO_SEEDS):
        parser.error(f"--post-ids 는 {len(DEMO_SEEDS)}개다: {len(post_ids)}개를 받았다.")
    verdict_ids = None
    if args.verdict_ids:
        verdict_ids = [v.strip() for v in args.verdict_ids.split(",") if v.strip()]
        if len(verdict_ids) != len(post_ids):
            parser.error(f"--verdict-ids 는 {len(post_ids)}개다: {len(verdict_ids)}개를 받았다.")

    settings = get_settings()
    url = args.url or secret_value(settings, "DATABASE_URL")
    if not url.strip():
        # 접속 문자열은 비밀값이다. 이름만 말한다.
        print("DATABASE_URL 이 비어 있다. --url 로 주거나 .env 에 채운다.", file=sys.stderr)
        return 2

    for post_id, rows in asyncio.run(_run(url, settings, args, post_ids, verdict_ids)):
        state = "넣었다" if rows else "이미 있다(또는 epoch 불일치). 넣지 않았다"
        print(f"{post_id}: {state} rows={rows} event_id={demo_event_id(post_id)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
