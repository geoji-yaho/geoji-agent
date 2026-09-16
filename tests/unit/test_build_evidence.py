"""근거 발급 `build_evidence`(04 §3.4). DB 없음, 순수 함수."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

from geoji_ai.application.build_evidence import build_evidence, target_pack
from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.domain.aggregation import validate_aggregation
from geoji_ai.domain.visibility import Scope, Visibility
from geoji_ai.ports.backend import ResolveEvidenceResponse

ROOT = Path(__file__).resolve().parents[2]
ROOM_A = "room-a"
ROOM_B = "room-b"


def load_snapshot(**update: Any) -> CaseSnapshot:
    """`case-snapshot-taxi.json`. `update` 는 최상위 필드 덮어쓰기."""
    data = json.loads((ROOT / "contracts/fixtures/case-snapshot-taxi.json").read_text("utf-8"))
    data.update(update)
    return CaseSnapshot.model_validate(data)


def make_resolved(snapshot: CaseSnapshot, **update: Any) -> ResolveEvidenceResponse:
    """현재 사건 기준으로 규칙대로 센 가짜 resolve-evidence 응답."""
    data: dict[str, Any] = {
        "sources": [],
        "aggregates": {
            "burn_rate": 0.41,
            "tier": "상거지",
            "no_spend_days": 3,
            "repeat_same_category_30d": 2,
            "excludes_post_id": snapshot.post_id,
            "window": {
                "start_at": (snapshot.created_at - timedelta(days=30)).isoformat(),
                "end_at": snapshot.created_at.isoformat(),
            },
            "rule_version": 1,
        },
        "room_rules": [],
        "recent_verdicts": [],
        "style_comments": [],
    }
    data.update(update)
    return ResolveEvidenceResponse.model_validate(data)


def rule(room_id: str, rule_id: str = "rule-1", text: str = "한 달에 택시 1번") -> dict[str, Any]:
    return {"room_id": room_id, "rule_id": rule_id, "version": 1, "text": text}


def always_match(category: str, room_rule: Any) -> bool:
    return True


# ⑥ 먼저 실패시킬 케이스 --------------------------------------------------------


def test_06_A방_전용_RULE_은_target_AB_pack_에서_빠진다():
    snapshot = load_snapshot(
        audience={
            "room_ids": [ROOM_A, ROOM_B],
            "audience_version": 1,
            "public_share_enabled": False,
        }
    )
    resolved = make_resolved(snapshot, room_rules=[rule(ROOM_A)])
    dossier = build_evidence(snapshot, resolved, pack_limit=12, rule_matcher=always_match)

    rules = [f for f in dossier.facts if f.fact_type == "RULE"]
    assert len(rules) == 1
    assert rules[0].scope.visibility is Visibility.ROOMS
    assert rules[0].scope.room_ids == frozenset({ROOM_A})

    pack_ab = target_pack(dossier, {ROOM_A, ROOM_B})
    assert all(f.fact_type != "RULE" for f in pack_ab)
    assert any(f.label == "F0" for f in pack_ab)

    pack_a = target_pack(dossier, {ROOM_A})
    assert any(f.fact_type == "RULE" for f in pack_a)


# ① ~ ⑤, ⑦, ⑧ ------------------------------------------------------------------


def verdict(post_id: str, judged_at: str, *, room: str = ROOM_A) -> dict[str, Any]:
    return {
        "post_id": post_id,
        "post_version": 1,
        "category": "교통/택시",
        "amount_krw": 9500,
        "reason": "늦잠",
        "result": "guilty",
        "sentence": "oneDay",
        "judged_at": judged_at,
        "scope": {"visibility": "ROOMS", "room_ids": [room]},
    }


def test_non_guilty_recent_verdict_accepts_null_sentence_without_rendering_none():
    snapshot = load_snapshot()
    prior = verdict("prior-post", snapshot.created_at.isoformat())
    prior.update(result="notGuilty", sentence=None)
    resolved = make_resolved(snapshot, recent_verdicts=[prior])
    dossier = build_evidence(snapshot, resolved, pack_limit=12)
    fact = next(f for f in dossier.facts if f.fact_type == "VERDICT")
    assert "결과 notGuilty." in fact.text
    assert "형량" not in fact.text
    assert "None" not in fact.text


def test_ambiguous_room_rule_indices_are_not_enabled_by_default():
    """Q5: source tuple에 room_id가 없으므로 현재 default matcher는 RULE을 발급하지 않는다."""
    snapshot = load_snapshot()
    resolved = make_resolved(
        snapshot,
        room_rules=[rule(ROOM_A, "0", "택시 금지"), rule(ROOM_B, "0", "택시 허용")],
    )
    dossier = build_evidence(snapshot, resolved, pack_limit=12)
    assert all(f.fact_type != "RULE" for f in dossier.facts)


def source(source_type: str, source_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_type": source_type,
        "source_id": source_id,
        "source_version": 1,
        "payload": payload,
        "scope": {"visibility": "PRIVATE", "room_ids": []},
    }


def test_01_F0_은_항상_첫_라벨이고_USER_CLAIM():
    snapshot = load_snapshot()
    dossier = build_evidence(snapshot, make_resolved(snapshot), pack_limit=12)
    f0 = dossier.facts[0]
    assert f0.label == "F0"
    assert f0.epistemic_type == "USER_CLAIM"
    assert f0.fact_type == "SPEND"
    assert f0.scope == Scope(Visibility.ROOMS, frozenset(snapshot.audience.room_ids))
    assert f0.sources == (("POST", snapshot.post_id, snapshot.post_version),)
    assert "12,000원" in f0.text
    assert "늦잠 자서 택시 탐" in f0.text

    # pack_limit 1 이어도 F0 만 남는다
    only = build_evidence(snapshot, make_resolved(snapshot), pack_limit=1)
    assert [f.label for f in only.facts] == ["F0"]

    public = load_snapshot(
        audience={"room_ids": [ROOM_A], "audience_version": 1, "public_share_enabled": True}
    )
    public_f0 = build_evidence(public, make_resolved(public), pack_limit=12).facts[0]
    assert public_f0.scope.visibility is Visibility.PUBLIC


def test_02_교통택시는_지하철_환산_그_외는_아메리카노():
    taxi = load_snapshot()
    taxi_text = build_evidence(taxi, make_resolved(taxi), pack_limit=12).facts[0].text
    assert "지하철 기본요금 1,400원 기준 약 8회분" in taxi_text
    assert "아메리카노" not in taxi_text

    food = load_snapshot(category="식비", item="치킨", amount_krw=20000)
    food_text = build_evidence(food, make_resolved(food), pack_limit=12).facts[0].text
    assert "아메리카노 4,500원 기준 약 4회분" in food_text
    assert "지하철" not in food_text

    cheap = load_snapshot(category="식비", item="껌", amount_krw=1000)
    cheap_text = build_evidence(cheap, make_resolved(cheap), pack_limit=12).facts[0].text
    assert "기준" not in cheap_text


def test_03_AGGREGATE_2개와_메타_4키():
    snapshot = load_snapshot()
    dossier = build_evidence(snapshot, make_resolved(snapshot), pack_limit=12)
    aggregates = [f for f in dossier.facts if f.fact_type == "AGGREGATE"]
    assert [f.label for f in aggregates] == ["F1", "F2"]
    for fact in aggregates:
        assert fact.epistemic_type == "DB_RECORD"
        assert set(fact.aggregation) == {"rule_version", "start_at", "end_at", "excludes_post_id"}
        assert fact.aggregation["excludes_post_id"] == snapshot.post_id
        validate_aggregation(fact.aggregation)
    assert "소진율 41%" in aggregates[0].text
    assert aggregates[1].text == "최근 30일 같은 카테고리 확정 소비 2건."


def test_03_excludes_post_id_가_현재_post_가_아니면_AGGREGATE_없음(caplog):
    snapshot = load_snapshot()
    resolved = make_resolved(snapshot)
    bad = resolved.model_copy(
        update={"aggregates": resolved.aggregates.model_copy(update={"excludes_post_id": "other"})}
    )
    with caplog.at_level("WARNING"):
        dossier = build_evidence(snapshot, bad, pack_limit=12)
    assert all(f.fact_type != "AGGREGATE" for f in dossier.facts)
    assert [f.label for f in dossier.facts] == ["F0"]
    assert "AGGREGATE" in caplog.text


def test_04_기본_rule_matcher_는_RULE_0개():
    snapshot = load_snapshot()
    resolved = make_resolved(snapshot, room_rules=[rule(ROOM_A), rule(ROOM_B, "rule-2")])
    dossier = build_evidence(snapshot, resolved, pack_limit=12)
    assert all(f.fact_type != "RULE" for f in dossier.facts)


def test_04_True_주입이면_ROOMS_room_id_RULE():
    snapshot = load_snapshot()
    resolved = make_resolved(snapshot, room_rules=[rule(ROOM_A), rule(ROOM_B, "rule-2")])
    dossier = build_evidence(snapshot, resolved, pack_limit=12, rule_matcher=always_match)
    rules = [f for f in dossier.facts if f.fact_type == "RULE"]
    assert [f.scope for f in rules] == [
        Scope(Visibility.ROOMS, frozenset({ROOM_A})),
        Scope(Visibility.ROOMS, frozenset({ROOM_B})),
    ]
    assert [f.sources for f in rules] == [(("RULE", "rule-1", 1),), (("RULE", "rule-2", 1),)]
    assert all(f.epistemic_type == "DB_RECORD" for f in rules)


def test_05_상한_초과면_뒤_PRIOR_MEM_부터_잘린다():
    snapshot = load_snapshot()
    resolved = make_resolved(
        snapshot,
        room_rules=[rule(ROOM_A)],
        recent_verdicts=[
            verdict("p-old", "2026-09-01T00:00:00Z"),
            verdict(snapshot.post_id, "2026-09-07T09:00:00Z"),  # 현재 post 는 뺀다
            verdict("p-new", "2026-09-05T00:00:00Z"),
        ],
        sources=[
            source("POST", "p-mem", {"category": "교통/택시", "amount_krw": 8000, "reason": "비"}),
            source("VERDICT", "v-mem", {"result": "guilty", "guilty_ratio": 0.8}),
        ],
    )
    full = build_evidence(snapshot, resolved, pack_limit=12, rule_matcher=always_match)
    assert [f.fact_type for f in full.facts] == [
        "SPEND",
        "AGGREGATE",
        "AGGREGATE",
        "RULE",
        "VERDICT",
        "VERDICT",
        "SPEND",
        "VERDICT",
    ]
    assert [f.sources[0][1] for f in full.facts[3:]] == [
        "rule-1",
        "p-new",
        "p-old",
        "p-mem",
        "v-mem",
    ]

    cut = build_evidence(snapshot, resolved, pack_limit=5, rule_matcher=always_match)
    assert [f.fact_type for f in cut.facts] == [
        "SPEND",
        "AGGREGATE",
        "AGGREGATE",
        "RULE",
        "VERDICT",
    ]
    assert cut.facts[-1].sources == (("POST", "p-new", 1),)
    assert len(cut.label_map) == 5


def test_07_style_comments_가_있어도_pack_에_없다():
    snapshot = load_snapshot()
    resolved = make_resolved(
        snapshot,
        style_comments=[
            {
                "comment_id": "c-1",
                "room_id": ROOM_A,
                "content": "택시비로 적금을 들었으면 벌써 부자",
                "created_at": "2026-09-06T00:00:00Z",
            }
        ],
    )
    dossier = build_evidence(snapshot, resolved, pack_limit=12)
    assert all("적금" not in f.text for f in dossier.facts)
    assert all(src[0] != "COMMENT" for f in dossier.facts for src in f.sources)
    assert len(dossier.facts) == 3


def test_08_항목_단위_숫자_문장이_없다():
    snapshot = load_snapshot()
    resolved = make_resolved(
        snapshot,
        recent_verdicts=[verdict("p-old", "2026-09-01T00:00:00Z")],
        sources=[source("POST", "p-mem", {"category": "교통/택시", "amount_krw": 8000})],
    )
    dossier = build_evidence(snapshot, resolved, pack_limit=12)
    # 품목어 뒤 횟수("택시 3회", "택시를 3번"). 환산 "약 N회분" 은 항목 횟수가 아니라 뺀다
    pattern = re.compile(rf"({re.escape(snapshot.item)}|택시)\S*\s*[\d,]+\s*(회|번|건)(?!분)")
    assert not [f.text for f in dossier.facts if pattern.search(f.text)]
    assert all(len(f.text) <= 500 for f in dossier.facts)
