"""FastAPI 앱(01 §3.1).

`uv run uvicorn geoji_ai.api.app:app --port 8100` 으로 뜬다.
기동 검사는 앱을 만들 때 한 번 돈다. 실패하면 `StartupError` 로 프로세스가 죽는다.
"""

from __future__ import annotations

from fastapi import FastAPI

from geoji_ai.api import health
from geoji_ai.core.config import Settings, get_settings
from geoji_ai.core.logging import configure_logging
from geoji_ai.core.startup import validate

__all__ = ["app", "create_app"]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else get_settings()
    configure_logging()
    validate(settings)

    app = FastAPI(title="geoji-ai", version="0.1.0")
    app.state.settings = settings
    app.include_router(health.router)
    return app


app = create_app()
