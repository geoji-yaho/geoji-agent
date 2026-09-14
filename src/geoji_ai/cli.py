"""명령줄 진입점(02 §3.6·§4.3).

```
uv run geoji-ai migrate                       # database/migrations 의 001~003 적용
uv run geoji-ai worker --reaper               # 큐 워커. --reaper 는 로컬 전용
uv run geoji-ai ledger-sweep --older-than 24h # UNKNOWN 호출 예약액을 spent 로 확정(08 §3.2)
```

접속 문자열은 비밀값이라 어디에도 출력하지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Sequence
from datetime import timedelta

from geoji_ai.adapters.postgres_call_ledger import PostgresCallLedger
from geoji_ai.adapters.postgres_jobs import make_engine
from geoji_ai.adapters.postgres_migrations import migrate
from geoji_ai.core.config import Settings, get_settings, secret_value
from geoji_ai.core.logging import configure_logging
from geoji_ai.ports.ledger import SweepReport

__all__ = ["main", "parse_duration"]

_DURATION = re.compile(r"([1-9][0-9]*)([hm])")
_DURATION_UNITS = {"h": "hours", "m": "minutes"}


def parse_duration(value: str) -> timedelta:
    """`24h`·`30m` → timedelta. 양의 정수 + `h`(시간)·`m`(분)만 받는다."""
    match = _DURATION.fullmatch(value.strip())
    if match is None:
        raise argparse.ArgumentTypeError(
            f"기간 형식이 아니다: {value!r}. 양의 정수 뒤에 h 또는 m(예: 24h, 30m)"
        )
    amount, unit = match.groups()
    return timedelta(**{_DURATION_UNITS[unit]: int(amount)})


_NO_DATABASE_URL = "DATABASE_URL 이 비어 있다. .env 또는 환경변수에 넣는다."


def _database_url(settings: Settings) -> str:
    """접속 문자열 원문. 돌려받은 값을 로그·표준출력에 넣지 않는다."""
    return secret_value(settings, "DATABASE_URL").strip()


async def _apply_migrations(url: str) -> list[int]:
    engine = make_engine(url)
    try:
        return await migrate(engine)
    finally:
        await engine.dispose()


def _run_migrate(settings: Settings) -> int:
    url = _database_url(settings)
    if not url:
        print(_NO_DATABASE_URL, file=sys.stderr)
        return 2
    applied = asyncio.run(_apply_migrations(url))
    if applied:
        print("적용한 마이그레이션: " + ", ".join(f"{version:03d}" for version in applied))
    else:
        print("새로 적용한 마이그레이션이 없다.")
    return 0


def _run_worker(settings: Settings, *, reaper: bool) -> int:
    # 워커 런타임은 여기서만 부른다. `migrate` 가 워커 쪽 import 에 묶이지 않게 한다.
    from geoji_ai.workers.main import run_worker

    if not _database_url(settings):
        print(_NO_DATABASE_URL, file=sys.stderr)
        return 2
    asyncio.run(run_worker(settings, reaper=reaper))
    return 0


async def _sweep(url: str, older_than: timedelta) -> SweepReport:
    engine = make_engine(url)
    try:
        return await PostgresCallLedger(engine).sweep_unknown(older_than)
    finally:
        await engine.dispose()


def _run_ledger_sweep(settings: Settings, *, older_than: timedelta) -> int:
    url = _database_url(settings)
    if not url:
        print(_NO_DATABASE_URL, file=sys.stderr)
        return 2
    report = asyncio.run(_sweep(url, older_than))
    # 건수·금액·키 개수만. 예산 키 원문·접속 문자열은 출력하지 않는다.
    print(
        f"UNKNOWN 정리: 호출 {report.calls}건, {report.micro_usd} micro-USD 를 spent 로 확정, "
        f"예산 키 {report.budget_keys}개 (그중 오래된 RESERVED {report.reserved_calls}건)"
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="geoji-ai", description="떼거지 AI 파트 명령줄")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate", help="database/migrations 의 001~003 을 번호 순으로 적용한다")
    worker = subparsers.add_parser("worker", help="큐 워커를 띄운다")
    worker.add_argument(
        "--reaper",
        action="store_true",
        help="lease 만료 회수를 같은 프로세스에서 돈다(개발·로컬 전용)",
    )
    sweep = subparsers.add_parser(
        "ledger-sweep",
        help="오래된 UNKNOWN 호출의 예약액을 spent 로 확정하고 리포트한다(08 §3.2)",
    )
    sweep.add_argument(
        "--older-than",
        required=True,
        type=parse_duration,
        metavar="DURATION",
        help="이보다 오래 전에 시작한 UNKNOWN 호출만 정리한다. 양의 정수 + h|m (예: 24h)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging()
    settings = get_settings()
    if args.command == "migrate":
        return _run_migrate(settings)
    if args.command == "ledger-sweep":
        return _run_ledger_sweep(settings, older_than=args.older_than)
    return _run_worker(settings, reaper=args.reaper)


if __name__ == "__main__":  # pragma: no cover - 진입점
    raise SystemExit(main())
