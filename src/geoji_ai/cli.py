"""명령줄 진입점(02 §3.6·§4.3).

```
uv run geoji-ai migrate          # database/migrations 의 001~003 적용
uv run geoji-ai worker --reaper  # 큐 워커. --reaper 는 로컬 전용
```

접속 문자열은 비밀값이라 어디에도 출력하지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from geoji_ai.adapters.postgres_jobs import make_engine
from geoji_ai.adapters.postgres_migrations import migrate
from geoji_ai.core.config import Settings, get_settings, secret_value
from geoji_ai.core.logging import configure_logging

__all__ = ["main"]

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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging()
    settings = get_settings()
    if args.command == "migrate":
        return _run_migrate(settings)
    return _run_worker(settings, reaper=args.reaper)


if __name__ == "__main__":  # pragma: no cover - 진입점
    raise SystemExit(main())
