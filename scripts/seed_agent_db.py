"""에이전트 DB 데모 시드(08 §3.5·§4.3, OP-05). 두 번 돌려도 행이 늘지 않는다.

    uv run scripts/seed_agent_db.py \
        [--banter-csv pairs.csv --banter-candidates candidates.jsonl [--banter-version 1]] \
        [--demo-user U --demo-room R --post-ids P1,P2 [--verdict-ids V1,V2] [--now ISO8601]] \
        [--url URL]

단계(인자가 없는 단계는 건너뛴다):

1. `ai.banter_examples`(06 승인분) — `--banter-csv`·`--banter-candidates` 가 둘 다 있을 때.
   `scripts/build_banter_examples.py` 의 `build_import_rows`·`insert_rows` 를 그대로 쓴다
   (uuid5 id + `ON CONFLICT DO NOTHING`). 선택 오류가 하나라도 있으면 DB 에 붙기 전에 1 로 끝난다
2. 04 데모 C 메모리 — `--demo-user`·`--demo-room`·`--post-ids` 가 셋 다 있을 때.
   `scripts/seed_memory_demo_c.py` 의 `seed` 를 그대로 쓴다(post_id 는 백엔드 시드가 준다, 10 §12)
3. 로컬 `meme_catalog` fixture — `database/migrations` DDL 에 테이블이 없으면 만들지 않고 경고만
   한다. 짤 메타는 백엔드 `meme_images` 소유다(10 §11)

한 단계의 인자를 일부만 주면 인자 오류(종료 코드 2)다.
접속은 `--url` 또는 `DATABASE_URL`(`.env` 포함)이다. **접속 문자열은 출력하지 않는다.** DB 오류도
예외 타입 이름만 출력한다. 프로젝트 venv 에서 돈다(`uv run`).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from geoji_ai.core.config import Settings, get_settings, secret_value

__all__ = [
    "MEME_CATALOG_TABLE",
    "MIGRATIONS_DIR",
    "BanterStep",
    "DemoStep",
    "SeedPlan",
    "build_parser",
    "ddl_has_table",
    "main",
    "plan_from_args",
    "read_banter_rows",
    "run_seed",
]

SCRIPTS_DIR = Path(__file__).resolve().parent
MIGRATIONS_DIR = SCRIPTS_DIR.parent / "database" / "migrations"
MEME_CATALOG_TABLE = "meme_catalog"

_BANTER_ARGS = ("banter_csv", "banter_candidates")
_DEMO_ARGS = ("demo_user", "demo_room", "post_ids")


def _load_script(name: str) -> ModuleType:
    """같은 디렉터리의 스크립트를 모듈로 읽는다(`scripts/` 는 패키지가 아니다)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


banter = _load_script("build_banter_examples")
demo_c = _load_script("seed_memory_demo_c")


# --- 계획 --------------------------------------------------------------------------


@dataclass(frozen=True)
class BanterStep:
    csv_path: Path
    candidates_path: Path
    version: int


@dataclass(frozen=True)
class DemoStep:
    user: str
    room: str
    post_ids: list[str]
    verdict_ids: list[str] | None
    now: datetime | None


@dataclass(frozen=True)
class SeedPlan:
    banter: BanterStep | None
    demo: DemoStep | None

    @property
    def needs_db(self) -> bool:
        return self.banter is not None or self.demo is not None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="에이전트 DB 데모 시드(08 §3.5). 멱등.")
    group = parser.add_argument_group("banter_examples(06 승인분)")
    group.add_argument("--banter-csv", type=Path, help="choice 를 채운 쌍대 선택 CSV")
    group.add_argument("--banter-candidates", type=Path, help="gen 이 만든 후보 JSONL")
    group.add_argument(
        "--banter-version", type=int, default=1, help="예시 version. 기본 1(DDL 기본값)"
    )
    demo = parser.add_argument_group("04 데모 C 메모리")
    demo.add_argument("--demo-user", help="데모 C 사용자 author_id")
    demo.add_argument("--demo-room", help="데모 C 방 room_id")
    demo.add_argument("--post-ids", help=f"백엔드 시드 post_id {len(demo_c.DEMO_SEEDS)}개. P1,P2")
    demo.add_argument("--verdict-ids", help="백엔드 시드 verdict_id(post-ids 순서). 없으면 uuid5")
    demo.add_argument("--now", type=demo_c._parse_now, help="기준 시각 ISO8601. 없으면 지금")
    parser.add_argument("--url", help="접속 문자열. 없으면 DATABASE_URL 을 쓴다.")
    return parser


def _split(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _given(args: argparse.Namespace, names: Sequence[str]) -> list[str]:
    return [name for name in names if getattr(args, name) not in (None, "")]


def _flag(name: str) -> str:
    return "--" + name.replace("_", "-")


def plan_from_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> SeedPlan:
    """단계 선택. 한 단계의 인자를 일부만 주면 `parser.error`."""
    banter_step: BanterStep | None = None
    given = _given(args, _BANTER_ARGS)
    if given and len(given) != len(_BANTER_ARGS):
        missing = [_flag(n) for n in _BANTER_ARGS if n not in given]
        parser.error(f"banter_examples 단계에 인자가 모자라다: {', '.join(missing)}")
    if given:
        banter_step = BanterStep(args.banter_csv, args.banter_candidates, args.banter_version)

    demo_step: DemoStep | None = None
    given = _given(args, _DEMO_ARGS)
    if given and len(given) != len(_DEMO_ARGS):
        missing = [_flag(n) for n in _DEMO_ARGS if n not in given]
        parser.error(f"데모 C 메모리 단계에 인자가 모자라다: {', '.join(missing)}")
    if not given and (args.verdict_ids or args.now):
        parser.error("--verdict-ids·--now 는 데모 C 메모리 단계 인자와 같이 준다")
    if given:
        post_ids = _split(args.post_ids)
        if len(post_ids) != len(demo_c.DEMO_SEEDS):
            parser.error(
                f"--post-ids 는 {len(demo_c.DEMO_SEEDS)}개다: {len(post_ids)}개를 받았다."
            )
        verdict_ids = _split(args.verdict_ids) or None
        if verdict_ids is not None and len(verdict_ids) != len(post_ids):
            parser.error(f"--verdict-ids 는 {len(post_ids)}개다: {len(verdict_ids)}개를 받았다.")
        demo_step = DemoStep(args.demo_user, args.demo_room, post_ids, verdict_ids, args.now)
    return SeedPlan(banter_step, demo_step)


# --- 단계 --------------------------------------------------------------------------


def ddl_has_table(table: str, migrations_dir: Path = MIGRATIONS_DIR) -> bool:
    """`database/migrations/*.sql` 에 `CREATE TABLE [IF NOT EXISTS] [ai.]<table>` 이 있는가."""
    pattern = re.compile(
        rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:\w+\.)?{re.escape(table)}\b",
        re.IGNORECASE,
    )
    return any(
        pattern.search(path.read_text(encoding="utf-8"))
        for path in sorted(migrations_dir.glob("*.sql"))
    )


def read_banter_rows(step: BanterStep) -> tuple[list[dict[str, Any]], list[str]]:
    """CSV·후보 JSONL → INSERT 행과 오류 문장. `build_banter_examples` 의 검사를 그대로 탄다."""
    with step.csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in banter.CSV_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            return [], [f"CSV 헤더에 없는 컬럼: {missing}"]
        csv_rows = list(reader)
    rows, errors = banter.build_import_rows(
        csv_rows, banter.read_jsonl(step.candidates_path), step.version
    )
    return rows, [str(error) for error in errors]


async def run_seed(
    url: str,
    settings: Settings,
    plan: SeedPlan,
    banter_rows: Sequence[dict[str, Any]],
) -> list[str]:
    """DB 단계를 차례로 돈다. 출력할 줄(접속 문자열 없음)을 돌려준다."""
    from geoji_ai.adapters.postgres_jobs import make_engine

    lines: list[str] = []
    engine = make_engine(url)
    try:
        if plan.banter is not None:
            inserted = await banter.insert_rows(engine, banter_rows)
            lines.append(
                f"banter_examples: 선택 {len(banter_rows)}건, 새로 넣음 {inserted}건"
                f"(version={plan.banter.version})"
            )
        if plan.demo is not None:
            written = await demo_c.seed(
                engine,
                settings,
                user=plan.demo.user,
                room=plan.demo.room,
                post_ids=plan.demo.post_ids,
                now=plan.demo.now or datetime.now(UTC),
                verdict_ids=plan.demo.verdict_ids,
            )
            for post_id, rows in written:
                state = "넣었다" if rows else "이미 있다(또는 epoch 불일치). 넣지 않았다"
                lines.append(f"데모 C 메모리 {post_id}: {state} rows={rows}")
    finally:
        await engine.dispose()
    return lines


# --- 명령줄 ------------------------------------------------------------------------


def main(argv: list[str] | None = None, *, settings: Settings | None = None) -> int:
    """`settings` 는 테스트용. 없으면 `get_settings()`."""
    parser = build_parser()
    args = parser.parse_args(argv)
    plan = plan_from_args(parser, args)

    if plan.banter is None:
        print("banter_examples: --banter-csv·--banter-candidates 가 없어 건너뛴다")
    if plan.demo is None:
        print("데모 C 메모리: --demo-user·--demo-room·--post-ids 가 없어 건너뛴다")
    if ddl_has_table(MEME_CATALOG_TABLE):
        # 계획서에 fixture 내용이 없다(08 §3.5 는 이름만). 값을 만들지 않는다.
        print(f"경고: {MEME_CATALOG_TABLE} fixture 내용이 계획서에 없어 건너뛴다", file=sys.stderr)
    else:
        print(
            f"경고: {MEME_CATALOG_TABLE} 테이블이 DDL(database/migrations)에 없어 만들지 않고 "
            "건너뛴다. 짤 메타는 백엔드 meme_images 소유다(10 §11).",
            file=sys.stderr,
        )

    banter_rows: list[dict[str, Any]] = []
    if plan.banter is not None:
        banter_rows, errors = read_banter_rows(plan.banter)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            print(f"banter_examples 오류 {len(errors)}건. 아무것도 넣지 않았다.", file=sys.stderr)
            return 1

    if not plan.needs_db:
        return 0

    settings = settings or get_settings()
    url = args_url if (args_url := _url_arg(argv)) else secret_value(settings, "DATABASE_URL")
    if not url.strip():
        # 접속 문자열은 비밀값이다. 이름만 말한다.
        print("DATABASE_URL 이 비어 있다. --url 로 주거나 .env 에 채운다.", file=sys.stderr)
        return 2
    try:
        lines = asyncio.run(run_seed(url, settings, plan, banter_rows))
    except Exception as exc:
        # 드라이버 예외 문자열에 접속 정보가 섞일 수 있어 타입 이름만 낸다.
        print(f"DB 시드 실패: {type(exc).__name__}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 0


def _url_arg(argv: list[str] | None) -> str | None:
    return build_parser().parse_args(argv).url


if __name__ == "__main__":
    raise SystemExit(main())
