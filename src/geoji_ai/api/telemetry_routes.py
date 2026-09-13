"""관측 엔드포인트(08 §3.3, 10 §4.5 제안). 둘 다 서비스 인증.

- `GET /internal/v1/metrics/snapshot` — 이 프로세스 레지스트리(`source: process`)
  + DB 지표(`source: db`).
  DB 에 닿지 못하면 프로세스 지표만 내고 `db: "unreachable"` 을 단다.
- `GET /internal/v1/trials/{post_id}/trace` — 최신 dossier 라벨·fact_type·scope, 출처 개수,
  노드 타임라인, 비용. 원문·개별 id 없음. 기록이 없으면 404 `TRACE_NOT_FOUND`.

엔진은 `app.state.telemetry_engine` 이 있으면 그것을 쓰고(테스트 주입), 없으면 `DATABASE_URL` 로
처음 요청 때 만들어 거기에 캐시한다. 레지스트리는 `app.state.metrics_registry` 가 있으면 그것,
없으면 `telemetry.metrics.REGISTRY`. 어댑터는 함수 안에서 import 한다(health.py 와 같은 이유).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from geoji_ai.api.auth import require_service_token
from geoji_ai.core.config import get_settings, secret_value
from geoji_ai.core.logging import get_logger
from geoji_ai.telemetry.metrics import REGISTRY, MetricsRegistry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

__all__ = ["router"]

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    dependencies=[Depends(require_service_token)],
)


def _registry(request: Request) -> MetricsRegistry:
    return getattr(request.app.state, "metrics_registry", None) or REGISTRY


def _engine(request: Request) -> AsyncEngine:
    engine = getattr(request.app.state, "telemetry_engine", None)
    if engine is not None:
        return engine
    settings = getattr(request.app.state, "settings", None) or get_settings()
    url = secret_value(settings, "DATABASE_URL").strip()
    if not url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "DB_UNAVAILABLE"}
        )
    from geoji_ai.adapters.postgres_jobs import make_engine

    engine = make_engine(url)
    request.app.state.telemetry_engine = engine
    return engine


@router.get("/metrics/snapshot")
async def metrics_snapshot(request: Request) -> dict[str, Any]:
    from geoji_ai.adapters.postgres_telemetry import PostgresTelemetry

    metrics = list(_registry(request).snapshot()["metrics"])
    db_state = "ok"
    try:
        metrics.extend(await PostgresTelemetry(_engine(request)).snapshot_series())
    except HTTPException:
        db_state = "unreachable"
    except Exception as exc:
        # 접속 문자열이 예외 문자열에 섞일 수 있어 타입 이름만 남긴다.
        get_logger("geoji_ai.telemetry").warning(
            "metrics_snapshot_db_failed", error_type=type(exc).__name__
        )
        db_state = "unreachable"
    return {"generated_at": datetime.now(UTC).isoformat(), "db": db_state, "metrics": metrics}


@router.get("/trials/{post_id}/trace")
async def trial_trace(post_id: str, request: Request) -> dict[str, Any]:
    from geoji_ai.adapters.postgres_telemetry import PostgresTelemetry

    trace = await PostgresTelemetry(_engine(request)).trace(post_id)
    if trace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"code": "TRACE_NOT_FOUND"}
        )
    return trace
