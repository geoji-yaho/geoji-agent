"""헬스 엔드포인트(01 §3.1).

- `/health/live` 는 프로세스가 살아 있는지만 본다. 늘 200 이다.
- `/health/ready` 는 일을 받을 수 있는지 본다. 지금은 벤더 키만 본다.
  DB 검사는 작업 2 에서 붙인다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from geoji_ai.core.config import Settings, get_settings
from geoji_ai.core.startup import missing_keys

router = APIRouter(prefix="/health", tags=["health"])


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> Any:
    missing = missing_keys(_settings(request))
    if missing:
        # 키 이름만 돌려준다. 값은 어디에도 싣지 않는다.
        return JSONResponse(status_code=503, content={"status": "not_ready", "missing": missing})
    return {"status": "ok", "missing": []}
