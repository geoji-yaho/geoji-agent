"""드립 예시 gen·pair·import 스크립트(06 §3.6, RM-07). 가짜 LLM 만 쓴다. DB 없음."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any, get_args

from geoji_ai.adapters.fake_llm import FakeLLM
from geoji_ai.contracts.intake import Category
from geoji_ai.contracts.writer import BanterStrategy
from geoji_ai.domain.intensity import ALL_INTENSITIES, Intensity
from geoji_ai.ports.llm import Cost, LLMPort, LLMResult, Usage

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

TIMEOUT_S = 6.0
MAX_TOKENS = 700
CATEGORIES = get_args(Category)


class CountingLLM:
    """호출마다 다른 문장을 돌려주는 스텁. 분포·순환 검사용."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def structured_call(
        self,
        *,
        role: str,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        max_output_tokens: int,
    ) -> LLMResult:
        self.calls.append({"role": role, "messages": messages})
        call = len(self.calls)
        texts = [{"text": f"호출{call:03d} 사건 문장 {i:02d}번"} for i in range(20)]
        return LLMResult(
            output={"candidates": texts},
            stop_reason="stop",
            usage=Usage(),
            cost=Cost(),
            provider_request_id=None,
            model_id="stub",
            vendor="stub",
            latency_ms=0,
        )


def _writer_output(*texts: str) -> dict:
    return {"writer": {"candidates": [{"text": text} for text in texts]}}


async def _gen(llm: LLMPort, **kwargs: Any) -> list[dict]:
    kwargs.setdefault("timeout_s", TIMEOUT_S)
    kwargs.setdefault("max_output_tokens", MAX_TOKENS)
    return await script.generate_candidates(llm, **kwargs)


# --- gen ---------------------------------------------------------------------------


async def test_gen_calls_writer_once_per_strategy_and_intensity_with_fake_llm() -> None:
    fake = FakeLLM(outputs=_writer_output("택시비로 버스 한 달 탄다", "걸어도 된다", "세 번째"))

    result = await _gen(fake, per_strategy=2)

    assert len(fake.calls) == len(BanterStrategy) * len(ALL_INTENSITIES)
    assert all(call.role == "writer" for call in fake.calls)
    assert all(call.schema == script.CANDIDATE_SCHEMA for call in fake.calls)
    assert all(call.timeout_s == TIMEOUT_S for call in fake.calls)
    assert all(call.max_output_tokens == MAX_TOKENS for call in fake.calls)
    # 요청 수 2 를 넘는 세 번째는 잘리고, 매 호출 같은 문장은 첫 호출 것만 남는다.
    assert [c["text"] for c in result] == ["택시비로 버스 한 달 탄다", "걸어도 된다"]


async def test_gen_distribution_is_strategies_times_intensities_times_n() -> None:
    llm = CountingLLM()

    result = await _gen(llm, per_strategy=3)

    assert len(result) == 8 * 3 * 3
    counts = Counter((c["strategy"], c["intensity"]) for c in result)
    assert set(counts) == {(s.value, i.value) for s in BanterStrategy for i in ALL_INTENSITIES}
    assert set(counts.values()) == {3}


async def test_gen_intensity_subset() -> None:
    result = await _gen(CountingLLM(), per_strategy=2, intensities=[Intensity.hell])

    assert len(result) == 8 * 1 * 2
    assert {c["intensity"] for c in result} == {"hell"}


async def test_gen_drops_seed_copies_after_normalization() -> None:
    seed_line = "그 돈이면 국밥이 두 그릇이다."
    fake = FakeLLM(
        outputs=_writer_output(
            "그 돈이면, 국밥이 두 그릇이다!!",  # 문장부호·공백만 다름
            "솔직히 그 돈이면 국밥이 두 그릇이다 ㅋㅋ",  # 시드를 포함
            "국밥이 두 그릇",  # 시드에 포함
            "편의점 도시락이면 사흘을 버틴다",  # 다른 사건
        )
    )
    seeds = [
        {
            "case_id": "seed-001",
            "one_liner": seed_line,
            "strategy": "CHEAPER_ALTERNATIVE",
            "intensity": "SPICY",  # proposal1 §9.2 는 대문자
            "source_type": "community",
        }
    ]

    result = await _gen(fake, per_strategy=4, seeds=seeds)

    assert [c["text"] for c in result] == ["편의점 도시락이면 사흘을 버틴다"]
    # 시드는 같은 전략·강도 호출의 참고 말투로만 들어간다.
    prompt = next(
        call.messages[1]["content"]
        for call in fake.calls
        if "CHEAPER_ALTERNATIVE" in call.messages[1]["content"]
        and "강도: spicy" in call.messages[1]["content"]
    )
    assert seed_line in prompt
    assert "다른 사건" in fake.calls[0].messages[0]["content"]


async def test_gen_drops_exclude_set_texts() -> None:
    fake = FakeLLM(outputs=_writer_output("평가 세트에 있는 문장이다", "새 문장이다"))

    result = await _gen(fake, per_strategy=2, exclude_texts=["평가 세트에 있는 문장이다."])

    assert [c["text"] for c in result] == ["새 문장이다"]


async def test_gen_cycles_category_enum_by_default() -> None:
    result = await _gen(CountingLLM(), per_strategy=4, intensities=[Intensity.mild])

    expected = [CATEGORIES[i % len(CATEGORIES)] for i in range(8 * 4)]
    assert [c["category"] for c in result] == expected


async def test_gen_uses_given_categories() -> None:
    result = await _gen(
        CountingLLM(), per_strategy=3, intensities=[Intensity.mild], categories=["식비", "배달"]
    )

    assert [c["category"] for c in result][:4] == ["식비", "배달", "식비", "배달"]


async def test_gen_candidate_id_is_deterministic_from_content() -> None:
    fake = FakeLLM(outputs=_writer_output("걸어서 갔으면 공짜다"))

    first = await _gen(fake, per_strategy=1)
    second = await _gen(FakeLLM(outputs=_writer_output("걸어서 갔으면 공짜다")), per_strategy=1)

    assert first[0]["id"] == second[0]["id"]
    assert first[0]["id"] == script.candidate_id(
        first[0]["strategy"], first[0]["intensity"], "걸어서 갔으면 공짜다"
    )
    assert first[0]["id"].startswith(f"{first[0]['strategy']}-{first[0]['intensity']}-")


def test_main_gen_then_pair_round_trip(tmp_path: Path) -> None:
    seeds = tmp_path / "seeds.jsonl"
    seeds.write_text(
        json.dumps(
            {
                "case_id": "seed-secret-id",
                "one_liner": "참고 말투",
                "strategy": "FREE_ALTERNATIVE",
                "intensity": "HELL",
                "source_type": "internal",
                "approved_by": "team",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "candidates.jsonl"
    pairs_csv = tmp_path / "pairs.csv"

    code = script.main(
        ["gen", "--per-strategy", "3", "--intensities", "mild,hell", "--seeds", str(seeds)]
        + ["--out", str(out)],
        llm=CountingLLM(),
        timeout_s=TIMEOUT_S,
        max_output_tokens=MAX_TOKENS,
    )
    assert code == 0
    raw = out.read_text(encoding="utf-8")
    candidates = [json.loads(line) for line in raw.splitlines()]
    assert len(candidates) == 8 * 2 * 3
    assert all(set(c) == {"id", "strategy", "intensity", "category", "text"} for c in candidates)
    assert "seed-secret-id" not in raw and "internal" not in raw

    assert script.main(["pair", "--in", str(out), "--out", str(pairs_csv)]) == 0
    with pairs_csv.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == script.CSV_COLUMNS
        rows = list(reader)
    # 그룹당 3개 → 2행(둘째 행은 b 빈칸)
    assert len(rows) == 8 * 2 * 2
    assert pairs_csv.read_bytes().startswith(b"\xef\xbb\xbf")


# --- pair --------------------------------------------------------------------------


def _cand(cid: str, strategy: str, intensity: str, text: str, category: str = "식비") -> dict:
    return {
        "id": cid,
        "strategy": strategy,
        "intensity": intensity,
        "category": category,
        "text": text,
    }


def test_pair_groups_by_strategy_intensity_and_leaves_odd_b_blank() -> None:
    candidates = [
        _cand("c1", "CHEAPER_ALTERNATIVE", "mild", "하나", "식비"),
        _cand("h1", "CHEAPER_ALTERNATIVE", "hell", "지옥 하나"),
        _cand("c2", "CHEAPER_ALTERNATIVE", "mild", "둘", "배달"),
        _cand("c3", "CHEAPER_ALTERNATIVE", "mild", "셋", "카페/간식"),
        _cand("h2", "CHEAPER_ALTERNATIVE", "hell", "지옥 둘"),
    ]

    pairs = script.build_pairs(candidates)

    assert all(tuple(p) == script.CSV_COLUMNS for p in pairs)
    assert [(p["a_id"], p["b_id"]) for p in pairs] == [("c1", "c2"), ("c3", ""), ("h1", "h2")]
    odd = pairs[1]
    assert odd["b_text"] == "" and odd["choice"] == ""
    assert pairs[0]["category"] == "식비"  # a 의 카테고리
    assert all(p["choice"] == "" for p in pairs)
    assert len({p["pair_id"] for p in pairs}) == 3


def test_write_pairs_csv_header_exact(tmp_path: Path) -> None:
    path = tmp_path / "p.csv"
    script.write_pairs_csv(
        path, script.build_pairs([_cand("x", "DIY_REPLACEMENT", "spicy", "쉼표, 포함")])
    )

    text = path.read_text(encoding="utf-8-sig")
    assert (
        text.splitlines()[0] == "pair_id,strategy,intensity,category,a_id,a_text,b_id,b_text,choice"
    )
    with path.open(encoding="utf-8-sig", newline="") as handle:
        assert next(csv.DictReader(handle))["a_text"] == "쉼표, 포함"


# --- import ------------------------------------------------------------------------


def _pair_row(a: dict, b: dict | None, choice: str) -> dict[str, str]:
    return {
        "pair_id": "p",
        "strategy": a["strategy"],
        "intensity": a["intensity"],
        "category": a["category"],
        "a_id": a["id"],
        "a_text": a["text"],
        "b_id": b["id"] if b else "",
        "b_text": b["text"] if b else "",
        "choice": choice,
    }


A = _cand("a1", "CHEAPER_ALTERNATIVE", "mild", "그 돈이면 버스를 한 달 탑니다")
B = _cand("b1", "CHEAPER_ALTERNATIVE", "mild", "택시 대신 따릉이도 있습니다")
C = _cand("c1", "EXCUSE_STRIPPING", "spicy", "피곤했다는 말은 핑계다", "교통/택시")
D = _cand("d1", "EXCUSE_STRIPPING", "spicy", "내일도 피곤할 거잖아", "교통/택시")


def test_import_selects_only_a_and_b_rows() -> None:
    csv_rows = [
        _pair_row(A, B, "a"),
        _pair_row(C, D, " B "),
        _pair_row(A, B, "none"),
        _pair_row(C, D, ""),
    ]

    rows, errors = script.build_import_rows(csv_rows, [A, B, C, D], version=2)

    assert errors == []
    assert [r["text"] for r in rows] == [A["text"], D["text"]]
    assert rows[1] == {
        "id": script.example_id("d1"),
        "category": "교통/택시",
        "strategy": "EXCUSE_STRIPPING",
        "intensity": "spicy",
        "text": D["text"],
        "version": 2,
    }


def test_import_rejects_unknown_choice_with_csv_line_number() -> None:
    csv_rows = [_pair_row(A, B, "a"), _pair_row(C, D, "none"), _pair_row(C, D, "둘다")]

    rows, errors = script.build_import_rows(csv_rows, [A, B, C, D], version=1)

    # 헤더 1행, 첫 데이터 행이 2행 → 세 번째 데이터 행은 4행
    assert [e.line for e in errors] == [4]
    assert str(errors[0]).startswith("4행:")
    assert len(rows) == 1


def test_import_rejects_death_words_and_worn_phrases_in_every_intensity() -> None:
    death = _cand("x1", "PREMISE_REJECTION", "hell", "이 돈 쓰고 죽어도 할 말 없다")
    worn = _cand("x2", "PREMISE_REJECTION", "mild", "정신 차리십시오 제발")
    ok = _cand("x3", "PREMISE_REJECTION", "hell", "이건 소비가 아니라 기부다")
    csv_rows = [_pair_row(ok, None, "a"), _pair_row(death, None, "a"), _pair_row(worn, None, "a")]

    rows, errors = script.build_import_rows(csv_rows, [death, worn, ok], version=1)

    assert [e.line for e in errors] == [3, 4]
    assert "죽어" in errors[0].reason
    assert "정신 차리십시오" in errors[1].reason
    assert [r["text"] for r in rows] == [ok["text"]]


def test_import_rejects_profanity_in_mild_and_spicy_but_allows_hell() -> None:
    mild = _cand("p1", "REPEAT_OFFENSE", "mild", "미친 소비네요")
    spicy = _cand("p2", "REPEAT_OFFENSE", "spicy", "또 샀냐 미친")
    hell = _cand("p3", "REPEAT_OFFENSE", "hell", "또 샀냐 미친 새끼야")
    csv_rows = [_pair_row(c, None, "a") for c in (mild, spicy, hell)]

    rows, errors = script.build_import_rows(csv_rows, [mild, spicy, hell], version=1)

    assert [e.line for e in errors] == [2, 3]
    assert all("미친" in e.reason for e in errors)
    assert [r["intensity"] for r in rows] == ["hell"]


def test_import_rejects_missing_id_blank_b_and_edited_text() -> None:
    edited = dict(_pair_row(A, B, "a"), a_text="엑셀에서 고친 문장")
    csv_rows = [
        dict(_pair_row(A, B, "a"), a_id="없는-id"),
        _pair_row(C, None, "b"),
        edited,
    ]

    rows, errors = script.build_import_rows(csv_rows, [A, B, C, D], version=1)

    assert rows == []
    assert [e.line for e in errors] == [2, 3, 4]


def test_import_row_ids_are_deterministic_uuid5() -> None:
    csv_rows = [_pair_row(A, B, "a"), _pair_row(C, D, "b")]

    first, _ = script.build_import_rows(csv_rows, [A, B, C, D], version=1)
    second, _ = script.build_import_rows(csv_rows, [A, B, C, D], version=1)

    assert [r["id"] for r in first] == [r["id"] for r in second]
    assert first[0]["id"] == "{}".format(script.example_id("a1"))
    from uuid import NAMESPACE_URL, uuid5

    assert first[0]["id"] == str(uuid5(NAMESPACE_URL, "banter-example:a1"))


def test_main_import_with_errors_exits_nonzero_without_db(tmp_path: Path, capsys) -> None:
    bad = _cand("bad", "REPEAT_OFFENSE", "mild", "지랄 났네")
    cands = tmp_path / "c.jsonl"
    script.write_jsonl(cands, [A, B, bad])
    pairs = tmp_path / "p.csv"
    script.write_pairs_csv(pairs, [_pair_row(A, B, "a"), _pair_row(bad, None, "a")])

    code = script.main(["import", "--csv", str(pairs), "--candidates", str(cands)])

    assert code == 1
    err = capsys.readouterr().err
    assert "3행:" in err
    assert "아무것도 넣지 않았다" in err


# --- 집계 --------------------------------------------------------------------------


def test_summarize_marks_shortfall() -> None:
    rows = script.summarize({("CHEAPER_ALTERNATIVE", "mild"): 5})

    labels = [r.label for r in rows]
    assert labels == ["총수", *[s.value for s in BanterStrategy], "mild", "spicy", "hell"]
    by_label = {r.label: r for r in rows}
    assert by_label["총수"].actual == 5 and not by_label["총수"].met
    assert by_label["CHEAPER_ALTERNATIVE"].met
    assert not by_label["FREE_ALTERNATIVE"].met
    assert not by_label["mild"].met  # 5 < 15
    assert "미달" in script.format_summary(rows)


def test_summarize_all_targets_met() -> None:
    counts = {(s.value, i.value): 3 for s in BanterStrategy for i in ALL_INTENSITIES}  # 72

    rows = script.summarize(counts)

    assert all(r.met for r in rows)
    assert {r.label: r.target for r in rows}["총수"] == 60
    assert {r.label: r.target for r in rows}["DIY_REPLACEMENT"] == 4
    assert {r.label: r.target for r in rows}["spicy"] == 15
    assert "미달" not in script.format_summary(rows)
