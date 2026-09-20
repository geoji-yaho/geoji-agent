"""노드 시간 예산(05 §3.1).

DB 가 준 `deadline_at` 과 같은 DB 가 준 `db_now` 의 차이만 믿는다. 호스트 시계(`datetime.now`)
는 쓰지 않고, 그 차이를 로컬 `time.monotonic` 기준 만료 시점으로 바꾼다(proposal2 §14.1).

노드 timeout = `min(노드 상한, 남은 시간 − 다음 필수 단계 예약)`. 결과가 0 이하면 그 노드를
**시작하지 않는다**(`None`). 검수 시간을 확보할 수 없으면 새 서기 호출을 시작하지 않는 규칙이
이 계산으로 선다.

돈 예산(06 §3.2)도 여기 둔다. 단위는 micro-USD 정수이고 KRW 는 화면·리포트에서만 쓴다.
단가표는 어댑터 쪽에 있어 domain 이 import 하지 않는다. 호출자가 단가를 넘긴다.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from geoji_ai.domain.draft_hash import _nfc

__all__ = [
    "CASE_CAP_MICRO_USD",
    "EVALUATOR",
    "FINALIZE_RESERVE_SECONDS",
    "JURY_BUDGET_PREFIX",
    "KRW_PER_USD",
    "NODE_NAMES",
    "NODE_RESULT_TTL",
    "SENTENCING",
    "SUBMISSION_BUDGET_PREFIX",
    "TICKS_PER_MICRO_USD",
    "WRITER",
    "Deadline",
    "NodeResultKey",
    "budget_key_for_jury",
    "budget_key_for_post",
    "budget_key_for_submission",
    "est_max_micro_usd",
    "inline_context_reserve",
    "node_cap",
    "node_result_expires_at",
    "request_hash",
    "reserve_after",
    "resolve_actual_micro_usd",
    "ticks_to_micro_usd",
]

SENTENCING = "sentencing"
WRITER = "writer"
EVALUATOR = "evaluator"
NODE_NAMES: tuple[str, ...] = (SENTENCING, WRITER, EVALUATOR)

#: finalize 에 남겨 두는 시간(05 §3.1).
FINALIZE_RESERVE_SECONDS = 0.5

_CAP_SETTING: dict[str, str] = {
    SENTENCING: "SENTENCING_NODE_TIMEOUT_SECONDS",
    WRITER: "WRITER_NODE_TIMEOUT_SECONDS",
    EVALUATOR: "EVALUATOR_NODE_TIMEOUT_SECONDS",
}


def _check_name(name: str) -> str:
    if name not in _CAP_SETTING:
        raise ValueError(f"예산을 모르는 노드: {name!r}")
    return name


def node_cap(name: str, settings: Any) -> float:
    """노드 상한(설정값)."""
    return float(getattr(settings, _CAP_SETTING[_check_name(name)]))


def reserve_after(name: str, settings: Any) -> float:
    """이 노드 뒤 필수 단계에 남겨 둘 시간.

    05 §3.1 원문이 정의한 두 가지만 예약한다.

    - writer → evaluator 상한(4s) + finalize 0.5s
    - evaluator → finalize 0.5s
    - sentencing → 0. 양형 timeout = `min(양형 상한, 남은 시간)`. 뒤 서기·검수 시간은 각 노드의
      `reserve_after` 가 확보하고, 남은 시간이 모자라면 뒤 노드가 줄어들거나 폴백한다
    """
    _check_name(name)
    if name == SENTENCING:
        return 0.0
    if name == EVALUATOR:
        return FINALIZE_RESERVE_SECONDS
    return node_cap(EVALUATOR, settings) + FINALIZE_RESERVE_SECONDS


def inline_context_reserve(settings: Any) -> float:
    """즉석 조서(`inline_context`) 전에 남겨 둘 시간 = 서기 상한 + 검수 상한 + finalize 0.5s.

    9/14 D-25(10 §15.5, 05 §3.1): 서기·검수·finalize 시간을 먼저 남기고 남는 시간만 조서에 쓴다.
    조서 timeout = `min(서기 상한, 남은 − 이 값)`. 0 이하면 조서를 시작하지 않는다
    (`minimal_dossier`).
    기본 상한(서기 6·검수 4)이면 10.5초라 10초 마감에서는 즉석 조서가 사실상 꺼진다.
    """
    return node_cap(WRITER, settings) + node_cap(EVALUATOR, settings) + FINALIZE_RESERVE_SECONDS


@dataclass(frozen=True)
class Deadline:
    """monotonic 기준 만료 시점. `clock` 은 테스트가 바꿔 끼운다."""

    expires_at_monotonic: float
    clock: Callable[[], float] = field(default=time.monotonic, compare=False, repr=False)

    @classmethod
    def from_db(
        cls,
        deadline_at: datetime,
        db_now: datetime,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> Deadline:
        """`deadline_at − db_now` 를 지금의 monotonic 에 더한다. 두 시각 모두 DB 가 준 값이다."""
        remaining = (deadline_at - db_now).total_seconds()
        return cls(expires_at_monotonic=clock() + remaining, clock=clock)

    def remaining_s(self) -> float:
        """남은 초. 지났으면 0."""
        return max(0.0, self.expires_at_monotonic - self.clock())

    def node_timeout(self, name: str, settings: Any) -> float | None:
        """`min(노드 상한, 남은 − reserve_after(name))`. 0 이하면 `None`(시작하지 않음)."""
        budget = min(node_cap(name, settings), self.remaining_s() - reserve_after(name, settings))
        if budget <= 0:
            return None
        return budget


# --- 돈 예산(06 §3.2) --------------------------------------------------------------

#: 환산 기준(06 §3.2 "1,450원/$"). 표시용이다.
KRW_PER_USD = 1450

#: 사건당 상한. 40원 상당 = `round(40/1450*1e6)` = 27,586 micro-USD.
CASE_CAP_MICRO_USD: int = round(40 / KRW_PER_USD * 1_000_000)

#: xAI `cost_in_usd_ticks` 환산. 1 micro-USD = 10,000 ticks.
TICKS_PER_MICRO_USD = 10_000

#: 제출 임시 예산 키 접두어(06 §3.2). intake 는 `post_id` 가 없다.
SUBMISSION_BUDGET_PREFIX = "submission:"

#: 배심원 예산 키 접두어. 데모 배심원 호출은 판결문 예산을 먹지 않는다(9/20 운영 로그).
JURY_BUDGET_PREFIX = "jury:"

#: `node_results` 보존 기간(06 §3.2 "보존 24h").
NODE_RESULT_TTL = timedelta(hours=24)


def est_max_micro_usd(
    prompt_tokens: int,
    max_output_tokens: int,
    price_in_per_1m: float | int | Decimal,
    price_out_per_1m: float | int | Decimal,
) -> int:
    """예약액 `입력 token × 입력 단가 + max_output_tokens × 출력 단가`, 올림.

    단가는 USD/1M token 이다. 1M token 당 USD 는 token 하나당 micro-USD 와 같다.
    float 오차로 올림이 한 칸 넘어가지 않게 `Decimal(str(단가))` 로 곱한다.
    """
    if prompt_tokens < 0 or max_output_tokens < 0:
        raise ValueError("token 수는 음수일 수 없다")
    total = prompt_tokens * Decimal(str(price_in_per_1m)) + max_output_tokens * Decimal(
        str(price_out_per_1m)
    )
    return math.ceil(total)


def ticks_to_micro_usd(ticks: int) -> int:
    """xAI ticks → micro-USD, 내림."""
    if ticks < 0:
        raise ValueError("ticks 는 음수일 수 없다")
    return ticks // TICKS_PER_MICRO_USD


def resolve_actual_micro_usd(micro_usd: int | None, ticks: int | None) -> int | None:
    """정산액. `micro_usd` → 없으면 `ticks` 내림 환산 → 둘 다 없으면 None(비용 모름)."""
    if micro_usd is not None:
        return micro_usd
    if ticks is not None:
        return ticks_to_micro_usd(ticks)
    return None


def budget_key_for_post(post_id: str) -> str:
    """사건 예산 키. `case_budgets.post_id` 에 그대로 들어간다."""
    return str(post_id)


def budget_key_for_submission(submission_id: str) -> str:
    """제출 임시 예산 키 `submission:{id}`. 사건 예산으로 옮기는 로직은 결정 대기(10 §4.4)."""
    return f"{SUBMISSION_BUDGET_PREFIX}{submission_id}"


def budget_key_for_jury(post_id: str) -> str:
    """배심원 예산 키 `jury:{post_id}`.

    사건 예산과 **따로** 둔다. 9/20 운영에서 배심원 호출이 사건 예산 40원을 먼저 깎아
    같은 사건의 검수관 2차 호출이 `BUDGET_EXCEEDED` 로 거부됐다(판결문이 전 강도 TEMPLATE).
    한 사건의 배심원 투표가 방마다 여러 건 생겨도 이 키 하나를 같이 쓰므로 총량은 막힌다.
    """
    return f"{JURY_BUDGET_PREFIX}{post_id}"


def request_hash(canonical_input: Any) -> str:
    """canonical 입력의 sha256 hex.

    `draft_hash` 와 같은 규칙이다. 문자열(키·값) NFC, 키 정렬, 공백 없는 JSON, 비 ASCII 그대로,
    UTF-8 바이트.
    """
    text = json.dumps(
        _nfc(canonical_input), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def node_result_expires_at(now: datetime) -> datetime:
    """`now + 24h`."""
    return now + NODE_RESULT_TTL


@dataclass(frozen=True)
class NodeResultKey:
    """`node_results` 재사용 키 5요소(06 §3.2). 전부 같을 때만 재사용한다."""

    request_hash: str
    model_id: str
    prompt_version: str
    policy_version: str
    privacy_versions: Any

    def versions(self) -> dict[str, Any]:
        """원장 포트 `versions` 인자 — `request_hash` 를 뺀 4요소."""
        return {
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "policy_version": self.policy_version,
            "privacy_versions": self.privacy_versions,
        }
