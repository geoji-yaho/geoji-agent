"""마이그레이션 러너(02 §3.6).

`database/migrations/NNN_*.sql` 을 번호 순으로 적용하고 `ai.schema_migrations` 에 기록한다.
이미 적용된 번호는 건너뛴다(재적용 no-op). 파일 하나가 한 트랜잭션이고, 같은 트랜잭션 안에서
적용 기록을 남긴다.

004 는 백엔드 소유라 **우리 러너는 001~003 만** 적용한다(02 §3.6, 00 §8.1).
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

__all__ = ["MAX_OWNED_VERSION", "MIGRATIONS_DIR", "migrate"]

#: `<저장소 루트>/database/migrations`.
MIGRATIONS_DIR: Path = Path(__file__).resolve().parents[3] / "database" / "migrations"

#: 우리가 적용하는 마지막 번호. 004 부터는 백엔드 소유라 읽지도 적용하지도 않는다.
MAX_OWNED_VERSION = 3

_FILENAME_RE = re.compile(r"^(\d{3})_.+\.sql$")

#: 마이그레이션 직렬화용 advisory lock 키. 이 저장소 안에서만 쓰는 임의의 고정값이다.
_LOCK_KEY = 4_602_001

# 러너가 먼저 만드는 것. 001 의 첫 줄도 `CREATE SCHEMA IF NOT EXISTS ai` 이지만,
# 적용 기록 테이블이 `ai` 스키마 안에 있으므로 스키마를 러너가 먼저 만들어야 한다.
_BOOTSTRAP_SQL: tuple[str, ...] = (
    "CREATE SCHEMA IF NOT EXISTS ai",
    "CREATE TABLE IF NOT EXISTS ai.schema_migrations ("
    "version integer PRIMARY KEY, "
    "applied_at timestamptz NOT NULL DEFAULT now())",
)


def _discover(migrations_dir: Path, max_version: int) -> list[tuple[int, Path]]:
    """`NNN_*.sql` 을 번호 순으로. `max_version` 초과는 목록에 넣지 않는다."""
    found: dict[int, Path] = {}
    for path in sorted(migrations_dir.glob("*.sql")):
        matched = _FILENAME_RE.match(path.name)
        if matched is None:
            continue
        version = int(matched.group(1))
        if version > max_version:
            continue
        if version in found:
            raise ValueError(f"마이그레이션 번호가 겹친다: {version}")
        found[version] = path
    return sorted(found.items())


async def _run_script(conn: AsyncConnection, sql: str) -> None:
    """문장 여러 개가 든 스크립트를 통째로 실행한다.

    asyncpg 는 prepared statement 에 여러 문장을 넣지 못해서 드라이버 연결의
    simple query 경로를 쓴다. 호출자가 연 트랜잭션 안에서 돈다.
    """
    raw_connection = await conn.get_raw_connection()
    await raw_connection.driver_connection.execute(sql)


async def migrate(
    engine: AsyncEngine,
    *,
    migrations_dir: Path | None = None,
    max_version: int = MAX_OWNED_VERSION,
) -> list[int]:
    """번호 순으로 적용하고 이번에 새로 적용한 번호 목록을 돌려준다."""
    directory = MIGRATIONS_DIR if migrations_dir is None else migrations_dir
    pending = _discover(directory, max_version)

    async with engine.begin() as conn:
        for statement in _BOOTSTRAP_SQL:
            await conn.execute(text(statement))

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT version FROM ai.schema_migrations"))
        already_applied = {int(row[0]) for row in result}

    newly_applied: list[int] = []
    for version, path in pending:
        if version in already_applied:
            continue
        sql = path.read_text(encoding="utf-8")
        async with engine.begin() as conn:
            # 두 프로세스가 같이 migrate 를 돌려도 한쪽만 적용한다. 이 러너는 배포에서
            # 백엔드가 Supabase 에 돌리는 것이라(README) 인스턴스 둘이 같이 뜰 수 있다.
            await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
            applied_now = await conn.execute(
                text("SELECT 1 FROM ai.schema_migrations WHERE version = :version"),
                {"version": version},
            )
            if applied_now.first() is not None:
                # 잠금을 기다리는 사이 다른 프로세스가 적용했다.
                continue
            await _run_script(conn, sql)
            await conn.execute(
                text("INSERT INTO ai.schema_migrations (version) VALUES (:version)"),
                {"version": version},
            )
        newly_applied.append(version)
    return newly_applied
