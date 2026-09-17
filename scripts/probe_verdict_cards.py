"""현재 운영 서기 요청으로 순한맛·매운맛·지옥맛을 점검한다.

python scripts/probe_verdict_cards.py                         # 기본: 호출 없는 요청 점검
python scripts/probe_verdict_cards.py --fake                  # 고정 fixture, 실제 AI 아님
python scripts/probe_verdict_cards.py --execute --out /tmp/cards.json  # 유료 xAI 3회

환경변수와 현재 디렉터리의 .env를 Settings로 읽는다. 실제 모델 호출은 --execute에서만 한다.
합성 택시 사건·가정한 배심원 평결과 형량을 입력한다. 양형·검수·백엔드 저장은 실행하지 않는다.
재시도·repair·기본 문구 치환 없이 받은 구조화 응답을 그대로 보고한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict
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


def example_case() -> tuple[CaseSnapshot, SentencingDecision]:
    """사용자의 실제 지출·이력과 무관한 고정 입력. F0 이외 근거를 만들지 않는다."""
    snapshot = CaseSnapshot.model_validate(
        {
            "schema_version": 1,
            "post_id": "probe-synthetic-taxi",
            "author_id": "probe-synthetic-user",
            "post_version": 1,
            "item": "택시",
            "reason": "늦잠으로 지각할 것 같아서",
            "amount_krw": 12000,
            "category": "교통/택시",
            "post_type": "spent",
            "created_at": "2026-09-17T00:00:00Z",
            "audience": {"room_ids": [], "audience_version": 1, "public_share_enabled": False},
            "privacy_versions": [],
            "room_snapshots": [],
            "intake_result": None,
            "jury": {
                "verdict_id": "probe-synthetic-verdict",
                "verdict_version": 1,
                "result": "guilty",
                "vote_counts": {"guilty": 3, "notGuilty": 1},
                "guilty_ratio": 0.75,
                "confirmed_at": "2026-09-17T00:10:00Z",
                "deadline_at": "2026-09-17T00:11:30Z",
                "policy": {
                    "version": "probe-fixed-assumption",
                    "allowed_sentences": [{"code": "oneDay", "rank": 1}],
                    "fallback_sentence": "oneDay",
                    "reason_required": True,
                },
                "target_intensities": [i.value for i in INTENSITIES],
                "default_intensity": "spicy",
            },
        }
    )
    decision = SentencingDecision(
        schema_version=1,
        sentence="oneDay",
        sentencing_reason="늦잠으로 발생한 택시 지출입니다.",
        reason_source="TEMPLATE",
        evidence_labels=["F0"],
        aggravating=[],
        mitigating=[],
    )
    return snapshot, decision


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


async def run_probe(settings: Settings, *, mode: Mode = "dry-run") -> dict[str, Any]:
    if mode not in ("dry-run", "fake", "execute"):
        raise ValueError("unknown probe mode")
    snapshot, decision = example_case()
    dossier = minimal_dossier(snapshot)
    requests = []
    for intensity in INTENSITIES:
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
        "assumptions": {
            "synthetic_case": True,
            "jury_result": "guilty",
            "sentence": "oneDay",
            "evidence_labels": ["F0"],
            "history_provided": False,
            "sentencing_model_called": False,
            "evaluator_called": False,
            "backend_saved": False,
            "retries": 0,
        },
        "results": [
            {"intensity": i.value, "request": request, "validation": "not_run", "output": None}
            for i, request in zip(INTENSITIES, requests, strict=True)
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
        report["results"] = await asyncio.gather(
            *(
                _call(llm, i.value, request)
                for i, request in zip(INTENSITIES, requests, strict=True)
            )
        )
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
    args = parser.parse_args(argv)
    selected: Mode = "execute" if args.execute else "fake" if args.fake else "dry-run"
    try:
        report = asyncio.run(run_probe(Settings(), mode=selected))
    except Exception:
        # Settings ValidationError에도 환경변수 원문이 들어갈 수 있다.
        print("점검 실패: 설정 또는 로컬 실행 환경을 확인하세요. 비밀값은 출력하지 않습니다.")
        return 1
    print(f"모드: {selected} | 모델: {report['model']} | 서기: {report['writer_version']}")
    print("합성 사건·가정한 유죄/징역 1일. 서기만 점검하며 검수·백엔드 저장은 실행하지 않습니다.")
    print("통과/종료 코드는 카드 형식 검사 기준입니다. 의미·문구 품질 검수 결과가 아닙니다.")
    if selected == "fake":
        print("고정 fixture 결과입니다. 실제 모델의 문구·토큰·지연시간이 아닙니다.")
    if report.get("error"):
        print(f"오류: {report['error']}")
    for item in report["results"]:
        print(f"[{LABELS[item['intensity']]}] 검증: {item['validation']}")
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
