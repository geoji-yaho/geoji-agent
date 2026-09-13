"""드립 예시 60 만들기(06 §3.6·§4.1, proposal1 §9.3 절차, RM-07).

원문 20+(팀 수집) → `gen` → `pair`(팀 쌍대 선택 CSV) → `import`(`ai.banter_examples`).
런타임 recall 은 `approved=true` 만 읽는다(06 §7.3). 원문 시드와 평가 세트는 저장소에 두지 않고
경로 인자로만 받는다.

    uv run scripts/build_banter_examples.py gen --per-strategy 6 --seeds SEEDS.jsonl \
        --out candidates.jsonl [--intensities mild,spicy,hell] [--categories 식비,배달] \
        [--exclude EVAL.jsonl]
    uv run scripts/build_banter_examples.py pair --in candidates.jsonl --out pairs.csv
    uv run scripts/build_banter_examples.py import --csv pairs.csv --candidates candidates.jsonl \
        [--version 1] [--url URL]

gen
- `BanterStrategy` 8종 × 강도마다 서기 모델(`MODEL_WRITER`, xAI) 1회 호출로 N개를 받는다.
  N 을 넘게 오면 N 개까지 자른다. 생성 프롬프트는 이 파일의 상수다(런타임 `prompts/` 가 아니다)
- 시드(proposal1 §9.2 모양 JSONL)의 `one_liner` 는 **참고 말투**로만 넘기고 "다른 사건" 원칙을
  지시한다. 정규화(공백·문장부호 제거, 소문자) 뒤 후보가 시드를 포함하거나 시드에 포함되면 버린다.
  `--exclude`(평가 세트) 문장도 같은 판정으로 버린다. 같은 실행 안에서 겹치는 후보도 버린다
- 카테고리는 `--categories` 가 없으면 `contracts.intake.Category` 11종을 순서대로 돌려 배정한다.
  호출마다 N 칸을 미리 배정하고, 버린 후보의 칸은 비운다
- 후보 id 는 `{strategy}-{intensity}-{sha1(text) 앞 10자}`. 내용으로 정해져 다른 실행에서 같은
  문장이 나오면 id 도 같다(import 에서 두 번 들어가지 않는다)
- 출력 JSONL 필드는 `id, strategy, intensity, category, text` 뿐이다.
  시드 id·출처 메타는 넣지 않는다

pair
- 같은 strategy·intensity 후보를 입력 순서대로 둘씩 묶는다. 홀수면 마지막 행은 `b_*` 가 빈칸.
  `category` 는 a 후보의 것이다(b 와 다를 수 있다). `choice` 는 빈칸 — 팀이 `a`/`b`/`none` 을 채운다
- CSV 는 `utf-8-sig`(엑셀에서 한글이 깨지지 않게)

import
- `choice`(앞뒤 공백 제거·소문자)가 `a`/`b` 인 행만 넣는다. `none`·빈칸은 건너뛴다. 그 밖 값은 오류
- 문장은 후보 JSONL 이 정본이다. CSV 의 id 가 JSONL 에 없거나, CSV 문장이 JSONL 문장과 다르면 오류
- 어휘 검사(`domain.lexicon`): `DEATH_WORDS`·`WORN_PHRASES` 는 모든 강도, `PROFANITY` 는
  mild·spicy. 걸리면 오류
- 오류가 하나라도 있으면 **아무것도 넣지 않고** 행 번호를 출력한 뒤 1 로 끝난다. 행 번호는 CSV
  레코드 번호다(헤더가 1행, 첫 데이터 행이 2행)
- 행 id 는 `uuid5(NAMESPACE_URL, "banter-example:{후보 id}")`, `ON CONFLICT (id) DO NOTHING` 이라
  두 번 돌려도 행이 늘지 않는다
- 끝에 06 §4.1 목표 대비 집계를 출력한다. 미달은 표시만 하고 종료 코드는 0 이다

키와 접속 문자열은 출력하지 않는다. 프로젝트 venv 에서 돈다(`uv run`).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.contracts.intake import Category
from geoji_ai.contracts.writer import BanterStrategy
from geoji_ai.domain.intensity import ALL_INTENSITIES, Intensity, parse_intensity
from geoji_ai.domain.lexicon import (
    DEATH_WORDS,
    PROFANITY,
    WORN_PHRASES,
    LexiconRule,
    applies,
)
from geoji_ai.ports.llm import LLMError, LLMPort

__all__ = [
    "CANDIDATE_SCHEMA",
    "CSV_COLUMNS",
    "TARGET_PER_INTENSITY",
    "TARGET_PER_STRATEGY",
    "TARGET_TOTAL",
    "RowError",
    "SummaryRow",
    "build_import_rows",
    "build_pairs",
    "candidate_id",
    "count_approved",
    "example_id",
    "format_summary",
    "generate_candidates",
    "insert_rows",
    "is_copy",
    "lexicon_violations",
    "load_texts",
    "main",
    "normalize",
    "read_jsonl",
    "summarize",
    "write_jsonl",
    "write_pairs_csv",
]

CATEGORIES: tuple[str, ...] = get_args(Category)

# --- 06 §4.1 예시 목표 -------------------------------------------------------------

TARGET_TOTAL = 60
TARGET_PER_STRATEGY = 4
TARGET_PER_INTENSITY = 15

# --- gen ---------------------------------------------------------------------------

#: 서기 역할 strict 스키마. 문장만 받는다(카테고리·전략은 요청에서 정한다).
CANDIDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
            },
        }
    },
}

#: 전략 이름의 한국어 풀이. enum 이름을 옮긴 것이다.
STRATEGY_HINTS: dict[BanterStrategy, str] = {
    BanterStrategy.CHEAPER_ALTERNATIVE: "더 싼 대안을 들이민다",
    BanterStrategy.FREE_ALTERNATIVE: "공짜 대안을 들이민다",
    BanterStrategy.DIY_REPLACEMENT: "직접 만들면 됐다고 짚는다",
    BanterStrategy.PREMISE_REJECTION: "소비의 전제를 부정한다",
    BanterStrategy.EXCUSE_STRIPPING: "변명을 벗겨 낸다",
    BanterStrategy.NECESSITY_APPROVAL: "필요한 소비였음을 인정한다",
    BanterStrategy.REPEAT_OFFENSE: "같은 소비의 반복을 짚는다",
    BanterStrategy.ROOM_RULE_CALLBACK: "방 규칙을 다시 꺼낸다",
}

GEN_SYSTEM_PROMPT = """\
너는 소비 절제 커뮤니티 '떼거지'의 판결문에 들어갈 한 줄 드립 예시를 쓰는 작가다.
친구들이 서로의 지출을 재판하는 방에서 쓰는 말투로, 사건 하나에 한 문장씩 쓴다.

지킬 것
- 다른 사건 원칙: 참고 말투 문장의 사건·품목·금액·표현을 그대로 가져오지 않는다.
  말투만 참고하고 사건은 새로 만든다
- 요청한 전략과 강도에 맞춘다. 문장마다 지정한 카테고리의 소비를 다룬다
- 한 문장, 이모지 없음
- 자해·죽음 어휘 금지: {death_words}
- 닳은 문구 금지: {worn_phrases}
- mild·spicy 에서는 욕을 쓰지 않는다: {profanity}

JSON 으로만 답한다. candidates 배열에 문장 {n}개를 순서대로 담는다."""

GEN_USER_PROMPT = """전략: {strategy} ({hint})
강도: {intensity}
카테고리(순서대로 한 문장씩): {categories}

참고 말투(사건은 가져오지 않는다):
{references}"""

_NO_REFERENCE = "(없음)"


def normalize(value: str) -> str:
    """시드 복사 판정용. 글자·숫자만 남기고 소문자로."""
    return "".join(char for char in value if char.isalnum()).lower()


def is_copy(candidate: str, references: Iterable[str]) -> bool:
    """정규화 뒤 후보⊂참조 또는 참조⊂후보면 복사로 본다. 빈 문장도 버린다."""
    cand = normalize(candidate)
    if not cand:
        return True
    for reference in references:
        ref = normalize(reference)
        if ref and (cand in ref or ref in cand):
            return True
    return False


def candidate_id(strategy: str, intensity: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return f"{strategy}-{intensity}-{digest}"


def _seed_intensity(seed: Mapping[str, Any]) -> Intensity | None:
    value = seed.get("intensity")
    if not isinstance(value, str) or not value.strip():
        return None
    return parse_intensity(value.strip().lower())


def _references(
    seeds: Sequence[Mapping[str, Any]], strategy: BanterStrategy, intensity: Intensity
) -> list[str]:
    """같은 전략·강도 시드. 없으면 같은 강도 시드."""
    usable = [seed for seed in seeds if isinstance(seed.get("one_liner"), str)]
    same = [
        seed["one_liner"]
        for seed in usable
        if seed.get("strategy") == strategy.value and _seed_intensity(seed) is intensity
    ]
    if same:
        return same
    return [seed["one_liner"] for seed in usable if _seed_intensity(seed) is intensity]


def _messages(
    strategy: BanterStrategy,
    intensity: Intensity,
    categories: Sequence[str],
    references: Sequence[str],
) -> list[dict]:
    system = GEN_SYSTEM_PROMPT.format(
        death_words=", ".join(DEATH_WORDS),
        worn_phrases=", ".join(WORN_PHRASES),
        profanity=", ".join(PROFANITY),
        n=len(categories),
    )
    user = GEN_USER_PROMPT.format(
        strategy=strategy.value,
        hint=STRATEGY_HINTS[strategy],
        intensity=intensity.value,
        categories=", ".join(categories),
        references="\n".join(f"- {line}" for line in references) or _NO_REFERENCE,
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def generate_candidates(
    llm: LLMPort,
    *,
    per_strategy: int,
    intensities: Sequence[Intensity] = ALL_INTENSITIES,
    categories: Sequence[str] = CATEGORIES,
    seeds: Sequence[Mapping[str, Any]] = (),
    exclude_texts: Sequence[str] = (),
    timeout_s: float,
    max_output_tokens: int,
) -> list[dict]:
    """전략 8종 × 강도마다 한 번 호출해 후보를 만든다. 시드 복사·평가 세트·중복은 버린다."""
    if per_strategy <= 0:
        raise ValueError(f"per_strategy 는 1 이상이다: {per_strategy}")
    if not categories:
        raise ValueError("categories 가 비었다")
    for seed in seeds:
        _seed_intensity(seed)  # 모르는 강도는 여기서 ValueError
    seed_texts = [seed["one_liner"] for seed in seeds if isinstance(seed.get("one_liner"), str)]
    blocked = [*seed_texts, *exclude_texts]

    results: list[dict] = []
    seen: set[str] = set()
    slot = 0
    for strategy in BanterStrategy:
        for intensity in intensities:
            assigned = [categories[(slot + i) % len(categories)] for i in range(per_strategy)]
            slot += per_strategy
            messages = _messages(
                strategy, intensity, assigned, _references(seeds, strategy, intensity)
            )
            try:
                result = await llm.structured_call(
                    role="writer",
                    messages=messages,
                    schema=CANDIDATE_SCHEMA,
                    timeout_s=timeout_s,
                    max_output_tokens=max_output_tokens,
                )
            except LLMError as exc:
                print(f"{strategy.value}/{intensity.value}: 호출 실패 {exc.kind}", file=sys.stderr)
                continue
            output = result.output if isinstance(result.output, Mapping) else {}
            items = output.get("candidates")
            if not isinstance(items, list):
                continue
            for index, item in enumerate(items[:per_strategy]):
                body = item.get("text") if isinstance(item, Mapping) else None
                if not isinstance(body, str) or is_copy(body, blocked):
                    continue
                key = normalize(body)
                if key in seen:
                    continue
                seen.add(key)
                body = body.strip()
                results.append(
                    {
                        "id": candidate_id(strategy.value, intensity.value, body),
                        "strategy": strategy.value,
                        "intensity": intensity.value,
                        "category": assigned[index],
                        "text": body,
                    }
                )
    return results


# --- 파일 --------------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name} {number}행: JSON 객체가 아니다")
        rows.append(value)
    return rows


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_texts(path: Path) -> list[str]:
    """평가 세트 문장. JSONL 이면 `text`·`one_liner`, 그 밖의 줄은 줄 전체."""
    texts: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            texts.append(stripped)
            continue
        if isinstance(value, dict):
            texts.extend(
                value[key] for key in ("text", "one_liner") if isinstance(value.get(key), str)
            )
        elif isinstance(value, str):
            texts.append(value)
        else:
            texts.append(stripped)
    return texts


# --- pair --------------------------------------------------------------------------

CSV_COLUMNS: tuple[str, ...] = (
    "pair_id",
    "strategy",
    "intensity",
    "category",
    "a_id",
    "a_text",
    "b_id",
    "b_text",
    "choice",
)


def build_pairs(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """같은 strategy·intensity 끼리 입력 순서대로 둘씩. 홀수 마지막은 b 빈칸."""
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for candidate in candidates:
        key = (str(candidate["strategy"]), str(candidate["intensity"]))
        groups.setdefault(key, []).append(candidate)

    pairs: list[dict[str, str]] = []
    for (strategy, intensity), members in groups.items():
        for number, start in enumerate(range(0, len(members), 2), start=1):
            a = members[start]
            b = members[start + 1] if start + 1 < len(members) else None
            pairs.append(
                {
                    "pair_id": f"{strategy}-{intensity}-p{number:02d}",
                    "strategy": strategy,
                    "intensity": intensity,
                    "category": str(a.get("category") or ""),
                    "a_id": str(a["id"]),
                    "a_text": str(a["text"]),
                    "b_id": str(b["id"]) if b else "",
                    "b_text": str(b["text"]) if b else "",
                    "choice": "",
                }
            )
    return pairs


def write_pairs_csv(path: Path, pairs: Iterable[Mapping[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for pair in pairs:
            writer.writerow({column: pair.get(column, "") for column in CSV_COLUMNS})


# --- import ------------------------------------------------------------------------

#: CSV 첫 데이터 행의 번호. 헤더가 1행이다.
FIRST_DATA_LINE = 2


@dataclass(frozen=True)
class RowError:
    line: int
    reason: str

    def __str__(self) -> str:
        return f"{self.line}행: {self.reason}"


def example_id(cand_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"banter-example:{cand_id}"))


def lexicon_violations(body: str, intensity: Intensity) -> list[str]:
    """`validation.py` 와 같은 부분 문자열 검사."""
    found: list[str] = []
    if applies(intensity, LexiconRule.DEATH_WORDS):
        found.extend(f"죽음어 {word}" for word in DEATH_WORDS if word in body)
    if applies(intensity, LexiconRule.WORN_PHRASES):
        found.extend(f"닳은 표현 {phrase}" for phrase in WORN_PHRASES if phrase in body)
    if applies(intensity, LexiconRule.PROFANITY):
        found.extend(f"욕 {word}" for word in PROFANITY if word in body)
    return found


def build_import_rows(
    csv_rows: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    version: int,
) -> tuple[list[dict[str, Any]], list[RowError]]:
    """선택된 문장 → INSERT 행. 오류는 CSV 행 번호와 함께 모은다."""
    by_id = {str(candidate["id"]): candidate for candidate in candidates}
    rows: dict[str, dict[str, Any]] = {}
    errors: list[RowError] = []

    for line, csv_row in enumerate(csv_rows, start=FIRST_DATA_LINE):
        choice = str(csv_row.get("choice") or "").strip().lower()
        if choice in ("", "none"):
            continue
        if choice not in ("a", "b"):
            errors.append(RowError(line, f"choice 는 a·b·none·빈칸이다: {choice!r}"))
            continue
        cand_id = str(csv_row.get(f"{choice}_id") or "").strip()
        if not cand_id:
            errors.append(RowError(line, f"{choice}_id 가 비었다"))
            continue
        candidate = by_id.get(cand_id)
        if candidate is None:
            errors.append(RowError(line, f"후보 JSONL 에 없는 id: {cand_id}"))
            continue
        body = str(candidate.get("text") or "").strip()
        csv_text = str(csv_row.get(f"{choice}_text") or "").strip()
        if csv_text != body:
            errors.append(RowError(line, f"CSV 문장이 후보 JSONL 과 다르다: {cand_id}"))
            continue
        try:
            strategy = BanterStrategy(str(candidate.get("strategy")))
            intensity = parse_intensity(str(candidate.get("intensity") or "").strip().lower())
        except ValueError as exc:
            errors.append(RowError(line, str(exc)))
            continue
        category = candidate.get("category") or None
        if category is not None and category not in CATEGORIES:
            errors.append(RowError(line, f"알 수 없는 카테고리: {category!r}"))
            continue
        violations = lexicon_violations(body, intensity)
        if violations:
            errors.append(RowError(line, f"{intensity.value} 금지 어휘 {violations}"))
            continue
        row_id = example_id(cand_id)
        rows[row_id] = {
            "id": row_id,
            "category": category,
            "strategy": strategy.value,
            "intensity": intensity.value,
            "text": body,
            "version": version,
        }
    return list(rows.values()), errors


INSERT_EXAMPLE_SQL = """
INSERT INTO ai.banter_examples (id, category, strategy, intensity, text, approved, version)
VALUES (CAST(:id AS uuid), :category, :strategy, :intensity, :text, true, :version)
ON CONFLICT (id) DO NOTHING
"""

COUNT_APPROVED_SQL = """
SELECT strategy, intensity, count(*) FROM ai.banter_examples
WHERE approved = true
GROUP BY strategy, intensity
"""


async def insert_rows(engine: AsyncEngine, rows: Sequence[Mapping[str, Any]]) -> int:
    """새로 넣은 행 수. 이미 있는 id 는 0."""
    inserted = 0
    async with engine.begin() as conn:
        for row in rows:
            result = await conn.execute(sql_text(INSERT_EXAMPLE_SQL), dict(row))
            inserted += result.rowcount or 0
    return inserted


async def count_approved(engine: AsyncEngine) -> dict[tuple[str, str], int]:
    async with engine.connect() as conn:
        result = await conn.execute(sql_text(COUNT_APPROVED_SQL))
        return {(str(row[0]), str(row[1])): int(row[2]) for row in result}


@dataclass(frozen=True)
class SummaryRow:
    label: str
    actual: int
    target: int

    @property
    def met(self) -> bool:
        return self.actual >= self.target


def summarize(counts: Mapping[tuple[str, str], int]) -> list[SummaryRow]:
    """06 §4.1 목표 대비. 총수, 전략 8종, 강도 3종 순."""
    rows = [SummaryRow("총수", sum(counts.values()), TARGET_TOTAL)]
    for strategy in BanterStrategy:
        actual = sum(n for (s, _), n in counts.items() if s == strategy.value)
        rows.append(SummaryRow(strategy.value, actual, TARGET_PER_STRATEGY))
    for intensity in ALL_INTENSITIES:
        actual = sum(n for (_, i), n in counts.items() if i == intensity.value)
        rows.append(SummaryRow(intensity.value, actual, TARGET_PER_INTENSITY))
    return rows


def format_summary(rows: Sequence[SummaryRow]) -> str:
    lines = ["| 항목 | 승인 | 목표 | 상태 |", "| --- | --- | --- | --- |"]
    for row in rows:
        state = "충족" if row.met else "미달"
        lines.append(f"| {row.label} | {row.actual} | ≥ {row.target} | {state} |")
    return "\n".join(lines)


# --- 명령줄 ------------------------------------------------------------------------


def _split(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="드립 예시 gen·pair·import(06 §3.6).")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("gen", help="서기 모델로 후보 JSONL 을 만든다")
    gen.add_argument("--per-strategy", type=int, required=True, help="전략·강도마다 후보 수 N")
    gen.add_argument("--intensities", help="mild,spicy,hell 중 쉼표 목록. 없으면 3종")
    gen.add_argument("--categories", help="카테고리 쉼표 목록. 없으면 Category 11종 순환")
    gen.add_argument("--seeds", type=Path, help="원문 시드 JSONL(proposal1 §9.2 모양)")
    gen.add_argument("--exclude", type=Path, help="평가 세트(JSONL 또는 한 줄 한 문장)")
    gen.add_argument("--out", type=Path, required=True, help="후보 JSONL 출력 경로")

    pair = sub.add_parser("pair", help="후보 JSONL 을 쌍대 선택 CSV 로 묶는다")
    pair.add_argument("--in", dest="input", type=Path, required=True, help="후보 JSONL")
    pair.add_argument("--out", type=Path, required=True, help="CSV 출력 경로")

    imp = sub.add_parser("import", help="선택된 문장을 ai.banter_examples 에 넣는다")
    imp.add_argument("--csv", type=Path, required=True, help="choice 를 채운 CSV")
    imp.add_argument("--candidates", type=Path, required=True, help="gen 이 만든 후보 JSONL")
    imp.add_argument("--version", type=int, default=1, help="예시 version. 기본 1(DDL 기본값)")
    imp.add_argument("--url", help="접속 문자열. 없으면 DATABASE_URL 을 쓴다.")
    return parser


def _default_llm() -> tuple[LLMPort, float, int] | None:
    from geoji_ai.adapters.openai_compat_llm import OpenAICompatLLM
    from geoji_ai.core.config import get_settings, secret_value

    settings = get_settings()
    api_key = secret_value(settings, "XAI_API_KEY")
    if not api_key.strip():
        print("XAI_API_KEY 가 비어 있다. .env 에 채운다.", file=sys.stderr)
        return None
    llm = OpenAICompatLLM(
        "xai", api_key=api_key, base_url=settings.XAI_BASE_URL, model_id=settings.MODEL_WRITER
    )
    return llm, float(settings.WRITER_NODE_TIMEOUT_SECONDS), settings.WRITER_MAX_OUTPUT_TOKENS


def _run_gen(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    llm: LLMPort | None,
    timeout_s: float | None,
    max_output_tokens: int | None,
) -> int:
    if args.per_strategy <= 0:
        parser.error("--per-strategy 는 1 이상이다")
    try:
        intensities = [parse_intensity(v.lower()) for v in _split(args.intensities)] or list(
            ALL_INTENSITIES
        )
    except ValueError as exc:
        parser.error(str(exc))
    categories = _split(args.categories) or list(CATEGORIES)
    unknown = [c for c in categories if c not in CATEGORIES]
    if unknown:
        parser.error(f"알 수 없는 카테고리: {unknown}")
    seeds = read_jsonl(args.seeds) if args.seeds else []
    exclude = load_texts(args.exclude) if args.exclude else []

    if llm is None:
        built = _default_llm()
        if built is None:
            return 2
        llm, default_timeout, default_tokens = built
        timeout_s = timeout_s if timeout_s is not None else default_timeout
        max_output_tokens = max_output_tokens if max_output_tokens is not None else default_tokens
    if timeout_s is None or max_output_tokens is None:
        raise ValueError("llm 을 주입할 때는 timeout_s·max_output_tokens 도 준다")

    candidates = asyncio.run(
        generate_candidates(
            llm,
            per_strategy=args.per_strategy,
            intensities=intensities,
            categories=categories,
            seeds=seeds,
            exclude_texts=exclude,
            timeout_s=timeout_s,
            max_output_tokens=max_output_tokens,
        )
    )
    write_jsonl(args.out, candidates)
    requested = len(BanterStrategy) * len(intensities) * args.per_strategy
    print(f"후보 {len(candidates)}/{requested}개 → {args.out}")
    return 0


def _run_pair(args: argparse.Namespace) -> int:
    pairs = build_pairs(read_jsonl(args.input))
    write_pairs_csv(args.out, pairs)
    print(f"쌍 {len(pairs)}행 → {args.out}")
    return 0


async def _import_async(url: str, rows: Sequence[Mapping[str, Any]]) -> tuple[int, str]:
    from geoji_ai.adapters.postgres_jobs import make_engine

    engine = make_engine(url)
    try:
        inserted = await insert_rows(engine, rows)
        counts = await count_approved(engine)
    finally:
        await engine.dispose()
    return inserted, format_summary(summarize(counts))


def _run_import(args: argparse.Namespace) -> int:
    with args.csv.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in CSV_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            print(f"CSV 헤더에 없는 컬럼: {missing}", file=sys.stderr)
            return 1
        csv_rows = list(reader)
    rows, errors = build_import_rows(csv_rows, read_jsonl(args.candidates), args.version)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"오류 {len(errors)}건. 아무것도 넣지 않았다.", file=sys.stderr)
        return 1

    url = args.url
    if not url:
        from geoji_ai.core.config import get_settings, secret_value

        url = secret_value(get_settings(), "DATABASE_URL")
    if not url.strip():
        # 접속 문자열은 비밀값이다. 이름만 말한다.
        print("DATABASE_URL 이 비어 있다. --url 로 주거나 .env 에 채운다.", file=sys.stderr)
        return 2
    inserted, table = asyncio.run(_import_async(url, rows))
    print(f"선택 {len(rows)}건, 새로 넣음 {inserted}건(version={args.version})")
    print(table)
    return 0


def main(
    argv: list[str] | None = None,
    *,
    llm: LLMPort | None = None,
    timeout_s: float | None = None,
    max_output_tokens: int | None = None,
) -> int:
    """`llm` 을 주면 gen 이 그것을 쓴다(테스트). 없으면 설정으로 xAI 어댑터를 만든다."""
    parser = build_parser()
    args = parser.parse_args(argv)
    match args.command:
        case "gen":
            return _run_gen(parser, args, llm, timeout_s, max_output_tokens)
        case "pair":
            return _run_pair(args)
        case "import":
            return _run_import(args)
    parser.error(f"알 수 없는 명령: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
