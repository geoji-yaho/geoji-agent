"""그래프 A — 심문관(07 §3.1·§3.3·§3.4).

LangGraph 없이 함수 3개 + 폴백이다: `validate_request` → `call_intake` → `validate_intake_output`,
실패하면 `fallback_result`. 진입점은 `run_intake`.

- 강한 인젝션·무관 텍스트는 모델 없이 `BLOCKED`(`intake_source=AI` — 코드 규칙도 AI 파트 판정이고
  계약 enum 이 AI/FALLBACK 뿐이다)
- 모델 호출은 `llm` 이 `ScopedLLM`(게이트웨이)이면 원장 경유(`submission:{id}`, node `intake`,
  `call_index` INITIAL 0 / FINAL_CHECK 1), 아니면 `structured_call` 직접(원장 없음)
- 로그는 `telemetry.logs.log_node`. 이벤트 이름 `intake_initial`·`intake_final_check` 로 mode 를
  드러낸다(`mode` 는 로그 필드 목록에 없다). 사유·항목 원문은 넘기지 않는다

이 모듈은 SQLAlchemy·어댑터를 import 하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any, get_args

from pydantic import ValidationError

from geoji_ai.application.llm_gateway import CallScope, ScopedLLM
from geoji_ai.contracts.intake import Category, IntakeRequest, IntakeResult
from geoji_ai.contracts.llm_schemas import intake_schema
from geoji_ai.core.config import Settings
from geoji_ai.domain.budget import budget_key_for_submission
from geoji_ai.domain.intake_rules import RuleVerdict, check_required, evaluate_rules
from geoji_ai.ports.llm import LLMError
from geoji_ai.prompts import load_prompt, prompt_bundle_version
from geoji_ai.telemetry.logs import log_node

__all__ = [
    "CATEGORY_MISMATCH_THRESHOLD",
    "GRAPH_NAME",
    "INTAKE_PROMPT",
    "MESSAGE_MAX_CODE_POINTS",
    "NODE",
    "IntakeOutputError",
    "call_intake",
    "fallback_result",
    "run_intake",
    "validate_intake_output",
    "validate_request",
]

GRAPH_NAME = "intake"
NODE = "intake"
INTAKE_PROMPT = "intake-v1.md"
#: `confidence ≥ 이 값 ∧ 사용자 값과 다름` → `MISMATCH`(07 §3.3 `(제안)`).
CATEGORY_MISMATCH_THRESHOLD = 0.8
MESSAGE_MAX_CODE_POINTS = 60
#: 모델 호출 timeout = `INTAKE_TIMEOUT_SECONDS − 이 값`(07 §3.1).
TIMEOUT_MARGIN_S = 0.2

_CATEGORIES: frozenset[str] = frozenset(get_args(Category))
_SENTENCE_ENDS = (".", "?", "!", "。", "\n")


class IntakeOutputError(ValueError):
    """모델 출력이 후처리에서 받아들일 수 없다. `code` 가 폴백 원인이다."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _event(req: IntakeRequest) -> str:
    return f"intake_{req.mode.lower()}"


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _log(req: IntakeRequest, node: str, started: float, *, ok: bool, **fields: Any) -> None:
    log_node(
        _event(req),
        graph_name=GRAPH_NAME,
        node=node,
        latency_ms=_elapsed_ms(started),
        ok=ok,
        **fields,
    )


# --- 노드 1 ------------------------------------------------------------------------


def validate_request(req: IntakeRequest) -> RuleVerdict:
    """필수값(위반이면 `IntakeRuleError` 전파) + 인젝션·무관 텍스트 규칙."""
    check_required(req.item, req.reason, req.amount_krw)
    return evaluate_rules(req.item, req.reason)


def _rule_blocked(req: IntakeRequest, verdict: RuleVerdict) -> IntakeResult:
    return IntakeResult.model_validate(
        {
            "schema_version": 1,
            "mode": req.mode,
            "status": "BLOCKED",
            "item_review": {"status": "OK", "suggested_item": None},
            "message": None,
            "category_review": {"status": "OK", "suggested_category": None, "confidence": 0.0},
            "injection_detected": verdict.injection_detected,
            "intake_source": "AI",
        }
    )


# --- 노드 2 ------------------------------------------------------------------------


def _messages(req: IntakeRequest, hint: bool) -> list[dict]:
    user = {
        "post_type": req.post_type,
        "amount_krw": req.amount_krw,
        "item": req.item,
        "category": req.category,
        "reason": req.reason,
        "injection_hint": hint,
        "mode": req.mode,
    }
    return [
        {"role": "system", "content": load_prompt(INTAKE_PROMPT)},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


async def call_intake(
    req: IntakeRequest,
    hint: bool,
    *,
    llm: Any,
    settings: Settings,
    remaining_s: Callable[[], float],
) -> tuple[dict[str, Any], Any]:
    """모델을 한 번 부른다. (출력, 게이트웨이 확정 정보 또는 None).

    `LLMError`·`TimeoutError` 는 그대로 올린다. 출력이 없거나 `stop` 이 아니면 `IntakeOutputError`.
    """
    messages = _messages(req, hint)
    schema = intake_schema(req.mode)
    timeout = settings.INTAKE_TIMEOUT_SECONDS - TIMEOUT_MARGIN_S
    max_output_tokens = settings.INTAKE_MAX_OUTPUT_TOKENS
    scoped = None
    if isinstance(llm, ScopedLLM):
        scope = CallScope(
            budget_key=budget_key_for_submission(req.submission_id),
            node=NODE,
            call_index=0 if req.mode == "INITIAL" else 1,
            generation_id=None,
            job_id=None,
            prompt_version=prompt_bundle_version(),
            policy_version=settings.GUARDRAIL_POLICY_VERSION,
            privacy_versions=[],
            remaining_s=remaining_s,
            reserve_s=TIMEOUT_MARGIN_S,
        )
        scoped = await llm.scoped_call(
            scope,
            role="intake",
            messages=messages,
            schema=schema,
            timeout_s=timeout,
            max_output_tokens=max_output_tokens,
        )
        result = scoped.result
    else:
        result = await asyncio.wait_for(
            llm.structured_call(
                role="intake",
                messages=messages,
                schema=schema,
                timeout_s=timeout,
                max_output_tokens=max_output_tokens,
            ),
            timeout,
        )
    if result.output is None or result.stop_reason != "stop":
        raise IntakeOutputError("NO_OUTPUT")
    return dict(result.output), scoped


# --- 노드 3 ------------------------------------------------------------------------


def _shorten_message(message: str) -> str:
    if len(message) <= MESSAGE_MAX_CODE_POINTS:
        return message
    cut = min((message.find(end) for end in _SENTENCE_ENDS if end in message), default=-1)
    if cut >= 0:
        first = message[: cut + 1].rstrip()
        if first and len(first) <= MESSAGE_MAX_CODE_POINTS:
            return first
    return message[:MESSAGE_MAX_CODE_POINTS]


def _clamp(value: Any) -> Any:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return min(1.0, max(0.0, float(value)))
    return value


def validate_intake_output(
    output: dict[str, Any], req: IntakeRequest, hint: bool = False
) -> IntakeResult:
    """후처리(07 §3.3). enum 밖 값이 올 수 있어 dict 단계에서 고친 뒤 계약으로 검증한다.

    `hint` 는 모델에 넘긴 약한 패턴 힌트다. 후처리 판정에는 쓰지 않는다(판정은 모델).
    `FINAL_CHECK` 에 `NEEDS_CLARIFICATION` 이면 `IntakeOutputError`. 계약 위반은 `ValidationError`.
    """
    del hint
    data = dict(output)
    data["mode"] = req.mode
    data["intake_source"] = "AI"

    if req.mode == "FINAL_CHECK" and data.get("status") == "NEEDS_CLARIFICATION":
        raise IntakeOutputError("FINAL_CHECK_CLARIFICATION")
    if data.get("injection_detected") is True:
        data["status"] = "BLOCKED"
    if req.mode == "FINAL_CHECK":
        data["item_review"] = {"status": "OK", "suggested_item": None}

    violation_codes: list[str] = []
    review = data.get("category_review")
    if isinstance(review, dict):
        review = dict(review)
        suggested = review.get("suggested_category")
        confidence = review.get("confidence")
        if suggested is not None and suggested not in _CATEGORIES:
            violation_codes.append("CATEGORY_OUT_OF_ENUM")
            review = {"status": "OK", "suggested_category": None, "confidence": _clamp(confidence)}
        else:
            mismatch = (
                suggested is not None
                and isinstance(confidence, int | float)
                and confidence >= CATEGORY_MISMATCH_THRESHOLD
                and suggested != req.category
            )
            review["status"] = "MISMATCH" if mismatch else "OK"
        data["category_review"] = review

    message = data.get("message")
    if data.get("status") == "PASS":
        data["message"] = None
    elif isinstance(message, str):
        data["message"] = _shorten_message(message)

    result = IntakeResult.model_validate(data)
    if violation_codes:
        log_node(
            _event(req),
            graph_name=GRAPH_NAME,
            node="validate_intake_output",
            ok=True,
            violation_codes=violation_codes,
        )
    return result


# --- 폴백 --------------------------------------------------------------------------


def fallback_result(req: IntakeRequest) -> IntakeResult:
    """`PASS`·`FALLBACK`·`message=""`(07 §3.1). 원장 UNKNOWN/FAILED 는 게이트웨이가 기록한다."""
    return IntakeResult.model_validate(
        {
            "schema_version": 1,
            "mode": req.mode,
            "status": "PASS",
            "item_review": {"status": "OK", "suggested_item": None},
            "message": "",
            "category_review": {"status": "OK", "suggested_category": None, "confidence": 0.0},
            "injection_detected": False,
            "intake_source": "FALLBACK",
        }
    )


def _fallback(req: IntakeRequest, started: float, reason: str) -> IntakeResult:
    _log(req, "fallback_result", started, ok=False, fallback_reason=reason)
    return fallback_result(req)


# --- 진입점 ------------------------------------------------------------------------


async def run_intake(
    req: IntakeRequest,
    *,
    llm: Any,
    settings: Settings,
    ledger: Any = None,
) -> IntakeResult:
    """심문관 한 번. 필수값 위반은 `IntakeRuleError` 로 전파한다(라우트가 422).

    원장은 `llm=LLMGateway(제출 전용 ledger, cap 1034 micro-USD)` 로 주입한다. `ledger` 인자는
    호환용(IN-03 `run_intake_eval` 과 약속한 시그니처)이며 쓰지 않는다.
    """
    del ledger
    run_started = time.monotonic()
    total_s = float(settings.INTAKE_TIMEOUT_SECONDS)

    def remaining_s() -> float:
        return total_s - (time.monotonic() - run_started)

    started = time.monotonic()
    verdict = validate_request(req)
    _log(req, "validate_request", started, ok=True)
    if verdict.blocked:
        return _rule_blocked(req, verdict)

    if llm is None:
        return _fallback(req, run_started, "NO_LLM")

    started = time.monotonic()
    try:
        output, scoped = await call_intake(
            req, verdict.injection_hint, llm=llm, settings=settings, remaining_s=remaining_s
        )
    except LLMError as exc:
        _log(req, "call_intake", started, ok=False)
        return _fallback(req, run_started, f"LLM_{exc.kind}")
    except TimeoutError:
        _log(req, "call_intake", started, ok=False)
        return _fallback(req, run_started, "LLM_TIMEOUT")
    except IntakeOutputError as exc:
        _log(req, "call_intake", started, ok=False)
        return _fallback(req, run_started, exc.code)
    except Exception:  # 어떤 호출 실패도 폴백으로 끝낸다(라우트 500 금지)
        _log(req, "call_intake", started, ok=False)
        return _fallback(req, run_started, "LLM_UNEXPECTED")
    _log(req, "call_intake", started, ok=True)

    started = time.monotonic()
    try:
        result = validate_intake_output(output, req, verdict.injection_hint)
    except IntakeOutputError as exc:
        _log(req, "validate_intake_output", started, ok=False)
        return _fallback(req, run_started, exc.code)
    except ValidationError:
        _log(req, "validate_intake_output", started, ok=False)
        return _fallback(req, run_started, "INVALID_OUTPUT")
    _log(req, "validate_intake_output", started, ok=True)

    if scoped is not None:
        await llm.remember(scoped)
    return result
