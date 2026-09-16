"""근거 발급(04 §3.4). DB 없음, 순수 함수.

`CaseSnapshot` + resolve-evidence 응답(10 §4.2) → `Dossier`.
순서는 F0(THIS_CASE) → AGGREGATE → RULE → PRIOR(recent_verdicts) → MEM(sources),
라벨은 잘라낸 뒤 `F0, F1, ...` 로 연속으로 붙인다. 총 개수 ≤ `pack_limit`.

- 항목 단위 숫자("택시 3회")는 만들지 않는다. 반복은 카테고리 단위 집계 1문장뿐이다
- `style_comments` 는 `ROOM_COMMENT_STYLE_ENABLED=false`(D-04)라 pack 에 넣지 않는다
- 백엔드 집계가 규칙과 어긋나면 AGGREGATE 를 넣지 않고 로그만 남긴다
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.domain.aggregation import CurrentCase, check_backend_aggregates, validate_aggregation
from geoji_ai.domain.visibility import Scope, Visibility, usable
from geoji_ai.ports.backend import (
    Aggregates,
    EvidenceScope,
    EvidenceSource,
    RecentVerdict,
    ResolveEvidenceResponse,
    RoomRule,
)
from geoji_ai.ports.preparation import EVIDENCE_TEXT_MAX, Dossier, EvidenceFact

__all__ = [
    "RuleMatcher",
    "build_evidence",
    "no_rule_match",
    "resolve_label",
    "snapshot_hash",
    "target_pack",
]

logger = logging.getLogger(__name__)

RuleMatcher = Callable[[str, RoomRule], bool]

# epistemic_type / fact_type (DDL 002 CHECK)
DB_RECORD = "DB_RECORD"
USER_CLAIM = "USER_CLAIM"
SPEND = "SPEND"
VERDICT = "VERDICT"
RULE = "RULE"
AGGREGATE = "AGGREGATE"

# F0 환산 표(사용자 9/14). 교통 카테고리만 지하철, 나머지는 아메리카노.
TRANSIT_CATEGORY = "교통/택시"
SUBWAY = ("지하철 기본요금", 1_400)
AMERICANO = ("아메리카노", 4_500)

# resolve-evidence `sources[].source_type` → fact_type (04 §3.3 source 열)
_SOURCE_FACT_TYPE = {"POST": SPEND, "VERDICT": VERDICT}


@dataclass(frozen=True)
class _Draft:
    """라벨 붙이기 전 근거."""

    epistemic_type: str
    fact_type: str
    text: str
    scope: Scope
    aggregation: dict[str, Any] | None
    occurred_at: datetime | None
    sources: tuple[tuple[str, str, int], ...]


def no_rule_match(category: str, rule: RoomRule) -> bool:
    """기본 RULE 매칭. 카테고리 키워드 사전은 작업 5 라 항상 False."""
    return False


def snapshot_hash(snapshot: CaseSnapshot) -> str:
    """`model_dump(mode="json")` 키 정렬 JSON 의 sha256 hex."""
    body = json.dumps(
        snapshot.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _clip(text: str) -> str:
    return text[:EVIDENCE_TEXT_MAX]


def _won(amount: int) -> str:
    return f"{amount:,}원"


def _quote(reason: str | None) -> str:
    return f"사유 '{reason}'" if reason else "사유 없음"


def _audience_scope(snapshot: CaseSnapshot) -> Scope:
    visibility = Visibility.PUBLIC if snapshot.audience.public_share_enabled else Visibility.ROOMS
    return Scope(visibility, frozenset(snapshot.audience.room_ids))


def _backend_scope(scope: EvidenceScope) -> Scope:
    return Scope(Visibility(scope.visibility), frozenset(scope.room_ids))


# --- F0 ------------------------------------------------------------------


def _this_case(snapshot: CaseSnapshot) -> _Draft:
    text = (
        f"이번 지출: {snapshot.item} {_won(snapshot.amount_krw)}, "
        f"카테고리 {snapshot.category}, {_quote(snapshot.reason)}."
    )
    name, unit = SUBWAY if snapshot.category == TRANSIT_CATEGORY else AMERICANO
    times = snapshot.amount_krw // unit
    if times >= 1:
        text += f" {name} {_won(unit)} 기준 약 {times}회분."
    return _Draft(
        epistemic_type=USER_CLAIM,
        fact_type=SPEND,
        text=_clip(text),
        scope=_audience_scope(snapshot),
        aggregation=None,
        occurred_at=snapshot.created_at,
        sources=(("POST", snapshot.post_id, snapshot.post_version),),
    )


# --- AGGREGATE -------------------------------------------------------------


def _aggregates(snapshot: CaseSnapshot, aggregates: Aggregates) -> list[_Draft]:
    current = CurrentCase(
        post_id=snapshot.post_id, category=snapshot.category, created_at=snapshot.created_at
    )
    meta = {
        "rule_version": aggregates.rule_version,
        "start_at": aggregates.window.start_at.isoformat(),
        "end_at": aggregates.window.end_at.isoformat(),
        "excludes_post_id": aggregates.excludes_post_id,
    }
    try:
        check_backend_aggregates(aggregates.model_dump(), current)
        validate_aggregation(meta)
    except ValueError as exc:
        logger.warning("백엔드 집계가 규칙과 어긋나 AGGREGATE 를 넣지 않는다: %s", exc)
        return []

    scope = _audience_scope(snapshot)
    status = (
        f"이번 달 예산 소진율 {round(aggregates.burn_rate * 100)}%, "
        f"티어 {aggregates.tier}, 무지출 {aggregates.no_spend_days}일."
    )
    repeat = f"최근 30일 같은 카테고리 확정 소비 {aggregates.repeat_same_category_30d}건."
    return [
        _Draft(DB_RECORD, AGGREGATE, _clip(text), scope, dict(meta), None, ())
        for text in (status, repeat)
    ]


# --- RULE ----------------------------------------------------------------


def _rules(snapshot: CaseSnapshot, rules: Iterable[RoomRule], matcher: RuleMatcher) -> list[_Draft]:
    return [
        _Draft(
            epistemic_type=DB_RECORD,
            fact_type=RULE,
            text=_clip(f"방 규칙 '{rule.text}'."),
            scope=Scope(Visibility.ROOMS, frozenset({rule.room_id})),
            aggregation=None,
            occurred_at=None,
            sources=((RULE, rule.rule_id, rule.version),),
        )
        for rule in rules
        if matcher(snapshot.category, rule)
    ]


# --- PRIOR / MEM -----------------------------------------------------------


def _prior(snapshot: CaseSnapshot, verdicts: Iterable[RecentVerdict]) -> list[_Draft]:
    others = [v for v in verdicts if v.post_id != snapshot.post_id]
    others.sort(key=lambda v: v.judged_at, reverse=True)
    drafts: list[_Draft] = []
    for v in others:
        sentence = f", 형량 {v.sentence}" if v.sentence is not None else ""
        text = (
            f"지난 판결({v.judged_at.month}/{v.judged_at.day}): {v.category} {_won(v.amount_krw)}, "
            f"{_quote(v.reason)}, 결과 {v.result}{sentence}."
        )
        drafts.append(
            _Draft(
                epistemic_type=DB_RECORD,
                fact_type=VERDICT,
                text=_clip(text),
                scope=_backend_scope(v.scope),
                aggregation=None,
                occurred_at=v.judged_at,
                sources=(("POST", v.post_id, v.post_version),),
            )
        )
    return drafts


def _source_text(fact_type: str, payload: Mapping[str, Any]) -> str:
    if fact_type == SPEND:
        parts = ["지난 지출:"]
        if payload.get("category"):
            parts.append(f"{payload['category']}")
        if isinstance(payload.get("amount_krw"), int):
            parts.append(_won(payload["amount_krw"]) + ",")
        parts.append(_quote(payload.get("reason")) + ".")
        return " ".join(parts)
    parts = ["지난 판결:"]
    if payload.get("result"):
        parts.append(f"결과 {payload['result']},")
    ratio = payload.get("guilty_ratio")
    if isinstance(ratio, int | float) and not isinstance(ratio, bool):
        parts.append(f"유죄 비율 {round(ratio * 100)}%,")
    if payload.get("sentence"):
        parts.append(f"형량 {payload['sentence']},")
    return " ".join(parts).rstrip(",:") + "."


def _memories(sources: Iterable[EvidenceSource]) -> list[_Draft]:
    drafts: list[_Draft] = []
    for source in sources:
        fact_type = _SOURCE_FACT_TYPE.get(source.source_type)
        if fact_type is None:
            logger.info("PRIOR/MEM 으로 옮기지 않는 source_type: %s", source.source_type)
            continue
        drafts.append(
            _Draft(
                epistemic_type=DB_RECORD,
                fact_type=fact_type,
                text=_clip(_source_text(fact_type, source.payload)),
                scope=_backend_scope(source.scope),
                aggregation=None,
                occurred_at=None,
                sources=((source.source_type, source.source_id, source.source_version),),
            )
        )
    return drafts


# --- 조립 ------------------------------------------------------------------


def build_evidence(
    snapshot: CaseSnapshot,
    resolved: ResolveEvidenceResponse,
    *,
    pack_limit: int,
    rule_matcher: RuleMatcher = no_rule_match,
) -> Dossier:
    """근거 pack 을 만든다. 상한을 넘으면 뒤(PRIOR/MEM)부터 잘린다."""
    if pack_limit < 1:
        raise ValueError(f"pack_limit 은 1 이상이어야 한다: {pack_limit}")
    drafts = [
        _this_case(snapshot),
        *_aggregates(snapshot, resolved.aggregates),
        *_rules(snapshot, resolved.room_rules, rule_matcher),
        *_prior(snapshot, resolved.recent_verdicts),
        *_memories(resolved.sources),
    ][:pack_limit]

    facts: list[EvidenceFact] = []
    label_map: dict[str, str] = {}
    for index, draft in enumerate(drafts):
        label = f"F{index}"
        label_map[label] = str(uuid.uuid4())
        facts.append(
            EvidenceFact(
                label=label,
                epistemic_type=draft.epistemic_type,
                fact_type=draft.fact_type,
                text=draft.text,
                scope=draft.scope,
                aggregation=draft.aggregation,
                occurred_at=draft.occurred_at,
                sources=draft.sources,
            )
        )
    return Dossier(
        dossier_id=str(uuid.uuid4()),
        post_id=snapshot.post_id,
        snapshot_hash=snapshot_hash(snapshot),
        facts=tuple(facts),
        label_map=label_map,
        privacy_versions=tuple((pv.scope_key, pv.epoch) for pv in snapshot.privacy_versions),
    )


def resolve_label(label_map: Mapping[str, str], label: str) -> str | None:
    """라벨 → evidence UUID. map 밖이면 None(근거 없음)."""
    return label_map.get(label)


def target_pack(dossier: Dossier, target_room_ids: Iterable[str]) -> tuple[EvidenceFact, ...]:
    """target 방들에 가는 문구에 쓸 수 있는 근거만(`visibility.usable`)."""
    target = frozenset(target_room_ids)
    return tuple(fact for fact in dossier.facts if usable(fact.scope, target))
