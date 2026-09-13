"""드립 예시 import(06 §3.6·§4.1 예시, RM-07). 실제 Postgres.

① 선택 행 import 2회 → 행 수 불변(uuid5 + ON CONFLICT DO NOTHING)
② 들어간 행은 approved=true, 준 version
③ `PostgresPreparation.approved_banter_examples` 로 문장이 읽힌다
④ 집계가 DB 에서 센 approved 수와 맞다
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.postgres_preparation import PostgresPreparation
from geoji_ai.domain.intensity import Intensity

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "build_banter_examples.py"


def _load_script() -> ModuleType:
    name = "build_banter_examples"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script()

VERSION = 3

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
    {
        "id": "EXCUSE_STRIPPING-hell-cccccccccc",
        "strategy": "EXCUSE_STRIPPING",
        "intensity": "hell",
        "category": "교통/택시",
        "text": "피곤했다는 핑계는 어제도 들었다",
    },
    {
        "id": "EXCUSE_STRIPPING-hell-dddddddddd",
        "strategy": "EXCUSE_STRIPPING",
        "intensity": "hell",
        "category": "교통/택시",
        "text": "택시는 다리가 없는 사람이 타는 거다",
    },
]


def _csv_rows() -> list[dict[str, str]]:
    pairs = script.build_pairs(CANDIDATES)
    pairs[0]["choice"] = "b"
    pairs[1]["choice"] = "a"
    return pairs


async def _count(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return int(
            (await conn.execute(text("SELECT count(*) FROM ai.banter_examples"))).scalar_one()
        )


async def test_import_twice_is_idempotent_and_readable(engine: AsyncEngine) -> None:
    rows, errors = script.build_import_rows(_csv_rows(), CANDIDATES, VERSION)
    assert errors == []
    assert len(rows) == 2

    assert await script.insert_rows(engine, rows) == 2
    assert await _count(engine) == 2

    again, _ = script.build_import_rows(_csv_rows(), CANDIDATES, VERSION)
    assert await script.insert_rows(engine, again) == 0
    assert await _count(engine) == 2

    async with engine.connect() as conn:
        stored = (
            (
                await conn.execute(
                    text(
                        "SELECT id::text AS id, approved, version, strategy, intensity, category "
                        "FROM ai.banter_examples ORDER BY intensity"
                    )
                )
            )
            .mappings()
            .all()
        )
    assert all(row["approved"] is True for row in stored)
    assert {row["version"] for row in stored} == {VERSION}
    assert {row["id"] for row in stored} == {
        script.example_id("CHEAPER_ALTERNATIVE-spicy-bbbbbbbbbb"),
        script.example_id("EXCUSE_STRIPPING-hell-cccccccccc"),
    }

    preparation = PostgresPreparation(engine)
    assert await preparation.approved_banter_examples(Intensity.spicy, "배달", 5) == [
        "배달비로 라면 세 봉지 산다"
    ]
    assert await preparation.approved_banter_examples(Intensity.hell, "교통/택시", 5) == [
        "피곤했다는 핑계는 어제도 들었다"
    ]
    assert await preparation.approved_banter_examples(Intensity.mild, "식비", 5) == []

    counts = await script.count_approved(engine)
    assert counts == {("CHEAPER_ALTERNATIVE", "spicy"): 1, ("EXCUSE_STRIPPING", "hell"): 1}
    summary = {row.label: row for row in script.summarize(counts)}
    assert summary["총수"].actual == 2 and not summary["총수"].met
