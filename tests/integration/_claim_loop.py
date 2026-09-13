"""경합 테스트가 subprocess 로 띄우는 claim 루프 (02 §4.1 "워커 2 프로세스 × 슬롯 4").

    python tests/integration/_claim_loop.py <worker_id> <iterations> [url]

접속은 3번째 인자 또는 `TEST_DATABASE_URL` 환경변수다. 접속 문자열은 찍지 않는다.
claim 에 성공할 때마다 `{"worker_id","job_id","generation_id"}` 를 한 줄 JSON 으로 stdout 에 쓴다.
빈 큐가 `EMPTY_LIMIT` 번 이어지면 끝낸다.

pytest 가 수집하지 않도록 파일 이름이 `test_` 로 시작하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from geoji_ai.adapters.postgres_jobs import PostgresJobs, make_engine
from geoji_ai.workers.dispatch import SLOT_KINDS

# 슬롯이 다루는 kind 전부. 한 프로세스가 4 kind 를 모두 필터로 건다.
# `SLOT_KINDS` 에서 만든다 — kind 가 늘면 이 테스트도 같이 본다.
KINDS = tuple(dict.fromkeys(kind for kinds in SLOT_KINDS.values() for kind in kinds))
EMPTY_LIMIT = 20
EMPTY_SLEEP_S = 0.02
LEASE_S = 60.0


async def _run(url: str, worker_id: str, iterations: int) -> None:
    engine = make_engine(url)
    jobs = PostgresJobs(engine, lease_s=LEASE_S)
    empty = 0
    try:
        for _ in range(iterations):
            job = await jobs.claim(KINDS, worker_id)
            if job is None:
                empty += 1
                if empty >= EMPTY_LIMIT:
                    break
                await asyncio.sleep(EMPTY_SLEEP_S)
                continue
            empty = 0
            line = {
                "worker_id": worker_id,
                "job_id": job.id,
                "generation_id": job.generation_id,
            }
            sys.stdout.write(json.dumps(line) + "\n")
            sys.stdout.flush()
    finally:
        await engine.dispose()


def main(argv: list[str]) -> int:
    worker_id = argv[0]
    iterations = int(argv[1])
    url = argv[2] if len(argv) > 2 else os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        sys.stderr.write("TEST_DATABASE_URL 이 없다\n")
        return 2
    asyncio.run(_run(url, worker_id, iterations))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
