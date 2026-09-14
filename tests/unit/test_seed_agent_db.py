"""에이전트 DB 시드 스크립트(08 §3.5·§4.3, OP-05). DB 없음 — DB 단계는 가짜로 바꾼다.

① 인자 파싱 ② 단계 선택(인자 없는 단계는 건너뜀, 일부만 주면 인자 오류) ③ 접속 문자열 비출력
④ `meme_catalog` 이 DDL 에 없으면 만들지 않고 경고 ⑤ banter 선택 오류면 DB 에 붙지 않는다
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from geoji_ai.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "seed_agent_db.py"


def _load_script() -> ModuleType:
    name = "seed_agent_db"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script()

SECRET = "pw-very-secret-7c1e"
HOST = "seed-db.invalid"
URL = f"postgresql+asyncpg://seed:{SECRET}@{HOST}:5432/geoji"
DEMO = ["--demo-user", "U", "--demo-room", "R", "--post-ids", "P1,P2"]


@pytest.fixture
def db_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """`run_seed` 를 기록만 하는 가짜로 바꾼다."""
    calls: list[dict[str, Any]] = []

    async def fake_run_seed(url: str, settings: Any, plan: Any, banter_rows: Any) -> list[str]:
        calls.append({"url": url, "plan": plan, "banter_rows": list(banter_rows)})
        return ["가짜 DB 단계 완료"]

    monkeypatch.setattr(script, "run_seed", fake_run_seed)
    return calls


def _settings() -> Settings:
    return Settings(_env_file=None)


def _plan(argv: list[str]) -> Any:
    parser = script.build_parser()
    return script.plan_from_args(parser, parser.parse_args(argv))


def _write_banter(tmp_path: Path, *, choice_id: str) -> tuple[Path, Path]:
    candidates = tmp_path / "candidates.jsonl"
    candidate = {
        "id": "CHEAPER_ALTERNATIVE-spicy-aaaaaaaaaa",
        "strategy": "CHEAPER_ALTERNATIVE",
        "intensity": "spicy",
        "category": "식비",
        "text": "그 돈이면 김밥 열 줄이다",
    }
    candidates.write_text(json.dumps(candidate, ensure_ascii=False) + "\n", encoding="utf-8")
    pairs = tmp_path / "pairs.csv"
    with pairs.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(script.banter.CSV_COLUMNS))
        writer.writeheader()
        writer.writerow(
            {
                "pair_id": "1",
                "strategy": candidate["strategy"],
                "intensity": candidate["intensity"],
                "category": candidate["category"],
                "a_id": choice_id,
                "a_text": candidate["text"],
                "b_id": "",
                "b_text": "",
                "choice": "a",
            }
        )
    return pairs, candidates


# --- ① 인자 파싱 ---------------------------------------------------------------------


def test_인자를_모두_주면_두_단계를_파싱한다(tmp_path: Path):
    plan = _plan(
        [
            "--banter-csv",
            str(tmp_path / "p.csv"),
            "--banter-candidates",
            str(tmp_path / "c.jsonl"),
            "--banter-version",
            "3",
            *DEMO,
            "--verdict-ids",
            "V1, V2",
            "--now",
            "2026-09-14T12:00:00+00:00",
        ]
    )

    assert plan.banter == script.BanterStep(tmp_path / "p.csv", tmp_path / "c.jsonl", 3)
    assert plan.demo == script.DemoStep(
        "U", "R", ["P1", "P2"], ["V1", "V2"], datetime(2026, 9, 14, 12, tzinfo=UTC)
    )
    assert plan.needs_db


def test_banter_version_기본값은_1():
    plan = _plan(["--banter-csv", "p.csv", "--banter-candidates", "c.jsonl"])
    assert plan.banter.version == 1
    assert plan.demo is None


# --- ② 단계 선택 ---------------------------------------------------------------------


def test_인자가_없으면_두_단계_모두_건너뛰고_DB_에_붙지_않는다(
    db_calls: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
):
    assert script.main([], settings=_settings()) == 0

    out = capsys.readouterr().out
    assert db_calls == []
    assert "banter_examples: --banter-csv·--banter-candidates 가 없어 건너뛴다" in out
    assert "데모 C 메모리: --demo-user·--demo-room·--post-ids 가 없어 건너뛴다" in out


def test_데모_인자만_있으면_banter_는_건너뛰고_데모만_돈다(
    db_calls: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
):
    assert script.main([*DEMO, "--url", URL], settings=_settings()) == 0

    out = capsys.readouterr().out
    assert len(db_calls) == 1
    plan = db_calls[0]["plan"]
    assert plan.banter is None
    assert plan.demo.post_ids == ["P1", "P2"]
    assert db_calls[0]["url"] == URL
    assert "banter_examples: --banter-csv·--banter-candidates 가 없어 건너뛴다" in out
    assert "가짜 DB 단계 완료" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["--banter-csv", "p.csv"],
        ["--banter-candidates", "c.jsonl"],
        ["--demo-user", "U", "--post-ids", "P1,P2"],
        ["--demo-room", "R"],
        ["--demo-user", "U", "--demo-room", "R", "--post-ids", "P1"],
        [*DEMO, "--verdict-ids", "V1"],
        ["--verdict-ids", "V1,V2"],
    ],
    ids=[
        "csv만",
        "candidates만",
        "demo_room_없음",
        "demo_room만",
        "post_ids_1개",
        "verdict_ids_개수",
        "verdict_ids만",
    ],
)
def test_한_단계의_인자를_일부만_주면_인자_오류(
    argv: list[str], db_calls: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
):
    with pytest.raises(SystemExit) as caught:
        script.main(argv, settings=_settings())

    assert caught.value.code == 2
    assert db_calls == []


# --- ③ 접속 문자열 비출력 ----------------------------------------------------------------


def test_성공해도_접속_문자열을_출력하지_않는다(
    db_calls: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
):
    assert script.main([*DEMO, "--url", URL], settings=_settings()) == 0

    captured = capsys.readouterr()
    for text in (captured.out, captured.err):
        assert SECRET not in text
        assert HOST not in text


def test_DB_오류_메시지에_접속_문자열이_있어도_출력하지_않는다(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    async def failing(url: str, *_: Any) -> list[str]:
        raise RuntimeError(f"could not connect to {url}")

    monkeypatch.setattr(script, "run_seed", failing)

    assert script.main([*DEMO, "--url", URL], settings=_settings()) == 1

    captured = capsys.readouterr()
    assert "DB 시드 실패: RuntimeError" in captured.err
    for text in (captured.out, captured.err):
        assert SECRET not in text
        assert HOST not in text


def test_DATABASE_URL_이_없으면_이름만_말하고_2(
    monkeypatch: pytest.MonkeyPatch,
    db_calls: list[dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert script.main(DEMO, settings=_settings()) == 2

    assert db_calls == []
    assert "DATABASE_URL 이 비어 있다" in capsys.readouterr().err


def test_DATABASE_URL_환경값을_쓰고_출력하지_않는다(
    db_calls: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
):
    settings = Settings(_env_file=None, DATABASE_URL=URL)

    assert script.main(DEMO, settings=settings) == 0

    assert db_calls[0]["url"] == URL
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


# --- ④ meme_catalog ------------------------------------------------------------------


def test_meme_catalog_은_DDL_에_없어_만들지_않고_경고한다(
    db_calls: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
):
    assert not script.ddl_has_table("meme_catalog")
    assert script.ddl_has_table("banter_examples")

    assert script.main([], settings=_settings()) == 0

    assert "meme_catalog 테이블이 DDL(database/migrations)에 없어" in capsys.readouterr().err


def test_ddl_has_table_은_CREATE_TABLE_문만_본다(tmp_path: Path):
    (tmp_path / "001_x.sql").write_text(
        "-- meme_catalog 는 주석일 뿐\nCREATE TABLE IF NOT EXISTS ai.meme_catalog (id uuid);",
        encoding="utf-8",
    )
    (tmp_path / "002_y.sql").write_text("SELECT 'meme_catalog_old';", encoding="utf-8")

    assert script.ddl_has_table("meme_catalog", tmp_path)
    assert not script.ddl_has_table("meme_catalog_old", tmp_path)


# --- ⑤ banter 선택 오류 ----------------------------------------------------------------


def test_banter_선택_오류면_DB_에_붙기_전에_1(
    tmp_path: Path, db_calls: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
):
    pairs, candidates = _write_banter(tmp_path, choice_id="no-such-candidate")

    argv = ["--banter-csv", str(pairs), "--banter-candidates", str(candidates), "--url", URL]
    assert script.main(argv, settings=_settings()) == 1

    assert db_calls == []
    assert "후보 JSONL 에 없는 id" in capsys.readouterr().err


def test_banter_선택이_맞으면_import_행을_DB_단계로_넘긴다(
    tmp_path: Path, db_calls: list[dict[str, Any]]
):
    pairs, candidates = _write_banter(tmp_path, choice_id="CHEAPER_ALTERNATIVE-spicy-aaaaaaaaaa")

    argv = ["--banter-csv", str(pairs), "--banter-candidates", str(candidates), "--url", URL]
    assert script.main(argv, settings=_settings()) == 0

    rows = db_calls[0]["banter_rows"]
    assert [(row["strategy"], row["intensity"], row["version"]) for row in rows] == [
        ("CHEAPER_ALTERNATIVE", "spicy", 1)
    ]
    assert rows[0]["id"] == script.banter.example_id("CHEAPER_ALTERNATIVE-spicy-aaaaaaaaaa")
