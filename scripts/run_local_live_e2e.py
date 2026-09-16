#!/usr/bin/env python3
"""실제 모델 1건 실측. 기본은 실행 범위 출력만 하며 키를 읽거나 서버를 시작하지 않는다."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from run_local_e2e import ROOT, Stack, default_java_home, failure, write_json

PLAN = {
    "posts": 1,
    "juror_votes": 3,
    "intensity": "mild (--intensity 로 spicy·hell)",
    "models": {
        "intake/context/sentencing/evaluator": "gpt-5.6-luna",
        "banter/writer": "grok-4.20-0309-non-reasoning",
    },
    # 9/16: 검수 출력 상한 3000 토큰이라 호출당 예약이 커졌다.
    # 기본 12회·$0.15 (--max-calls·--cap-usd 로 조정)
    "max_calls_including_repair": 12,
    "reservation_cap_usd": 0.15,
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
        self.intensity = args.intensity
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
            {
                "cap_micro_usd": int(round(args.cap_usd * 1_000_000)),
                "max_calls": args.max_calls,
                "reserved_micro_usd": 0,
                "calls": [],
            },
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

    async def optional(self, name, coro):
        """server main 에 아직 없는 엔드포인트(짤 관리자 API·PNG 렌더, 10 §16.6)는 건너뛴다."""
        try:
            return await coro
        except AssertionError as exc:
            if ": 404 != " not in str(exc):
                raise
            self.report.setdefault("skipped", []).append({"step": name, "reason": str(exc)[:200]})
            print(f"건너뜀 {name}: 백엔드에 엔드포인트 없음(404)", flush=True)
            return None

    async def run(self):
        await self.boot()
        await self.setup_users()
        await self.optional("seed_catalog", self.seed_catalog())
        case = await self.case("live-one-post")
        await self.optional("card_png", self.card_png(case))
        await self.collect_texts(case)
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

    async def collect_texts(self, case):
        """사람이 읽을 판결문. 강도별 문구·출처·형량·양형 이유를 report 와 stdout 에 남긴다."""
        from uuid import UUID

        post = case["post_id"]
        row = await self.db.fetchrow("SELECT * FROM verdicts WHERE post_id=$1", UUID(post))
        texts = await self.db.fetch(
            "SELECT intensity, headline, statement, source, text_version "
            "FROM verdict_texts WHERE verdict_id=$1 ORDER BY intensity",
            row["id"],
        )
        view = (
            await self.request("GET", f"/api/posts/{post}/verdict?room_id={self.room_id}")
        ).json()
        self.report["verdict"] = {
            "jury_result": row["jury_result"],
            "sentence": row["sentence"],
            "sentence_source": row["sentence_source"],
            "sentencing_reason": row["sentencing_reason"],
            "reason_source": row["reason_source"],
            "text_status": row["text_status"],
            "last_failed_code": row["last_failed_code"],
            "texts": [
                {
                    "intensity": t["intensity"],
                    "source": t["source"],
                    "headline": t["headline"],
                    "statement": [s["text"] for s in json.loads(t["statement"])]
                    if isinstance(t["statement"], str)
                    else [s["text"] for s in t["statement"]],
                }
                for t in texts
            ],
            "public_view": view.get("view"),
        }
        print("\n===== 판결문 =====", flush=True)
        print(
            f"결과 {row['jury_result']} · 형량 {row['sentence']}({row['sentence_source']}) · "
            f"양형 이유({row['reason_source']}): {row['sentencing_reason']}",
            flush=True,
        )
        for t in self.report["verdict"]["texts"]:
            print(f"\n[{t['intensity']}] ({t['source']}) {t['headline']}", flush=True)
            for line in t["statement"]:
                print(f"  - {line}", flush=True)
        if row["last_failed_code"]:
            print(f"\n마지막 실패 코드: {row['last_failed_code']}", flush=True)
        print("==================\n", flush=True)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-approved", action="store_true")
    parser.add_argument("--backend", type=Path)
    parser.add_argument("--frontend", type=Path)
    parser.add_argument("--java-home", type=Path, default=default_java_home())
    parser.add_argument(
        "--intensity", choices=["mild", "spicy", "hell"], default="mild", help="방 강도"
    )
    parser.add_argument("--max-calls", type=int, default=PLAN["max_calls_including_repair"])
    parser.add_argument("--cap-usd", type=float, default=PLAN["reservation_cap_usd"])
    parser.add_argument("--keep", action="store_true", help="끝나도 스택을 남긴다(웹으로 볼 때)")
    args = parser.parse_args()
    if not args.execute_approved:
        print(json.dumps(PLAN, ensure_ascii=False, indent=2))
        return
    if not args.backend:
        parser.error("--backend 경로가 필요합니다.")
    args.jar = None
    # server main 은 15 §5 참고 패치(멱등키 재전송·PNG) 미반영이라
    # baseline 검사 집합으로 돈다(10 §16.6).
    args.baseline = True
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
