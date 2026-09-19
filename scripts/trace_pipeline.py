"""에이전트 파이프라인 한 바퀴를 돌리고 역할 간 핸드오프를 마크다운으로 남긴다(9/19).

uv run scripts/trace_pipeline.py --out trace.md                 # 기본: FakeLLM(무료, 키 없음)
uv run scripts/trace_pipeline.py --execute --out trace.md       # 실제 모델(유료, 약 7~8회 호출)
uv run scripts/trace_pipeline.py --execute --case 4 --first-spend --out trace.md
uv run scripts/trace_pipeline.py --execute --snapshot my-case.json --out trace.md

심문관 → 조서·드립 → 양형관·서기·검수관·finalize 를 같은 사건으로 잇고, 호출마다 시스템 프롬프트·
입력·출력을 그대로 적는다. 판결문이 이상할 때 어느 역할이 무엇을 받고 무엇을 넘겼는지
한 파일로 본다.
백엔드·DB 는 메모리 fake(`tests/fakes/pipeline.py`)다. 아무것도 저장하지 않는다.

`--case N` 은 `scripts/probe_verdict_cards.py` 의 합성 사건(0~5), `--snapshot` 은 CaseSnapshot JSON.
`--first-spend` 는 백엔드 이력을 비운다(F0 만). 기본은 반복 3건·방 규칙·지난 판결·지난 지출이
있는 단골 사건이다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from tests.fakes.pipeline import render_trace, run_pipeline_async  # noqa: E402

from geoji_ai.adapters.fake_llm import FakeLLM  # noqa: E402
from geoji_ai.adapters.llm_router import RoleRoutedLLM, build_llm  # noqa: E402
from geoji_ai.contracts.case import CaseSnapshot  # noqa: E402
from geoji_ai.core.config import Settings  # noqa: E402

FIXTURE = ROOT / "contracts" / "fixtures" / "case-snapshot-taxi.json"


def load_snapshot(args: argparse.Namespace) -> CaseSnapshot:
    if args.snapshot:
        return CaseSnapshot.model_validate(json.loads(args.snapshot.read_text(encoding="utf-8")))
    if args.case is not None:
        from probe_verdict_cards import example_case

        snapshot, _ = example_case(args.case)
        # 합성 사건은 방이 없다. 드립·서기가 돌게 방 하나를 붙인다(강도는 --intensity).
        data = snapshot.model_dump(mode="json")
        data["audience"]["room_ids"] = ["room-trace"]
        data["room_snapshots"] = [
            {"room_id": "room-trace", "intensity": args.intensity, "rule_version": 1}
        ]
        data["privacy_versions"] = [{"scope_key": "room:room-trace", "epoch": 1}]
        return CaseSnapshot.model_validate(data)
    return CaseSnapshot.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


async def amain(args: argparse.Namespace) -> int:
    settings = (
        Settings() if args.execute else Settings(_env_file=None, OPENAI_API_KEY="", XAI_API_KEY="")
    )
    snapshot = load_snapshot(args)
    if args.execute:
        llm = build_llm(settings)
        if llm is None:
            print("OPENAI_API_KEY·XAI_API_KEY 가 없다. .env 또는 환경변수에 둔다.")
            return 1
    else:
        llm = FakeLLM()
    try:
        trace = await run_pipeline_async(
            llm,
            snapshot,
            settings=settings,
            resolved=None if args.first_spend else "default",
            remaining_s=args.remaining_s,
            with_intake=not args.no_intake,
        )
    finally:
        if isinstance(llm, RoleRoutedLLM):
            for role in ("writer", "evaluator", "sentencing"):
                adapter = llm.adapter_for(role)
                if adapter is not None:
                    await adapter.client.close()
    text = render_trace(trace, full_prompts=args.full_prompts)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(
            f"추적: {args.out} ({'실제 모델' if args.execute else 'FakeLLM'}) 결과 {trace.outcome}"
        )
    else:
        print(text)
    return 0 if trace.outcome == "AI_READY" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--execute", action="store_true", help="실제 모델 호출(유료)")
    parser.add_argument("--snapshot", type=Path, help="CaseSnapshot JSON 경로")
    parser.add_argument("--case", type=int, help="probe_verdict_cards 합성 사건 번호(0~5)")
    parser.add_argument("--intensity", default="spicy", choices=["mild", "spicy", "hell"])
    parser.add_argument("--first-spend", action="store_true", help="백엔드 이력 없음(F0 만)")
    parser.add_argument("--no-intake", action="store_true", help="심문관 단계를 뺀다")
    parser.add_argument("--remaining-s", type=float, default=90.0, help="SENTENCE 마감(초)")
    parser.add_argument("--full-prompts", action="store_true", help="시스템 프롬프트 전문 포함")
    parser.add_argument("--out", type=Path, help="마크다운 저장 경로(없으면 stdout)")
    args = parser.parse_args(argv)
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
