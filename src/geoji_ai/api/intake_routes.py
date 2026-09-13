"""심문관 intake M2 스텁(03 §3.4, 10 §4).

`POST /internal/v1/intake` 는 늘 `PASS`·`FALLBACK` 을 돌려준다. `mode=FINAL_CHECK` 도
`PASS` 다. 모델·DB 를 부르지 않는다. 작업 5 가 심문관 본체로 교체한다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from geoji_ai.api.auth import require_service_token
from geoji_ai.contracts.intake import IntakeRequest, IntakeResult

__all__ = ["router"]

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    dependencies=[Depends(require_service_token)],
)


@router.post("/intake", response_model=IntakeResult)
async def intake(req: IntakeRequest) -> IntakeResult:
    return IntakeResult.model_validate(
        {
            "schema_version": 1,
            "mode": req.mode,
            "status": "PASS",
            "item_review": {"status": "OK", "suggested_item": None},
            "message": None,
            "category_review": {"status": "OK", "suggested_category": None, "confidence": 1.0},
            "injection_detected": False,
            "intake_source": "FALLBACK",
        }
    )
