"""에이전트 DB 시드 멱등(08 §3.5, OP-05). 실제 Postgres.

`scripts/seed_agent_db.py` 를 명령줄 그대로(`main(argv)`) 두 번 돌린다 → `ai.banter_examples`·
`ai.memory_facts`·`ai.processed_memory_events` 행 수가 같다. 출력에 접속 문자열이 없다.
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.core.config import Settings
from tests.unit.test_seed_agent_db import script

TABLES = ("ai.banter_examples", "ai.memory_facts", "ai.processed_memory_events")

CANDIDATES = [
    {
        "id": "CHEAPER_ALTERNATIVE-spicy-aaaaaaaaaa",
        "strategy": "CHEAPER_ALTERNATIVE",
        "intensity": "spicy",
        "category": "식비",
        "text": "그 돈이면 김밥 열 줄이다",
    },
    {
        "id": "CHEAPER_ALTERNATIVE-spicy-bbbbbbbbbb",
        "strategy": "CHEAPER_ALTERNATIVE",
        "intensity": "spicy",
        "category": "배달",
        "text": "배달비로 라면 세 봉지 산다",
    },
]


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in CANDIDATES), encoding="utf-8"
    )
    pairs = tmp_path / "pairs.csv"
    with pairs.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(script.banter.CSV_COLUMNS))
        writer.writeheader()
        writer.writerow(
            {
                "pair_id": "1",
                "strategy": "CHEAPER_ALTERNATIVE",
                "intensity": "spicy",
                "category": "식비",
                "a_id": CANDIDATES[0]["id"],
                "a_text": CANDIDATES[0]["text"],
                "b_id": CANDIDATES[1]["id"],
                "b_text": CANDIDATES[1]["text"],
                "choice": "b",
            }
        )
    return pairs, candidates


async def _counts(engine: AsyncEngine) -> dict[str, int]:
    async with engine.connect() as conn:
        return {
            table: int((await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())
            for table in TABLES
        }


async def test_두_번_실행해도_행_수가_같다(
    engine: AsyncEngine,
    test_database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    pairs, candidates = _write_inputs(tmp_path)
    argv = [
        "--banter-csv",
        str(pairs),
        "--banter-candidates",
        str(candidates),
        "--demo-user",
        "user-seed-c",
        "--demo-room",
        "room-seed-c",
        "--post-ids",
        "post-seed-1,post-seed-2",
        "--now",
        "2026-09-14T12:00:00+00:00",
        "--url",
        test_database_url,
    ]
    settings = Settings(_env_file=None)
    assert await _counts(engine) == dict.fromkeys(TABLES, 0)

    # main 은 안에서 asyncio.run 을 부른다. 테스트 이벤트 루프 밖(스레드)에서 돌린다.
    assert await asyncio.to_thread(script.main, argv, settings=settings) == 0
    first = await _counts(engine)
    assert first["ai.banter_examples"] == 1
    assert first["ai.memory_facts"] > 0
    assert first["ai.processed_memory_events"] == len(script.demo_c.DEMO_SEEDS)

    assert await asyncio.to_thread(script.main, argv, settings=settings) == 0
    assert await _counts(engine) == first

    captured = capsys.readouterr()
    password = make_url(test_database_url).password
    assert "새로 넣음 1건" in captured.out and "새로 넣음 0건" in captured.out
    assert test_database_url not in captured.out + captured.err
    if password:
        assert f":{password}@" not in captured.out + captured.err
    assert sys.modules["seed_agent_db"] is script
