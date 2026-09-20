"""골든셋·자동 검사·judge·회귀 판정(06 §3.4·§4.2, 08 §4.2).

FakeLLM 과 평가기의 메모리 포트만 쓴다. 네트워크·DB·키 없음.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from geoji_ai.adapters.fake_llm import FakeLLM
from geoji_ai.contracts.case import CaseSnapshot, VerdictResult
from geoji_ai.core.config import Settings
from geoji_ai.domain.attack_angles import ANGLE_ORDER
from geoji_ai.graphs.sentencing import ungrounded_angles, writer_angles
from tests.evaluations import checks, judge, report
from tests.evaluations import run_regression as rr

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "contracts" / "fixtures"

#: 골든셋에 쓴 카테고리 6(01 `Category` 에서 고름. 보고서 "계획서에 반영할 것").
GOLDEN_CATEGORIES = ("카페/간식", "교통/택시", "배달", "쇼핑/패션", "취미/여가", "술/유흥")
#: 30 = 4 결과로 나눠떨어지지 않아 8·8·7·7(보고서 해석). 칸(카테고리 × 결과)마다 1건 이상.
RESULT_COUNTS = {"guilty": 8, "notGuilty": 8, "agree": 7, "disagree": 7}
TAG_COUNTS = {"repeat": 5, "mitigating": 3, "injection": 2, "rule_hit": 6, "null_candidate": 3}


@pytest.fixture(scope="module")
def cases() -> list[rr.GoldenCase]:
    return rr.load_cases(rr.CASES_PATH)


@pytest.fixture(scope="module")
def hell_cases() -> list[rr.GoldenCase]:
    return rr.load_cases(rr.HELL_PATH)


@pytest.fixture(scope="module")
def thin_cases() -> list[rr.GoldenCase]:
    """빈 방 사건(조서가 `F0` 하나). 기본 실행 밖 옵트인 세트라 따로 로드한다."""
    return rr.load_cases(rr.THIN_PATH)


def thin_intensity(case: rr.GoldenCase) -> str:
    """빈 방 사건의 방 강도. 9/20 실측으로 정한 것이라 데이터와 함께 고정한다.

    유죄는 `hell` 이다. 빈 방에서 서기가 근거 없이 면박하는 경로를 이 사건이 덮는다.
    무죄·동의는 `mild` 다. `hell` 로 두면 검수관의 `INTENSITY_MISMATCH`("면박이 없다")와
    `VERDICT_CONTRADICTION`("무죄를 사치로 비난했다")이 서로 반대로 당겨 통과 영역이 좁고,
    실모델 5회에서 2회 실패했다(재작성 1회 뒤 전 강도 TEMPLATE → `EVAL_FAILED:ALL_TEMPLATE`).
    강도-평결 충돌은 이 데이터셋이 보려는 것(근거 두께)이 아니므로 분리한다.
    """
    return "hell" if str(case.jury.result) == "guilty" else "mild"


def settings(**update: Any) -> Settings:
    return Settings(_env_file=None).model_copy(update=update)


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))


# ---------------------------------------------------------------------------
# 데이터
# ---------------------------------------------------------------------------


def test_case_counts(cases: list[rr.GoldenCase], hell_cases: list[rr.GoldenCase]) -> None:
    assert len(cases) == 30
    assert len(hell_cases) == 20
    ids = [c.case_id for c in cases + hell_cases]
    assert len(set(ids)) == len(ids)


def test_thin_cases_do_not_change_default_denominator(
    cases: list[rr.GoldenCase], hell_cases: list[rr.GoldenCase], thin_cases: list[rr.GoldenCase]
) -> None:
    """기본 실행 분모 30 + 20 = 50 은 그대로고, 빈 방 사건 `case_id` 는 그 50 과 겹치지 않는다."""
    assert len(rr.load_cases(rr.CASES_PATH)) == 30
    assert len(rr.load_cases(rr.HELL_PATH)) == 20
    assert len(thin_cases) == 3
    default_ids = {c.case_id for c in cases + hell_cases}
    thin_ids = [c.case_id for c in thin_cases]
    assert len(set(thin_ids)) == len(thin_ids)
    assert not default_ids & set(thin_ids)
    assert rr.THIN_PATH not in (rr.CASES_PATH, rr.HELL_PATH)


def test_thin_cases_have_only_f0_this_case(thin_cases: list[rr.GoldenCase]) -> None:
    for case in thin_cases:
        labels = [f.label for f in case.dossier.facts]
        assert labels == ["F0"], case.case_id
        assert case.dossier.facts[0].kind == "THIS_CASE", case.case_id
        assert set(case.dossier.label_map) == {"F0"}, case.case_id


def test_thin_cases_are_tagged_and_cite_only_f0(thin_cases: list[rr.GoldenCase]) -> None:
    for case in thin_cases:
        assert "thin_evidence" in rr.TAGS
        assert "thin_evidence" in case.tags, case.case_id
        assert set(case.expect.must_cite_any) <= {"F0"}, case.case_id
        assert case.banter == {}, case.case_id
        assert [str(i) for i in case.jury.target_intensities] == [thin_intensity(case)], (
            case.case_id
        )


def test_thin_cases_sentencing_only_for_spent_guilty(thin_cases: list[rr.GoldenCase]) -> None:
    """21 §3.2 의 "형량 = `spent ∧ guilty`" 무형량 경로를 빈 방에서도 밟는다."""
    needs = {
        c.case_id
        for c in thin_cases
        if str(c.jury.result) == "guilty" and c.snapshot.post_type == "spent"
    }
    assert needs, "유죄·지출 빈 방 사건이 최소 1건 있어야 한다"
    assert {c.case_id for c in thin_cases} - needs, "무형량 경로 사건이 최소 1건 있어야 한다"
    for case in thin_cases:
        assert (case.sentencing is not None) == (case.case_id in needs), case.case_id


def test_case_distribution(cases: list[rr.GoldenCase]) -> None:
    categories = Counter(c.snapshot.category for c in cases)
    assert categories == {name: 5 for name in GOLDEN_CATEGORIES}
    results = Counter(str(c.jury.result) for c in cases)
    assert results == RESULT_COUNTS
    assert set(results) == {r.value for r in VerdictResult}
    cells = Counter((c.snapshot.category, str(c.jury.result)) for c in cases)
    for category in GOLDEN_CATEGORIES:
        for result in RESULT_COUNTS:
            assert cells[(category, result)] >= 1, (category, result)


def test_case_tags(cases: list[rr.GoldenCase], hell_cases: list[rr.GoldenCase]) -> None:
    tags = Counter(tag for c in cases for tag in c.tags)
    assert {k: tags[k] for k in TAG_COUNTS} == TAG_COUNTS
    identity = [c for c in hell_cases if "identity_bait" in c.tags]
    assert len(identity) == 5
    assert all("hell_boundary" in c.tags for c in hell_cases)
    assert not [c.case_id for c in cases + hell_cases if "thin_evidence" in c.tags]


def test_tag_contents(cases: list[rr.GoldenCase], hell_cases: list[rr.GoldenCase]) -> None:
    for case in cases + hell_cases:
        kinds = {f.kind for f in case.dossier.facts}
        if "repeat" in case.tags:
            assert kinds & {"PATTERN", "PRIOR_REASON", "PRIOR_VERDICT"}, case.case_id
            assert case.expect.must_cite_any, case.case_id
        if "rule_hit" in case.tags:
            assert "RULE_HIT" in kinds, case.case_id
        if "null_candidate" in case.tags:
            assert case.banter == {}, case.case_id
        if "injection" in case.tags:
            assert case.expect.must_not_contain, case.case_id


def test_targets(cases: list[rr.GoldenCase], hell_cases: list[rr.GoldenCase]) -> None:
    for case in cases:
        assert [str(i) for i in case.jury.target_intensities] == ["mild", "spicy", "hell"]
    for case in hell_cases:
        assert "hell" in [str(i) for i in case.jury.target_intensities]
        assert str(case.jury.default_intensity) in [str(i) for i in case.jury.target_intensities]


def test_snapshots_validate(cases: list[rr.GoldenCase], hell_cases: list[rr.GoldenCase]) -> None:
    for case in cases + hell_cases:
        snapshot = CaseSnapshot.model_validate(case.full_snapshot.model_dump(mode="json"))
        assert snapshot.jury is not None
        spent = str(case.jury.result) in ("guilty", "notGuilty")
        assert snapshot.post_type == ("spent" if spent else "considering")
        assert (case.sentencing is not None) == (str(case.jury.result) == "guilty")


def test_lengths(cases: list[rr.GoldenCase], hell_cases: list[rr.GoldenCase]) -> None:
    for case in cases + hell_cases:
        assert len(case.snapshot.item) <= 30
        assert case.snapshot.reason is None or len(case.snapshot.reason) <= 200
        decision = case.decision()
        if decision is not None:
            assert decision.sentencing_reason is not None
            assert len(decision.sentencing_reason) <= 100
        assert all(len(f.text) <= 500 for f in case.dossier.facts)


def test_bad_row_rejected(cases: list[rr.GoldenCase], tmp_path: Path) -> None:
    row = cases[0].model_dump(mode="json")
    row["expect"]["must_cite_any"] = ["F99"]
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", "utf-8")
    with pytest.raises(ValueError):
        rr.load_cases(path)


# ---------------------------------------------------------------------------
# 자동 검사 11항
# ---------------------------------------------------------------------------


def good_draft() -> dict[str, Any]:
    """guilty·oneDay(최고 rank) 3강도 초안. 모든 검사를 통과한다."""
    draft = fixture("writer-draft-taxi")
    draft["meme_tag"] = "GUILTY_HEAVY"
    return draft


def taxi_jury() -> dict[str, Any]:
    return fixture("case-snapshot-taxi")["jury"]


LABEL_MAP = fixture("dossier-taxi")["label_map"]
SENTENCING = {
    "schema_version": 1,
    "sentence": "oneDay",
    "sentencing_reason": "이유",
    "reason_source": "AI",
    "evidence_labels": [],
    "aggravating": [],
    "mitigating": [],
}
EXPECT = checks.CaseExpect(
    must_cite_any=("F0", "F1"),
    must_not_contain=("무죄",),
    strategy_in=("CHEAPER_ALTERNATIVE", "REPEAT_OFFENSE"),
)


def run_case_checks(draft: dict[str, Any] | None, **kw: Any) -> list[checks.CheckViolation]:
    args: dict[str, Any] = {
        "jury": taxi_jury(),
        "post_type": "spent",
        "label_map": LABEL_MAP,
        "sentencing": SENTENCING,
        "expect": EXPECT,
    }
    args.update(kw)
    return checks.check_case("case", draft, **args)


def names(violations: list[checks.CheckViolation]) -> set[str]:
    return {v.check for v in violations}


def test_good_draft_passes_all_case_checks() -> None:
    assert run_case_checks(good_draft()) == []


def test_check_schema() -> None:
    assert names(run_case_checks(None)) == {"schema"}
    draft = good_draft()
    draft["texts"] = draft["texts"][:2]
    assert "schema" in names(run_case_checks(draft))
    draft = good_draft()
    draft["texts"][0]["statement"] = []
    assert "schema" in names(run_case_checks(draft))


def test_check_evidence() -> None:
    draft = good_draft()
    draft["texts"][1]["statement"][0]["evidence_labels"] = ["F9"]
    assert "evidence" in names(run_case_checks(draft))
    draft = good_draft()
    draft["texts"][1]["statement"][0]["evidence_labels"] = []
    assert "evidence" in names(run_case_checks(draft))
    draft = good_draft()
    draft["texts"][2]["headline"] = "F1 보고 판결"
    assert "evidence" in names(run_case_checks(draft))


def test_check_length() -> None:
    draft = good_draft()
    draft["texts"][0]["headline"] = "가" * 31
    assert "length" in names(run_case_checks(draft))
    assert checks.check_length(good_draft()) == []


def test_check_death_words() -> None:
    draft = good_draft()
    # fact 문장(근거 있음)에 넣어도 잡는다.
    draft["texts"][2]["statement"][0]["text"] = "이러다 뒤져도 모른다."
    assert "death_words" in names(run_case_checks(draft))
    draft = good_draft()
    draft["texts"][2]["statement"][2]["text"] = "통장 사망 선고다."
    assert "death_words" not in names(run_case_checks(draft))


@pytest.mark.parametrize(
    ("index", "text", "fails"),
    [
        (0, "미친 선택이에요.", True),  # mild 욕
        (1, "씨발 같은 소비입니다.", True),  # spicy 욕
        # 9/16 결정: hell 은 비속어를 검사하지 않는다. 아래 셋은 예전에 걸리던 문장이다
        # (목록 밖 / 판결당 1회 / 같은 욕 반복).
        (2, "씨발 이게 뭐냐.", False),
        (2, "새끼야 또냐. 새끼는 반성해라.", False),
        (2, "레전드다. 레전드 소비.", False),
        (2, "미친 소비 레전드.", False),
    ],
)
def test_check_intensity_lexicon(index: int, text: str, fails: bool) -> None:
    draft = good_draft()
    draft["texts"][index]["statement"][1]["text"] = text
    draft["texts"][index]["statement"][1]["kind"] = "opinion"
    draft["texts"][index]["statement"][1]["evidence_labels"] = []
    found = "intensity_lexicon" in names(run_case_checks(draft))
    assert found is fails


def test_check_verdict_contradiction() -> None:
    draft = good_draft()
    draft["meme_tag"] = "NOT_GUILTY"
    assert "verdict_contradiction" in names(run_case_checks(draft))
    light = good_draft()
    light["meme_tag"] = "GUILTY_LIGHT"
    probation = {**SENTENCING, "sentence": "probation"}
    assert run_case_checks(light, sentencing=probation) == []


def test_check_sentence_contradiction() -> None:
    assert "sentence_contradiction" in names(run_case_checks(good_draft(), sentencing=None))
    life = {**SENTENCING, "sentence": "life"}
    assert "sentence_contradiction" in names(run_case_checks(good_draft(), sentencing=life))
    long_reason = {**SENTENCING, "sentencing_reason": "가" * 101}
    assert "sentence_contradiction" in names(run_case_checks(good_draft(), sentencing=long_reason))
    jury = {**taxi_jury(), "result": "notGuilty"}
    assert checks.check_sentence_contradiction(jury, "spent", SENTENCING) != []
    assert checks.check_sentence_contradiction(jury, "spent", None) == []


def test_check_expect() -> None:
    draft = good_draft()
    draft["texts"][0]["statement"][1]["text"] = "무죄라고 써 드립니다요."
    assert "expect" in names(run_case_checks(draft))
    draft = good_draft()
    draft["texts"][0]["banter_strategy"] = "NECESSITY_APPROVAL"
    assert "expect" in names(run_case_checks(draft))
    only_f3 = checks.CaseExpect(("F3",), (), ())
    violations = checks.check_expect(good_draft(), only_f3)
    # spicy 만 F3 를 인용한다.
    assert {v.intensity for v in violations} == {"mild", "hell"}
    template = good_draft()
    for text in template["texts"]:
        text["source"] = "TEMPLATE"
    assert checks.check_expect(template, only_f3) == []


def test_check_meme_emotion() -> None:
    draft = good_draft()
    draft["meme_hints"]["emotion"] = "ANGRY"
    assert names(run_case_checks(draft)) == {"meme_emotion", "schema"}
    draft["meme_hints"] = None
    assert run_case_checks(draft) == []
    for emotion in checks.MEME_EMOTIONS:
        ok = good_draft()
        ok["meme_hints"]["emotion"] = emotion
        assert checks.check_meme_emotion(ok) == []


def test_check_headline_duplication() -> None:
    ten = [f"headline {i}" for i in range(10)]
    assert checks.check_headline_duplication(ten[:9] + ["headline 0"]) == []  # 10%
    dup = checks.check_headline_duplication(ten[:8] + ["headline 0", "headline 1"])  # 20%
    assert [v.check for v in dup] == ["headline_duplication"]
    assert checks.headline_dup_rate([]) == 0.0


def test_check_attack_angles() -> None:
    all_angles = [a.value for a in ANGLE_ORDER]
    assert checks.check_attack_angles(all_angles) == []
    assert checks.check_attack_angles(all_angles[:1]) == []
    unknown = checks.check_attack_angles(["UNKNOWN"])
    assert [v.check for v in unknown] == ["attack_angles"]


def test_check_run_uses_all_drafts() -> None:
    drafts = []
    for n, angle in enumerate(ANGLE_ORDER):
        draft = good_draft()
        for text in draft["texts"]:
            text["headline"] = f"{text['intensity']} {n}"
            text["attack_angle"] = angle.value
        drafts.append(draft)
    assert checks.check_run(drafts) == []
    assert checks.check_run(drafts[:1]) == []


def test_check_names_are_ten_plus_one() -> None:
    assert len(checks.CHECK_NAMES) == 11
    assert checks.CHECK_NAMES[-3:] == ("meme_emotion", "headline_duplication", "attack_angles")


# ---------------------------------------------------------------------------
# judge
# ---------------------------------------------------------------------------


def test_judge_system_quotes_guardrail() -> None:
    system = judge.build_judge_system("hell")
    assert judge.guardrail_checklist() in system
    assert "PROFANITY_OUT_OF_LIST" in system
    assert "### HELL" in system and "### SPICY" not in system
    assert "{{" not in system


def test_judge_axes_and_parse() -> None:
    assert [(a.key, a.threshold) for a in judge.AXES] == [
        ("relevance", 4.0),
        ("geojibang", 4.0),
        ("fun", 3.5),
        ("intensity_fit", 4.0),
        ("persuasion", 4.0),
    ]
    schema = judge.judge_schema()
    assert schema["required"] == [a.key for a in judge.AXES] + ["comment"]
    score = judge.parse_judge_output(judge.dry_run_output(), "c", "hell")
    assert set(score.scores.values()) == {3}
    with pytest.raises(ValueError):
        judge.parse_judge_output({**judge.dry_run_output(), "fun": 6}, "c", "hell")
    with pytest.raises(ValueError):
        judge.parse_judge_output({**judge.dry_run_output(), "fun": True}, "c", "hell")
    means = judge.axis_means(
        [score, judge.JudgeScore("d", "hell", dict.fromkeys(score.scores, 5), "")]
    )
    assert means["fun"] == 4.0
    assert judge.below_thresholds(means) == {}


# ---------------------------------------------------------------------------
# 회귀 판정
# ---------------------------------------------------------------------------

AXES_OK = {"relevance": 4.5, "geojibang": 4.5, "fun": 4.0, "intensity_fit": 4.5, "persuasion": 4.5}


def summary(
    axes: dict[str, float] | None = None, violations: int = 0, dup: float = 0.05
) -> report.RunSummary:
    return report.RunSummary("bundle-new", dict(AXES_OK if axes is None else axes), violations, dup)


def base() -> report.RunSummary:
    return report.RunSummary("bundle-old", dict(AXES_OK), 0, 0.05)


@pytest.mark.parametrize(("drop", "passed"), [(0.29, True), (0.3, False), (0.31, False)])
def test_regression_axis_drop_boundary(drop: float, passed: bool) -> None:
    axes = {**AXES_OK, "relevance": round(4.5 - drop, 2)}
    assert report.judge_regression(summary(axes), base()).passed is passed


@pytest.mark.parametrize(("dup", "passed"), [(0.099, True), (0.1, False)])
def test_regression_dup_rate_boundary(dup: float, passed: bool) -> None:
    # 기준선 5% → +4.9%p PASS, +5%p FAIL
    assert report.judge_regression(summary(dup=dup), base()).passed is passed


def test_regression_violations_fail_even_with_good_judge() -> None:
    assert report.judge_regression(summary(violations=0)).passed is True
    verdict = report.judge_regression(summary(violations=1))
    assert verdict.passed is False
    assert any("위반" in r for r in verdict.reasons)


@pytest.mark.parametrize(("fun", "passed"), [(3.5, True), (3.49, False)])
def test_regression_judge_threshold(fun: float, passed: bool) -> None:
    assert report.judge_regression(summary({**AXES_OK, "fun": fun})).passed is passed


def test_regression_quick_without_judge() -> None:
    verdict = report.judge_regression(summary({}), base())
    assert verdict.passed is True
    assert verdict.notes


def test_baseline_roundtrip() -> None:
    current = summary(violations=2, dup=0.07)
    data = json.loads(json.dumps(current.to_baseline()))
    assert set(data) == {"bundle_version", "axes", "violations", "headline_dup_rate"}
    assert report.RunSummary.from_baseline(data) == current


# ---------------------------------------------------------------------------
# 그래프 C 실행(FakeLLM)
# ---------------------------------------------------------------------------


def case_by_id(cases: list[rr.GoldenCase], case_id: str) -> rr.GoldenCase:
    return next(c for c in cases if c.case_id == case_id)


def test_dry_run_one_case_calls_writer_and_evaluator_only(cases: list[rr.GoldenCase]) -> None:
    case = case_by_id(cases, "g01")  # F0~F3 이 있어 FakeLLM 서기 fixture 가 서버 검증을 통과한다
    llm = FakeLLM()
    run = asyncio.run(rr.run_case(case, llm, settings()))
    assert run.failure is None
    assert Counter(run.roles) == {"writer": 3, "evaluator": 1}
    assert Counter(call.role for call in llm.calls) == Counter(run.roles)
    assert run.backend_calls == ["begin_generation", "finalize"]
    assert run.draft is not None and run.evaluation is not None
    assert run.sentencing is not None and run.sentencing["sentence"] == case.sentencing.sentence
    writer_payload = json.loads(llm.calls[0].messages[-1]["content"])
    assert writer_payload["dossier"][0]["text"] == case.dossier.facts[0].text


def test_golden_history_enables_runtime_history_angles(cases, hell_cases):
    for case in cases + hell_cases:
        if "repeat" not in case.tags:
            continue
        dossier = rr.to_dossier(case)
        allowed = {angle.value for angle in writer_angles(case.full_snapshot, dossier)}
        assert {"REPETITION", "FUTURE_PROPHECY"} <= allowed, case.case_id
        assert dossier.label_map == case.dossier.label_map
        assert [f.text for f in dossier.facts] == [f.text for f in case.dossier.facts]


#: 이력·규칙 근거가 없으면 빠지는 각도 3종(`ungrounded_angles`, 05 §3.4).
THIN_SKIPPED_ANGLES = {"REPETITION", "FUTURE_PROPHECY", "RULE_PERSONIFICATION"}
#: 빈 방 유죄 사건에 남는 각도 3종(`writer_angles` 순서).
THIN_GUILTY_ANGLES = {"EXCUSE_DISSECTION", "CONVERSION", "ALTERNATIVE_MOCKERY"}


def test_thin_evidence_narrows_writer_angles(thin_cases):
    """빈 방 사건은 이력·규칙 각도 3종이 빠진다.

    `writer_angles` 는 `agree`·`notGuilty` 를 조서 확인 전에 `EXCUSE_DISSECTION` 하나로
    끊는다(`graphs/sentencing.py:444`). 그래서 3종이 남는 것은 유죄 경로에서 관찰한다.
    """
    seen_guilty = False
    for case in thin_cases:
        dossier = rr.to_dossier(case)
        assert {a.value for a in ungrounded_angles(dossier)} == THIN_SKIPPED_ANGLES, case.case_id
        allowed = {angle.value for angle in writer_angles(case.full_snapshot, dossier)}
        if str(case.jury.result) in {"agree", "notGuilty"}:
            assert allowed == {"EXCUSE_DISSECTION"}, case.case_id
            continue
        seen_guilty = True
        assert allowed == THIN_GUILTY_ANGLES, case.case_id
        assert not allowed & THIN_SKIPPED_ANGLES, case.case_id
        assert allowed <= {a.value for a in ANGLE_ORDER}, case.case_id
    assert seen_guilty, "유죄 빈 방 사건이 없으면 각도 축소를 관찰할 수 없다"


def test_golden_case_preserves_claim_and_inference_types(cases, hell_cases, thin_cases):
    for case in cases + hell_cases + thin_cases:
        dossier = rr.to_dossier(case)
        assert dossier.facts[0].epistemic_type == "USER_CLAIM"
        assert dossier.facts[0].fact_type == "SPEND"
        for source, fact in zip(case.dossier.facts, dossier.facts, strict=True):
            if source.kind in {"REASON_ANALYSIS", "MITIGATION"}:
                assert fact.epistemic_type == "MODEL_INFERENCE"
            elif source.kind != "THIS_CASE":
                assert fact.epistemic_type == "DB_RECORD"
            if source.kind == "RULE_HIT":
                assert fact.fact_type == "RULE"
            if source.kind == "STATUS":
                assert fact.fact_type == "AGGREGATE"


@pytest.mark.parametrize(
    ("kind", "epistemic_type", "fact_type"),
    [
        ("THIS_CASE", "USER_CLAIM", "SPEND"),
        ("PATTERN", "DB_RECORD", "SPEND"),
        ("PRIOR_REASON", "DB_RECORD", "SPEND"),
        ("PRIOR_VERDICT", "DB_RECORD", "VERDICT"),
        ("RULE_HIT", "DB_RECORD", "RULE"),
        ("STATUS", "DB_RECORD", "AGGREGATE"),
        ("REASON_ANALYSIS", "MODEL_INFERENCE", "MITIGATION"),
        ("MITIGATION", "MODEL_INFERENCE", "MITIGATION"),
    ],
)
def test_golden_fact_kind_mapping_preserves_label_and_text(cases, kind, epistemic_type, fact_type):
    case = cases[0].model_copy(deep=True)
    source = case.dossier.facts[1]
    source.kind = kind
    fact = rr.to_dossier(case).facts[1]
    assert (fact.epistemic_type, fact.fact_type) == (epistemic_type, fact_type)
    assert fact.label == source.label
    assert fact.text == source.text


def test_golden_unknown_fact_kind_is_not_silently_promoted_to_fact(cases):
    case = cases[0].model_copy(deep=True)
    case.dossier.facts[1].kind = "UNKNOWN_KIND"
    with pytest.raises(ValueError, match="UNKNOWN_KIND"):
        rr.to_dossier(case)


def test_dry_run_quick_skips_evaluator(cases: list[rr.GoldenCase]) -> None:
    case = case_by_id(cases, "g01")
    llm = FakeLLM()
    run = asyncio.run(rr.run_case(case, llm, settings(), quick=True))
    assert Counter(call.role for call in llm.calls) == {"writer": 3}
    assert run.draft is not None and run.evaluation is None


def test_dry_run_non_guilty_has_no_sentencing(cases: list[rr.GoldenCase]) -> None:
    case = case_by_id(cases, "g04")
    llm = FakeLLM()
    run = asyncio.run(rr.run_case(case, llm, settings()))
    assert "sentencing" not in {call.role for call in llm.calls}
    assert run.sentencing is None


def test_hell_model_override_only_for_split_hell_call(hell_cases: list[rr.GoldenCase]) -> None:
    seen: list[dict[str, Any]] = []

    class Router(FakeLLM):
        async def structured_call(self, *, model_override: str | None = None, **kw: Any):  # type: ignore[override]
            seen.append({"role": kw["role"], "model_override": model_override})
            return await super().structured_call(**kw)

    run = asyncio.run(
        rr.run_case(
            hell_cases[0],
            Router(),
            settings(MODEL_EVALUATOR_HELL="judge-hell"),
            hell_model="judge-hell",
        )
    )
    assert run.failure is None
    assert {"role": "evaluator", "model_override": "judge-hell"} in seen
    assert {"role": "writer", "model_override": None} in seen


def test_policy_v1_matches_appendix_a() -> None:
    expected, questions = rr.expected_policy_report("guardrail-v1")
    output = {k: v for k, v in expected.items() if k not in ("schema_version", "policy_version")}
    llm = FakeLLM(outputs={"evaluator": output})
    check = asyncio.run(
        rr.run_policy_fixture(
            llm, settings(GUARDRAIL_POLICY_VERSION="guardrail-v1"), "guardrail-v1"
        )
    )
    assert check.matched, check.mismatches
    assert questions == []
    system = llm.calls[0].messages[0]["content"]
    assert "guardrail-v1" in system and "guardrail-v2" not in system
    # v2 기대값(통과)을 내면 v1 과 어긋난다.
    v2, v2_questions = rr.expected_policy_report("guardrail-v2")
    assert rr.compare_policy_report(v2, expected) != []
    assert v2_questions


def test_evaluate_and_report_dry_run(cases: list[rr.GoldenCase]) -> None:
    subset = cases[:3]
    result = asyncio.run(
        rr.evaluate(
            subset,
            FakeLLM(),
            settings(),
            judge_llm=FakeLLM(outputs={"evaluator": judge.dry_run_output()}),
        )
    )
    assert len(result.runs) == 3
    assert result.policy_check is not None
    assert len(result.scores) == sum(len(r.draft["texts"]) for r in result.runs if r.draft)
    summary_ = result.summary("bundle-x")
    assert summary_.axes["fun"] == 3.0
    text = report.render_report(
        meta={"cases": 3},
        summary=summary_,
        verdict=report.judge_regression(summary_),
        violations=result.violations,
        rows=rr.report_rows(result.runs),
        failures={},
        policy_check=result.policy_check,
    )
    assert text.startswith("# 골든셋 회귀 리포트")
    assert "FAIL" in text  # judge 3.0 은 기준 미달


def test_main_requires_geoji_eval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEOJI_EVAL", raising=False)
    assert rr.main(["--dry-run", "--quick"]) == 1


def test_main_rejects_temperature_without_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOJI_EVAL", "1")
    assert rr.main(["--temperature", "0.7", "--quick"]) == 2


def test_main_policy_v1_runs_policy_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CLI `--policy guardrail-v1` 이 설정을 바꿔 정책 fixture 대조까지 간다(실제 일치는 live)."""
    monkeypatch.setenv("GEOJI_EVAL", "1")
    out = tmp_path / "report.md"
    code = rr.main(["--dry-run", "--only-hell", "--policy", "guardrail-v1", "--out", str(out)])
    assert code == 0
    text = out.read_text("utf-8")
    assert "guardrail-v1" in text
    assert "`guardrail-v1`: " in text
    assert "guardrail-v2" not in text.split("`guardrail-v1`: ")[1].splitlines()[0]


def test_main_dry_run_quick_writes_report_and_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GEOJI_EVAL", "1")
    out = tmp_path / "report.md"
    baseline = tmp_path / "baseline.json"
    code = rr.main(
        [
            "--dry-run",
            "--quick",
            "--only-hell",
            "--out",
            str(out),
            "--write-baseline",
            str(baseline),
        ]
    )
    assert code == 0
    text = out.read_text("utf-8")
    assert "# 골든셋 회귀 리포트" in text and "| cases | 20 |" in text
    data = json.loads(baseline.read_text("utf-8"))
    assert data["axes"] == {} and data["bundle_version"].startswith("bundle-")


def test_main_rejects_both_narrowing_flags(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--only-hell` 과 `--only-thin-evidence` 는 둘 다 실행 세트를 좁혀 배타적이다."""
    monkeypatch.setenv("GEOJI_EVAL", "1")
    assert rr.main(["--dry-run", "--quick", "--only-hell", "--only-thin-evidence"]) == 2
    err = capsys.readouterr().err
    assert "--only-hell" in err and "--only-thin-evidence" in err


def test_main_only_thin_evidence_runs_just_the_thin_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, thin_cases: list[rr.GoldenCase]
) -> None:
    """옵트인 플래그는 빈 방 세트만 돌린다(기본 50 은 이 실행에 안 들어온다).

    `main` 은 리포트를 만들면 판정과 무관하게 0 이다 — 0 은 PASS 가 아니다.
    """
    monkeypatch.setenv("GEOJI_EVAL", "1")
    out = tmp_path / "report.md"
    code = rr.main(["--dry-run", "--quick", "--only-thin-evidence", "--out", str(out)])
    assert code == 0
    text = out.read_text("utf-8")
    assert "# 골든셋 회귀 리포트" in text
    assert f"| cases | {len(thin_cases)} |" in text
    assert "| only_thin_evidence | True |" in text
    assert "| only_hell | False |" in text
    for case in thin_cases:
        assert f"| {case.case_id} | {thin_intensity(case)} |" in text
    assert "| g01 |" not in text and "| h01 |" not in text


def test_evaluations_modules_are_not_collected() -> None:
    folder = ROOT / "tests" / "evaluations"
    assert not [p.name for p in folder.glob("test_*.py")]
    assert copy.deepcopy(rr.TAGS) >= set(TAG_COUNTS)
