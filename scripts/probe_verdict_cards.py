"""현재 운영 서기 요청으로 순한맛·매운맛·지옥맛을 점검한다.

python scripts/probe_verdict_cards.py                         # 기본: 호출 없는 요청 점검
python scripts/probe_verdict_cards.py --fake                  # 고정 fixture, 실제 AI 아님
python scripts/probe_verdict_cards.py --execute --out /tmp/cards.json  # 유료 xAI 3회
python scripts/probe_verdict_cards.py --execute --case 2      # 세 강도 모두 같은 사건(2번)

환경변수와 현재 디렉터리의 .env를 Settings로 읽는다. 실제 모델 호출은 --execute에서만 한다.
합성 사건(`EXAMPLE_CASES`, 운영 데이터와 무관)·가정한 배심원 평결과 형량을 입력한다.
기본은 강도마다 다른 사건을 돌려 쓴다(`--case rotate`). `--case N` 이면 세 강도가 같은 사건이다.
양형·검수·백엔드 저장은 실행하지 않는다.
재시도·repair·기본 문구 치환 없이 받은 구조화 응답을 그대로 보고한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from geoji_ai.adapters.fake_llm import FakeLLM
from geoji_ai.adapters.llm_router import RoleRoutedLLM, build_llm
from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.contracts.sentencing import SentencingDecision
from geoji_ai.contracts.writer import CardTextDraft, MemeHints
from geoji_ai.core.config import Settings, secret_value
from geoji_ai.domain.intensity import Intensity
from geoji_ai.graphs.sentencing import build_writer_request, minimal_dossier
from geoji_ai.ports.llm import LLMError, LLMPort
from geoji_ai.prompts import WRITER_VERSION, prompt_bundle_version

Mode = Literal["dry-run", "fake", "execute"]
INTENSITIES = (Intensity.mild, Intensity.spicy, Intensity.hell)
LABELS = {"mild": "순한맛", "spicy": "매운맛", "hell": "지옥맛"}


@dataclass(frozen=True)
class ExampleCase:
    """합성 사건 하나. 실제 사용자·지출·이력과 무관하다. 형량은 유죄일 때만."""

    key: str
    item: str
    reason: str
    amount_krw: int
    category: str
    post_type: str
    result: str
    vote_counts: dict[str, int]
    sentence: str | None
    sentencing_reason: str | None


#: 9/18: 사건 하나로만 돌리면 세 강도 문구가 같은 소재("지하철 8번")로 몰려 품질을 볼 수 없었다.
#: 카테고리·금액·사유·평결을 섞은 6건. 골든셋 사건(tests/evaluations/golden)과 소재가 겹치지 않는다.
EXAMPLE_CASES: tuple[ExampleCase, ...] = (
    ExampleCase(
        key="taxi",
        item="택시",
        reason="늦잠으로 지각할 것 같아서",
        amount_krw=12000,
        category="교통/택시",
        post_type="spent",
        result="guilty",
        vote_counts={"guilty": 3, "notGuilty": 1},
        sentence="oneDay",
        sentencing_reason="늦잠으로 발생한 택시 지출입니다.",
    ),
    ExampleCase(
        key="night-snack",
        item="편의점 야식",
        reason="야근 끝나고 배고파서 참을 수가 없었다",
        amount_krw=8900,
        category="식비",
        post_type="spent",
        result="guilty",
        vote_counts={"guilty": 4, "notGuilty": 0},
        sentence="probation",
        sentencing_reason="야근 뒤 충동적으로 산 야식입니다.",
    ),
    ExampleCase(
        key="game-skin",
        item="게임 스킨",
        reason="한정 판매라 지금 아니면 못 산다",
        amount_krw=33000,
        category="취미/여가",
        post_type="spent",
        result="guilty",
        vote_counts={"guilty": 3, "notGuilty": 2},
        sentence="life",
        sentencing_reason="한정 판매를 이유로 한 게임 아이템 지출입니다.",
    ),
    ExampleCase(
        key="rain-delivery",
        item="치킨 배달",
        reason="비 와서 나가기 싫었다",
        amount_krw=24000,
        category="배달",
        post_type="spent",
        result="guilty",
        vote_counts={"guilty": 2, "notGuilty": 1},
        sentence="oneDay",
        sentencing_reason="날씨를 이유로 시킨 배달 지출입니다.",
    ),
    ExampleCase(
        key="interview-perm",
        item="헤어 펌",
        reason="다음 주 면접이라 첫인상이 중요하다",
        amount_krw=150000,
        category="뷰티",
        post_type="spent",
        result="notGuilty",
        vote_counts={"guilty": 1, "notGuilty": 3},
        sentence=None,
        sentencing_reason=None,
    ),
    ExampleCase(
        key="keyboard-considering",
        item="기계식 키보드",
        reason="지금 쓰는 키보드가 키가 자꾸 씹힌다",
        amount_krw=189000,
        category="기타",
        post_type="considering",
        result="disagree",
        vote_counts={"agree": 1, "disagree": 3},
        sentence=None,
        sentencing_reason=None,
    ),
)


def example_case(index: int = 0) -> tuple[CaseSnapshot, SentencingDecision | None]:
    """`EXAMPLE_CASES[index]` 를 스냅샷·양형 결과로 만든다. F0 이외 근거를 만들지 않는다.

    비유죄(무죄·동의·기각)는 양형이 없으므로 결정이 None 이다(운영 그래프와 같다).
    """
    case = EXAMPLE_CASES[index % len(EXAMPLE_CASES)]
    total = sum(case.vote_counts.values())
    against = case.vote_counts.get("guilty", 0) + case.vote_counts.get("disagree", 0)
    snapshot = CaseSnapshot.model_validate(
        {
            "schema_version": 1,
            "post_id": f"probe-synthetic-{case.key}",
            "author_id": "probe-synthetic-user",
            "post_version": 1,
            "item": case.item,
            "reason": case.reason,
            "amount_krw": case.amount_krw,
            "category": case.category,
            "post_type": case.post_type,
            "created_at": "2026-09-17T00:00:00Z",
            "audience": {"room_ids": [], "audience_version": 1, "public_share_enabled": False},
            "privacy_versions": [],
            "room_snapshots": [],
            "intake_result": None,
            "jury": {
                "verdict_id": f"probe-synthetic-verdict-{case.key}",
                "verdict_version": 1,
                "result": case.result,
                "vote_counts": case.vote_counts,
                "guilty_ratio": round(against / total, 2) if total else 0.0,
                "confirmed_at": "2026-09-17T00:10:00Z",
                "deadline_at": "2026-09-17T00:11:30Z",
                "policy": {
                    "version": "probe-fixed-assumption",
                    "allowed_sentences": [
                        {"code": "probation", "rank": 1},
                        {"code": "oneDay", "rank": 2},
                        {"code": "life", "rank": 3},
                    ],
                    "fallback_sentence": "oneDay",
                    "reason_required": True,
                },
                "target_intensities": [i.value for i in INTENSITIES],
                "default_intensity": "spicy",
            },
        }
    )
    if case.sentence is None:
        return snapshot, None
    decision = SentencingDecision(
        schema_version=1,
        sentence=case.sentence,
        sentencing_reason=case.sentencing_reason or "",
        reason_source="TEMPLATE",
        evidence_labels=["F0"],
        aggravating=[],
        mitigating=[],
    )
    return snapshot, decision


def case_indices(case: str) -> list[int]:
    """`rotate` 는 강도마다 다른 사건(0·1·2), 숫자는 세 강도가 같은 사건."""
    if case == "rotate":
        return [i % len(EXAMPLE_CASES) for i in range(len(INTENSITIES))]
    index = int(case)
    if not 0 <= index < len(EXAMPLE_CASES):
        raise ValueError(f"--case 는 rotate 또는 0~{len(EXAMPLE_CASES) - 1}")
    return [index] * len(INTENSITIES)


def _case_summary(index: int) -> dict[str, Any]:
    case = EXAMPLE_CASES[index]
    return {
        "index": index,
        "key": case.key,
        "item": case.item,
        "amount_krw": case.amount_krw,
        "result": case.result,
        "sentence": case.sentence,
    }


def validate_output(output: dict[str, Any] | None, schema: dict[str, Any]) -> bool:
    """전체 메타데이터·서버 지정 enum과 카드의 공백/길이를 함께 검증한다."""
    if output is None or not Draft202012Validator(schema).is_valid(output):
        return False
    card = {k: v for k, v in output.items() if k not in ("meme_hints", "meme_tag")}
    try:
        CardTextDraft.model_validate({**card, "source": "AI"})
        if output["meme_hints"] is not None:
            MemeHints.model_validate(output["meme_hints"])
    except ValidationError:
        return False
    return True


async def _call(llm: LLMPort, intensity: str, request: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    record: dict[str, Any] = {
        "intensity": intensity,
        "request": request,
        "output": None,
        "validation": "failed",
        "error": None,
        "usage": None,
        "cost": None,
        "stop_reason": None,
        "evidence_validation": "not_run",
        "unknown_evidence_labels": [],
    }
    try:
        # SDK timeout 외에 전체 wall-clock 상한도 둔다. 어댑터는 max_retries=0이다.
        async with asyncio.timeout(request["timeout_s"]):
            result = await llm.structured_call(**request)
        record.update(
            output=result.output,
            usage=asdict(result.usage),
            cost=asdict(result.cost),
            stop_reason=result.stop_reason,
            model=result.model_id,
            vendor=result.vendor,
            provider_latency_ms=result.latency_ms,
        )
        if result.stop_reason != "stop":
            record["error"] = result.stop_reason.upper()
        elif validate_output(result.output, request["schema"]):
            record["validation"] = "passed"
            known = {
                fact["id"] for fact in json.loads(request["messages"][1]["content"])["dossier"]
            }
            unknown = sorted(
                {
                    label
                    for statement in result.output["statement"]
                    for label in statement["evidence_labels"]
                    if label not in known
                }
            )
            record["unknown_evidence_labels"] = unknown
            record["evidence_validation"] = "failed" if unknown else "labels_exist"
        else:
            record["error"] = "INVALID_OUTPUT"
    except LLMError as exc:
        record.update(
            error=exc.kind,
            usage=asdict(exc.usage) if exc.usage else None,
            cost=asdict(exc.cost) if exc.cost else None,
        )
    except TimeoutError:
        record["error"] = "TIMEOUT"
    except Exception:
        # 예외 문자열/traceback에는 공급자 본문이나 자격증명이 포함될 수 있다.
        record["error"] = "CALL_FAILED"
    record["latency_ms"] = round((time.perf_counter() - started) * 1000)
    return record


async def _close_clients(llm: LLMPort, settings: Settings) -> None:
    if not isinstance(llm, RoleRoutedLLM):
        return
    adapters = {
        llm.adapter_for("writer"),
        llm.adapter_for("evaluator"),
        llm.adapter_for("evaluator", settings.MODEL_EVALUATOR_HELL),
    }
    for adapter in adapters:
        if adapter is not None:
            await adapter.client.close()


async def run_probe(
    settings: Settings, *, mode: Mode = "dry-run", case: str = "rotate"
) -> dict[str, Any]:
    if mode not in ("dry-run", "fake", "execute"):
        raise ValueError("unknown probe mode")
    indices = case_indices(case)
    requests = []
    for intensity, index in zip(INTENSITIES, indices, strict=True):
        snapshot, decision = example_case(index)
        dossier = minimal_dossier(snapshot)
        messages, schema = build_writer_request(snapshot, dossier, decision, intensity)
        requests.append(
            {
                "role": "writer",
                "messages": messages,
                "schema": schema,
                "timeout_s": settings.WRITER_NODE_TIMEOUT_SECONDS,
                "max_output_tokens": settings.WRITER_MAX_OUTPUT_TOKENS,
            }
        )
    report: dict[str, Any] = {
        "mode": mode,
        "scope": "writer_only",
        "success": True,
        "validation_scope": "card_schema_only",
        "model": settings.MODEL_WRITER,
        "writer_version": WRITER_VERSION,
        "prompt_bundle": prompt_bundle_version(),
        "provider_calls": 0,
        "case_mode": case,
        "assumptions": {
            "synthetic_case": True,
            "cases": [_case_summary(index) for index in indices],
            "evidence_labels": ["F0"],
            "history_provided": False,
            "sentencing_model_called": False,
            "evaluator_called": False,
            "backend_saved": False,
            "retries": 0,
        },
        "results": [
            {
                "intensity": i.value,
                "case": _case_summary(index),
                "request": request,
                "validation": "not_run",
                "output": None,
            }
            for i, index, request in zip(INTENSITIES, indices, requests, strict=True)
        ],
    }
    if mode == "dry-run":
        return report
    if mode == "execute" and not secret_value(settings, "XAI_API_KEY"):
        return {**report, "success": False, "error": "MISSING_XAI_API_KEY"}
    if settings.WRITER_NODE_TIMEOUT_SECONDS <= 0 or settings.WRITER_MAX_OUTPUT_TOKENS <= 0:
        return {**report, "success": False, "error": "INVALID_WRITER_LIMITS"}
    try:
        llm = FakeLLM() if mode == "fake" else build_llm(settings)
    except Exception:
        return {**report, "success": False, "error": "CLIENT_SETUP_FAILED"}
    if llm is None:
        return {**report, "success": False, "error": "CLIENT_UNAVAILABLE"}
    try:
        called = await asyncio.gather(
            *(
                _call(llm, i.value, request)
                for i, request in zip(INTENSITIES, requests, strict=True)
            )
        )
        report["results"] = [
            {**record, "case": _case_summary(index)}
            for record, index in zip(called, indices, strict=True)
        ]
    finally:
        await _close_clients(llm, settings)
    report["provider_calls"] = 3 if mode == "execute" else 0
    report["success"] = all(item["validation"] == "passed" for item in report["results"])
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="요청 점검만 (기본값, 무료)")
    mode.add_argument("--execute", action="store_true", help="실제 xAI 서기 3회 호출 (유료)")
    mode.add_argument("--fake", action="store_true", help="고정 fixture 점검 (실제 AI 아님)")
    parser.add_argument("--out", type=Path, help="원본 구조화 응답·요청·사용량 JSON 저장 경로")
    parser.add_argument(
        "--case",
        default="rotate",
        help=f"rotate(기본, 강도마다 다른 사건) 또는 0~{len(EXAMPLE_CASES) - 1}(세 강도 같은 사건)",
    )
    args = parser.parse_args(argv)
    selected: Mode = "execute" if args.execute else "fake" if args.fake else "dry-run"
    try:
        case_indices(args.case)
    except ValueError as exc:
        print(f"점검 실패: {exc}")
        return 1
    try:
        report = asyncio.run(run_probe(Settings(), mode=selected, case=args.case))
    except Exception:
        # Settings ValidationError에도 환경변수 원문이 들어갈 수 있다.
        print("점검 실패: 설정 또는 로컬 실행 환경을 확인하세요. 비밀값은 출력하지 않습니다.")
        return 1
    print(f"모드: {selected} | 모델: {report['model']} | 서기: {report['writer_version']}")
    print("합성 사건·가정한 평결·형량. 서기만 점검하며 검수·백엔드 저장은 실행하지 않습니다.")
    print("통과/종료 코드는 카드 형식 검사 기준입니다. 의미·문구 품질 검수 결과가 아닙니다.")
    if selected == "fake":
        print("고정 fixture 결과입니다. 실제 모델의 문구·토큰·지연시간이 아닙니다.")
    if report.get("error"):
        print(f"오류: {report['error']}")
    for item in report["results"]:
        case = item["case"]
        tag = f"{case['item']} {case['amount_krw']:,}원 {case['result']}"
        print(f"[{LABELS[item['intensity']]} · {tag}] 검증: {item['validation']}")
        output = item["output"]
        if isinstance(output, dict):
            # JSON 표현은 원문을 자르거나 문장을 합치지 않고 제어문자도 안전하게 표시한다.
            print(json.dumps(output, ensure_ascii=False))
        if item.get("error"):
            print(f"  오류: {item['error']}")
        if item.get("unknown_evidence_labels"):
            print(f"  근거 검사 실패: 조서에 없는 라벨 {item['unknown_evidence_labels']}")
        if item.get("usage"):
            print(f"  usage={item['usage']} cost={item['cost']} latency_ms={item['latency_ms']}")
    if args.out:
        try:
            args.out.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except OSError:
            print("결과 파일을 저장하지 못했습니다. --out 경로와 쓰기 권한을 확인하세요.")
            return 1
        print(f"보고서: {args.out}")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
