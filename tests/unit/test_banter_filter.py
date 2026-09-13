"""드립 필터 4규칙(05 §3.2 `validate_banter`)과 조서 fact 병합."""

from __future__ import annotations

from geoji_ai.application.build_evidence import build_evidence
from geoji_ai.contracts.writer import BanterStrategy
from geoji_ai.domain.intensity import Intensity
from geoji_ai.domain.visibility import Scope, Visibility
from geoji_ai.graphs.preparation import filter_banter, merge_inferred_facts
from geoji_ai.graphs.states import Candidate
from tests.unit.test_build_evidence import load_snapshot, make_resolved

LABELS = ("F0", "F1", "F2")


def _c(
    text: str = "지하철이 있었잖아요.",
    strategy: BanterStrategy = BanterStrategy.PREMISE_REJECTION,
    labels: tuple[str, ...] = (),
) -> Candidate:
    return Candidate(
        candidate_id="c",
        text=text,
        strategy=strategy,
        fits=("guilty",),
        evidence_labels=labels,
    )


def test_깨끗한_후보는_그대로():
    candidate = _c(labels=("F1",))
    assert filter_banter([candidate], Intensity.spicy, LABELS) == [candidate]


def test_규칙1_REPEAT_OFFENSE_ROOM_RULE_CALLBACK_라벨_없으면_삭제():
    candidates = [
        _c(strategy=BanterStrategy.REPEAT_OFFENSE),
        _c(strategy=BanterStrategy.ROOM_RULE_CALLBACK),
        _c(strategy=BanterStrategy.REPEAT_OFFENSE, labels=("F2",)),
    ]
    kept = filter_banter(candidates, Intensity.mild, LABELS)
    assert [(c.strategy, c.evidence_labels) for c in kept] == [
        (BanterStrategy.REPEAT_OFFENSE, ("F2",))
    ]


def test_규칙2_없는_라벨은_지우고_그_뒤_필수_전략이_비면_삭제():
    kept = filter_banter(
        [
            _c(labels=("F1", "F9")),
            _c(strategy=BanterStrategy.ROOM_RULE_CALLBACK, labels=("F7",)),
        ],
        Intensity.spicy,
        LABELS,
    )
    assert [c.evidence_labels for c in kept] == [("F1",)]


def test_규칙3_DEATH_WORDS_는_모든_강도에서_삭제():
    for intensity in Intensity:
        assert filter_banter([_c(text="통장이 아니라 네가 죽어")], intensity, LABELS) == []


def test_규칙4_PROFANITY_는_mild_spicy_에서만_삭제():
    rude = _c(text="미친 소비입니다")
    assert filter_banter([rude], Intensity.mild, LABELS) == []
    assert filter_banter([rude], Intensity.spicy, LABELS) == []
    assert filter_banter([rude], Intensity.hell, LABELS) == [rude]


def test_merge_inferred_facts_는_라벨_밖_fact_와_허용_밖_kind_를_지운다():
    snapshot = load_snapshot()
    dossier = build_evidence(snapshot, make_resolved(snapshot), pack_limit=12)
    base = len(dossier.facts)
    output = {
        "facts": [
            {"kind": "RULE_HIT", "text": "규칙 적중", "source_refs": ["F0", "F1"]},
            {"kind": "MITIGATION", "text": "없는 라벨", "source_refs": ["F99"]},
            {"kind": "MITIGATION", "text": "참조 없음", "source_refs": []},
            {"kind": "PATTERN", "text": "허용 밖", "source_refs": ["F0"]},
            {"kind": "REASON_ANALYSIS", "text": "사유 분석", "source_refs": ["F0"]},
        ],
        "reason_analysis": {
            "has_mitigation": False,
            "mitigation_kind": None,
            "injection_suspected": False,
        },
    }

    merged, analysis = merge_inferred_facts(dossier, output)

    added = merged.facts[base:]
    assert [(f.label, f.epistemic_type, f.fact_type, f.text) for f in added] == [
        (f"F{base}", "MODEL_INFERENCE", "RULE", "규칙 적중")
    ]
    # F0(ROOMS 방) ∩ F1(집계, 같은 방) → ROOMS 같은 방
    assert added[0].scope == Scope(Visibility.ROOMS, frozenset(snapshot.audience.room_ids))
    assert added[0].sources == dossier.facts[0].sources + dossier.facts[1].sources
    assert set(merged.label_map) == {f.label for f in merged.facts}
    assert analysis is not None and analysis["facts"] == ["사유 분석"]
    assert analysis["has_mitigation"] is False


def test_merge_inferred_facts_출력_없음이면_그대로():
    snapshot = load_snapshot()
    dossier = build_evidence(snapshot, make_resolved(snapshot), pack_limit=12)
    assert merge_inferred_facts(dossier, None) == (dossier, None)
