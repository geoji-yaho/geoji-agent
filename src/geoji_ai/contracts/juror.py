"""배심원(떼거지봇) 모델 출력 계약(18 §3.2).

JSON Schema 정본은 만들지 않는다 — 백엔드가 보는 모양은 18 §3.6(19 §5) 요청 본문이고
이 모델은 **모델 출력 전용**이다(`contracts/jobs.py` 와 같은 지위). 동등성 테스트 대상이 아니다.

`verdict` 4종은 프론트 정본(`geoji-web/src/shared/domain/verdict.ts`)의 투표 가능 값이다.
게시물 유형별 허용 2개는 `llm_schemas.juror_schema(post_type)` 가 enum 으로 주입하고, 실제
검증은 `graphs/jury_vote.py` 가 한다(xAI strict 는 enum·maxLength 를 강제하지 않는다).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["JUROR_REASON_MAX", "JurorVerdict", "JurorVote"]

#: 사유 상한(18 §1 결정 10, 사람 표처럼 1~2문장 60자 이내).
JUROR_REASON_MAX = 60

JurorVerdict = Literal["guilty", "notGuilty", "agree", "disagree"]


class JurorVote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: JurorVerdict
    reason: Annotated[str, Field(min_length=1, max_length=JUROR_REASON_MAX)]
