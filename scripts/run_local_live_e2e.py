#!/usr/bin/env python3
"""실제 모델 1건 실측. 기본은 실행 범위 출력만 하며 키를 읽거나 서버를 시작하지 않는다."""

import argparse
import asyncio
import os
from pathlib import Path

from run_local_e2e import ROOT, Stack, failure, write_json

PLAN = {
    "posts": 1,
    "juror_votes": 3,
    "intensity": "mild",
    "models": {
        "intake/context/sentencing/evaluator": "gpt-5.6-luna",
        "banter/writer": "grok-4.20-0309-non-reasoning",
    },
    "max_calls_including_repair": 10,
    "reservation_cap_usd": 0.05,
    "data": "가상 계정의 택시 12000원/늦잠 사유 1건; 로컬 DB만 사용",
    "output": (
        "actual llm_calls usage/cost/latency; AI_READY vs TEMPLATE_READY; selected meme and PNG"
    ),
    "image_generation": "none",
}


class LiveStack(Stack):
    live = True

    def __init__(self, args):
        super().__init__(args)
        from dotenv import dotenv_values

        local_values = dotenv_values(ROOT / ".env")
        for key in ("OPENAI_API_KEY", "XAI_API_KEY"):
            value = os.environ.get(key) or local_values.get(key)
            if not value:
                raise ValueError(
                    f"{key}가 없습니다. 채팅에 입력하지 말고 로컬 환경변수로 주입하세요."
                )
            self.env[key] = value
        self.env.update(
            MODEL_JUDGMENT="gpt-5.6-luna",
            MODEL_EVALUATOR_HELL="gpt-5.6-luna",
            MODEL_WRITER="grok-4.20-0309-non-reasoning",
            GEOJI_EVAL="1",
        )
        budget = self.directory / "live-budget.json"
        write_json(
            budget,
            {"cap_micro_usd": 50000, "max_calls": 10, "reserved_micro_usd": 0, "calls": []},
            private=True,
        )
        self.env["GEOJI_LIVE_BUDGET"] = str(budget)
        self.report.update(scope="real Spring + real vendor models, one local post", plan=PLAN)
        self.report.pop("paid_model_calls", None)

    def start(self, name, command, cwd=ROOT):
        command = [
            arg.replace("tests.local_e2e.runtime:api", "tests.evaluations.local_live_runtime:api")
            if name == "api"
            else arg
            for arg in command
        ]
        if name == "worker":
            command = [
                arg.replace("tests.local_e2e.runtime", "tests.evaluations.local_live_runtime")
                for arg in command
            ]
        return super().start(name, command, cwd)

    async def run(self):
        await self.boot()
        await self.setup_users()
        await self.seed_catalog()
        case = await self.case("live-one-post")
        await self.card_png(case)
        self.report["model_calls"] = [
            dict(row)
            for row in await self.db.fetch(
                "SELECT vendor,model_id,node,status,actual_micro_usd,prompt_tokens,"
                "completion_tokens,reasoning_tokens,"
                "extract(epoch FROM finished_at-started_at)*1000 AS latency_ms "
                "FROM ai.llm_calls ORDER BY started_at"
            )
        ]
        self.report["result"] = (
            "AI_READY" if case["verdict"]["textStatus"] == "AI_READY" else "FALLBACK"
        )


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-approved", action="store_true")
    parser.add_argument("--backend", type=Path)
    parser.add_argument("--frontend", type=Path)
    parser.add_argument(
        "--java-home", type=Path, default=Path("/private/tmp/geoji-e2e-jdk25/Contents/Home")
    )
    args = parser.parse_args()
    if not args.execute_approved:
        import json

        print(json.dumps(PLAN, ensure_ascii=False, indent=2))
        return
    if not args.backend:
        parser.error("--backend 경로가 필요합니다.")
    args.jar = None
    args.baseline = args.keep = False
    args.db_port, args.backend_port, args.ai_port, args.issuer_port = 55438, 18082, 18102, 18199
    stack = LiveStack(args)
    print(f"실측 기록: {stack.directory}", flush=True)
    try:
        await stack.run()
    except BaseException as exc:
        stack.report.update(failure(exc, getattr(stack, "env", {})))
    finally:
        write_json(stack.directory / "report.json", stack.report)
        await stack.close()
        print(f"결과: {stack.report.get('result')} — {stack.directory / 'report.json'}", flush=True)
    if stack.report.get("result") == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
