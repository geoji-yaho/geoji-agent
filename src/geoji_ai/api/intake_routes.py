"""심문관 intake 라우트(07 §3.1, 10 §4).

`POST /internal/v1/intake` 는 그래프 A(`graphs.intake.run_intake`)를 부른다. LLM(게이트웨이 또는
라우터, 없으면 None)과 설정은 `app.state` 에서 읽는다. 코드 규칙의 필수값 위반은 422 이고
detail 에는 코드만 넣는다(원문 금지).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from geoji_ai.api.auth import require_service_token
from geoji_ai.contracts.intake import IntakeRequest, IntakeResult
from geoji_ai.core.config import get_settings
from geoji_ai.domain.intake_rules import IntakeRuleError
from geoji_ai.graphs.intake import run_intake

__all__ = ["router"]

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    dependencies=[Depends(require_service_token)],
)


@router.post("/intake", response_model=IntakeResult)
async def intake(req: IntakeRequest, request: Request) -> IntakeResult:
    state = request.app.state
    settings = getattr(state, "settings", None) or get_settings()
    try:
        return await run_intake(req, llm=getattr(state, "intake_llm", None), settings=settings)
    except IntakeRuleError as err:
        raise HTTPException(status_code=422, detail={"code": err.code}) from None
