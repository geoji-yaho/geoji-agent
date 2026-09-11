"""헬스 엔드포인트(01 §3.1).

- `/health/live` 는 프로세스가 살아 있는지만 본다. 늘 200 이다.
- `/health/ready` 는 일을 받을 수 있는지 본다. 벤더 키와 DB 를 본다(DB 는 작업 2 에서 붙였다).

`missing_keys()` 는 벤더 키만 안다(`core/startup.py`). `DATABASE_URL` 은 여기서
그 결과 뒤에 덧붙인다 — 벤더 키가 앞, DB 가 뒤다.

워커 프로세스에는 이 엔드포인트가 없다.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from geoji_ai.core.config import Settings, get_settings, secret_value
from geoji_ai.core.startup import missing_keys

router = APIRouter(prefix="/health", tags=["health"])

#: DB 검사 기본 타임아웃(초).
DB_CHECK_TIMEOUT_SECONDS = 2.0


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


async def check_db_reachable(url: str, timeout_s: float = DB_CHECK_TIMEOUT_SECONDS) -> bool:
    """`SELECT 1`. `timeout_s` 안에 못 하면 `False`.

    접속 문자열은 어디에도 싣지 않는다. 예외 내용도 응답에 넣지 않는다(키·호스트가 샌다).
    어댑터는 이 함수 안에서만 import 한다 — API 모듈을 불러오는 것만으로 DB 드라이버를
    끌어오지 않게, 그리고 단위 테스트가 이 이름을 monkeypatch 할 수 있게.
    """
    from sqlalchemy import text

    from geoji_ai.adapters.postgres_jobs import make_engine

    engine = make_engine(url)
    try:
        async with asyncio.timeout(timeout_s):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        with contextlib.suppress(Exception):
            await engine.dispose()


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> Any:
    settings = _settings(request)
    missing = missing_keys(settings)
    database_url = secret_value(settings, "DATABASE_URL").strip()
    if not database_url:
        missing = [*missing, "DATABASE_URL"]
    if missing:
        # 키 이름만 돌려준다. 값은 어디에도 싣지 않는다.
        return JSONResponse(status_code=503, content={"status": "not_ready", "missing": missing})
    if not await check_db_reachable(database_url):
        return JSONResponse(status_code=503, content={"status": "not_ready", "db": "unreachable"})
    return {"status": "ok", "missing": []}
