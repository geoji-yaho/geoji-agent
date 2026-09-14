"""FastAPI 앱(01 §3.1).

`uv run uvicorn geoji_ai.api.app:app --port 8100` 으로 뜬다.
기동 검사는 앱을 만들 때 한 번 돈다. 실패하면 `StartupError` 로 프로세스가 죽는다.

심문관 LLM(`app.state.intake_llm`, 07 §3.4):

- `build_llm(settings)` 이 None(키 둘 다 없음)이면 None → intake 는 늘 폴백. 기동은 성공한다
- `DATABASE_URL` 이 있으면 제출 전용 원장(`PostgresCallLedger(cap=1,034 micro-USD)`)
  + `VendorHealth` + `LLMGateway`. 없으면 라우터를 그대로(원장 없음)
- 엔진 생성은 lazy 라 import 시점에 DB 에 접속하지 않는다. 종료 때 lifespan 이 dispose 한다
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from geoji_ai.adapters.llm_router import build_llm, price_for
from geoji_ai.adapters.postgres_call_ledger import PostgresCallLedger
from geoji_ai.adapters.postgres_jobs import make_engine
from geoji_ai.api import errors, health, intake_routes, telemetry_routes
from geoji_ai.application.llm_gateway import LLMGateway
from geoji_ai.core.config import Settings, get_settings, secret_value
from geoji_ai.core.logging import configure_logging
from geoji_ai.core.startup import validate
from geoji_ai.domain.vendor_health import VendorHealth

__all__ = ["SUBMISSION_CAP_MICRO_USD", "app", "create_app"]

#: 제출 단위 임시 예산 cap(07 §3.4, 심문 2회분 ≈ 1.5원).
SUBMISSION_CAP_MICRO_USD = 1034

_UNSET: Any = object()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        engine = getattr(app.state, "intake_engine", None)
        if engine is not None:
            await engine.dispose()


def _build_intake_llm(settings: Settings, app: FastAPI) -> Any:
    router = build_llm(settings)
    url = secret_value(settings, "DATABASE_URL")
    if router is None or not url:
        return router
    engine = make_engine(url)
    app.state.intake_engine = engine
    ledger = PostgresCallLedger(engine, cap_micro_usd=SUBMISSION_CAP_MICRO_USD)
    return LLMGateway(router, ledger, VendorHealth(), price_for)


def create_app(settings: Settings | None = None, *, intake_llm: Any = _UNSET) -> FastAPI:
    settings = settings if settings is not None else get_settings()
    configure_logging()
    validate(settings)

    app = FastAPI(title="geoji-ai", version="0.1.0", lifespan=_lifespan)
    app.state.settings = settings
    app.state.intake_engine = None
    app.state.intake_llm = _build_intake_llm(settings, app) if intake_llm is _UNSET else intake_llm
    errors.install(app)
    app.include_router(health.router)
    app.include_router(intake_routes.router)
    app.include_router(telemetry_routes.router)
    return app


app = create_app()
