"""dossier·trial_prep 저장 어댑터(04 §3.4·§3.5, 05 §3.2·§3.3).

`ports.preparation.PreparationPort` 의 Postgres 구현. 한 트랜잭션 안에서
`ai.privacy_epochs` 를 읽어 스냅샷 epoch 와 비교하고, 어긋나면 아무것도 쓰지 않고
`EvidenceInvalidated` 를 올린다(트랜잭션 롤백). 일치하면 `ai.dossiers` →
`ai.evidence` → `ai.evidence_sources` 순으로 INSERT 한다.

`save_prep` 은 `ai.trial_prep` 을 `ON CONFLICT (post_id, input_hash, prompt_version) DO NOTHING`
으로 먼저 넣는다. 충돌이면 dossier 를 쓰지 않고 기존 행을 돌려준다(완료분 불변, 동시 처리에도 1행).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.types import Text

from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.contracts.writer import BanterStrategy
from geoji_ai.domain.input_hash import input_hash
from geoji_ai.domain.intensity import Intensity, parse_intensity
from geoji_ai.domain.privacy import mismatched_scopes
from geoji_ai.domain.visibility import Scope, Visibility
from geoji_ai.graphs.states import Candidate
from geoji_ai.ports.preparation import (
    Dossier,
    EvidenceFact,
    EvidenceInvalidated,
    PrepSaveResult,
    ValidPrep,
)

__all__ = ["PostgresPreparation"]

# 04 §3.5 원문.
EPOCH_SQL = "SELECT scope_key, epoch FROM ai.privacy_epochs WHERE scope_key = ANY(:keys)"

INSERT_DOSSIER_SQL = """
INSERT INTO ai.dossiers (id, post_id, snapshot_hash, label_map, privacy_versions)
VALUES (:id, :post_id, :snapshot_hash, CAST(:label_map AS jsonb), CAST(:privacy_versions AS jsonb))
"""

INSERT_EVIDENCE_SQL = """
INSERT INTO ai.evidence (
  id, dossier_id, label, epistemic_type, fact_type, text, scope, aggregation, occurred_at
) VALUES (
  :id, :dossier_id, :label, :epistemic_type, :fact_type, :text,
  CAST(:scope AS jsonb), CAST(:aggregation AS jsonb), :occurred_at
)
"""

INSERT_SOURCE_SQL = """
INSERT INTO ai.evidence_sources (evidence_id, source_type, source_id, source_version)
VALUES (:evidence_id, :source_type, :source_id, :source_version)
"""

INSERT_PREP_SQL = """
INSERT INTO ai.trial_prep (
  id, post_id, post_version, audience_version, rules_version, privacy_versions,
  prompt_version, input_hash, status, dossier_id
) VALUES (
  :id, :post_id, :post_version, :audience_version, NULL, CAST(:privacy_versions AS jsonb),
  :prompt_version, :input_hash, 'DOSSIER_READY', :dossier_id
)
ON CONFLICT (post_id, input_hash, prompt_version) DO NOTHING
RETURNING CAST(id AS text) AS id
"""

SELECT_PREP_KEY_SQL = """
SELECT CAST(id AS text) AS id, status FROM ai.trial_prep
WHERE post_id = :post_id AND input_hash = :input_hash AND prompt_version = :prompt_version
"""

UPDATE_BANTER_SQL = """
UPDATE ai.trial_prep SET banter_json = CAST(:banter AS jsonb), status = 'COMPLETE'
WHERE id = CAST(:id AS uuid) AND banter_json IS NULL AND status = 'DOSSIER_READY'
"""

# 05 §3.3 조건 중 SQL 로 거르는 것. privacy_versions 비교는 파이썬에서 한다(jsonb 배열 순서 무관).
SELECT_VALID_PREP_SQL = """
SELECT CAST(tp.id AS text) AS prep_id, tp.banter_json,
       CAST(d.id AS text) AS dossier_id, d.post_id, d.snapshot_hash, d.label_map,
       d.privacy_versions
FROM ai.trial_prep tp
JOIN ai.dossiers d ON d.id = tp.dossier_id
WHERE tp.post_id = :post_id AND tp.input_hash = :input_hash
  AND tp.prompt_version = :prompt_version
  AND tp.status IN ('DOSSIER_READY', 'COMPLETE')
  AND tp.invalidated_at IS NULL AND d.invalidated_at IS NULL
"""

SELECT_EVIDENCE_SQL = """
SELECT CAST(id AS text) AS id, label, epistemic_type, fact_type, text, scope, aggregation,
       occurred_at
FROM ai.evidence
WHERE dossier_id = CAST(:dossier_id AS uuid) AND invalidated_at IS NULL
ORDER BY CAST(substr(label, 2) AS int)
"""

SELECT_SOURCES_SQL = """
SELECT CAST(s.evidence_id AS text) AS evidence_id, s.source_type, s.source_id, s.source_version
FROM ai.evidence_sources s JOIN ai.evidence e ON e.id = s.evidence_id
WHERE e.dossier_id = CAST(:dossier_id AS uuid)
ORDER BY s.source_type, s.source_id, s.source_version
"""

SELECT_EXAMPLES_SQL = """
SELECT text FROM ai.banter_examples
WHERE approved = true AND intensity = :intensity
ORDER BY (category IS NOT DISTINCT FROM :category) DESC, version DESC, created_at DESC NULLS LAST
LIMIT :limit
"""


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json(value: Any) -> Any:
    """asyncpg 는 jsonb 를 문자열로 줄 때가 있다."""
    if isinstance(value, str | bytes):
        return json.loads(value)
    return value


def _scope_json(scope: Scope) -> str:
    return _dumps({"visibility": str(scope.visibility), "room_ids": sorted(scope.room_ids)})


def _privacy_json(pairs: tuple[tuple[str, int], ...]) -> str:
    return _dumps([{"scope_key": k, "epoch": e} for k, e in pairs])


def _evidence_params(dossier_id: uuid.UUID, evidence_id: uuid.UUID, fact: EvidenceFact) -> dict:
    return {
        "id": evidence_id,
        "dossier_id": dossier_id,
        "label": fact.label,
        "epistemic_type": fact.epistemic_type,
        "fact_type": fact.fact_type,
        "text": fact.text,
        "scope": _scope_json(fact.scope),
        "aggregation": None if fact.aggregation is None else _dumps(fact.aggregation),
        "occurred_at": fact.occurred_at,
    }


def _banter_json(banter: Mapping[Intensity, list[Candidate]]) -> str:
    return _dumps(
        {
            str(intensity): [
                {
                    "candidate_id": c.candidate_id,
                    "text": c.text,
                    "strategy": str(c.strategy),
                    "fits": list(c.fits),
                    "evidence_labels": list(c.evidence_labels),
                }
                for c in candidates
            ]
            for intensity, candidates in banter.items()
        }
    )


def _parse_banter(raw: Any) -> dict[Intensity, list[Candidate]]:
    data = _json(raw)
    if not isinstance(data, Mapping):
        return {}
    return {
        parse_intensity(key): [
            Candidate(
                candidate_id=str(item["candidate_id"]),
                text=str(item["text"]),
                strategy=BanterStrategy(item["strategy"]),
                fits=tuple(item.get("fits") or ()),
                evidence_labels=tuple(item.get("evidence_labels") or ()),
            )
            for item in items
        ]
        for key, items in data.items()
    }


def _privacy_pairs(raw: Any) -> set[tuple[str, int]]:
    return {(str(item["scope_key"]), int(item["epoch"])) for item in _json(raw) or []}


class PostgresPreparation:
    """`PreparationPort` 의 Postgres 구현."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    # --- 쓰기 -------------------------------------------------------------------

    async def save_dossier(self, dossier: Dossier) -> str:
        async with self._engine.begin() as conn:
            await self._check_epochs(conn, dossier.privacy_versions)
            await self._insert_dossier(conn, dossier)
        return dossier.dossier_id

    async def save_prep(
        self, dossier: Dossier, snapshot: CaseSnapshot, prompt_version: str
    ) -> PrepSaveResult:
        key = {
            "post_id": snapshot.post_id,
            "input_hash": input_hash(snapshot),
            "prompt_version": prompt_version,
        }
        async with self._engine.begin() as conn:
            await self._check_epochs(conn, dossier.privacy_versions)
            inserted = (
                await conn.execute(
                    text(INSERT_PREP_SQL),
                    {
                        **key,
                        "id": uuid.uuid4(),
                        "post_version": snapshot.post_version,
                        "audience_version": snapshot.audience.audience_version,
                        "privacy_versions": _privacy_json(dossier.privacy_versions),
                        "dossier_id": uuid.UUID(dossier.dossier_id),
                    },
                )
            ).first()
            if inserted is None:
                existing = (await conn.execute(text(SELECT_PREP_KEY_SQL), key)).mappings().one()
                return PrepSaveResult(
                    prep_id=existing["id"], status=existing["status"], reused=True
                )
            await self._insert_dossier(conn, dossier)
        return PrepSaveResult(prep_id=inserted.id, status="DOSSIER_READY", reused=False)

    async def save_banter(self, prep_id: str, banter: Mapping[Intensity, list[Candidate]]) -> bool:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text(UPDATE_BANTER_SQL), {"id": prep_id, "banter": _banter_json(banter)}
            )
        return result.rowcount == 1

    # --- 읽기 -------------------------------------------------------------------

    async def load_valid_prep(
        self, snapshot: CaseSnapshot, prompt_version: str
    ) -> ValidPrep | None:
        params = {
            "post_id": snapshot.post_id,
            "input_hash": input_hash(snapshot),
            "prompt_version": prompt_version,
        }
        expected = {(pv.scope_key, pv.epoch) for pv in snapshot.privacy_versions}
        async with self._engine.connect() as conn:
            row = (await conn.execute(text(SELECT_VALID_PREP_SQL), params)).mappings().first()
            if row is None or _privacy_pairs(row["privacy_versions"]) != expected:
                return None
            dossier_id = row["dossier_id"]
            evidence = (
                (await conn.execute(text(SELECT_EVIDENCE_SQL), {"dossier_id": dossier_id}))
                .mappings()
                .all()
            )
            sources = (
                (await conn.execute(text(SELECT_SOURCES_SQL), {"dossier_id": dossier_id}))
                .mappings()
                .all()
            )
        by_evidence: dict[str, list[tuple[str, str, int]]] = {}
        for source in sources:
            by_evidence.setdefault(source["evidence_id"], []).append(
                (source["source_type"], source["source_id"], int(source["source_version"]))
            )
        facts = tuple(_fact(item, tuple(by_evidence.get(item["id"], ()))) for item in evidence)
        dossier = Dossier(
            dossier_id=dossier_id,
            post_id=row["post_id"],
            snapshot_hash=row["snapshot_hash"],
            facts=facts,
            label_map={item["label"]: item["id"] for item in evidence},
            privacy_versions=tuple((pv.scope_key, pv.epoch) for pv in snapshot.privacy_versions),
        )
        return ValidPrep(
            dossier=dossier, banter=_parse_banter(row["banter_json"]), prep_id=row["prep_id"]
        )

    async def approved_banter_examples(
        self, intensity: Intensity, category: str, limit: int
    ) -> list[str]:
        if limit <= 0:
            return []
        async with self._engine.connect() as conn:
            rows = await conn.execute(
                text(SELECT_EXAMPLES_SQL),
                {"intensity": str(intensity), "category": category, "limit": limit},
            )
            return [row[0] for row in rows]

    async def stale_scopes(self, privacy_versions: Iterable[tuple[str, int]]) -> list[str]:
        pairs = tuple((str(scope_key), int(epoch)) for scope_key, epoch in privacy_versions)
        async with self._engine.connect() as conn:
            current = await self._current_epochs(conn, pairs)
        return mismatched_scopes(pairs, current)

    # --- 내부 -------------------------------------------------------------------

    @staticmethod
    async def _current_epochs(
        conn: AsyncConnection, pairs: tuple[tuple[str, int], ...]
    ) -> dict[str, int]:
        keys = [scope_key for scope_key, _ in pairs]
        if not keys:
            return {}
        statement = text(EPOCH_SQL).bindparams(bindparam("keys", type_=ARRAY(Text)))
        rows = await conn.execute(statement, {"keys": keys})
        return {row.scope_key: int(row.epoch) for row in rows}

    @classmethod
    async def _check_epochs(cls, conn: AsyncConnection, pairs: tuple[tuple[str, int], ...]) -> None:
        mismatched = mismatched_scopes(pairs, await cls._current_epochs(conn, pairs))
        if mismatched:
            raise EvidenceInvalidated(mismatched)

    @staticmethod
    async def _insert_dossier(conn: AsyncConnection, dossier: Dossier) -> None:
        dossier_id = uuid.UUID(dossier.dossier_id)
        await conn.execute(
            text(INSERT_DOSSIER_SQL),
            {
                "id": dossier_id,
                "post_id": dossier.post_id,
                "snapshot_hash": dossier.snapshot_hash,
                "label_map": _dumps(dict(dossier.label_map)),
                "privacy_versions": _privacy_json(dossier.privacy_versions),
            },
        )
        sources: list[dict[str, Any]] = []
        for fact in dossier.facts:
            evidence_id = uuid.UUID(dossier.label_map[fact.label])
            await conn.execute(
                text(INSERT_EVIDENCE_SQL), _evidence_params(dossier_id, evidence_id, fact)
            )
            sources.extend(
                {
                    "evidence_id": evidence_id,
                    "source_type": source_type,
                    "source_id": source_id,
                    "source_version": source_version,
                }
                for source_type, source_id, source_version in dict.fromkeys(fact.sources)
            )
        if sources:
            await conn.execute(text(INSERT_SOURCE_SQL), sources)


def _fact(row: Mapping[str, Any], sources: tuple[tuple[str, str, int], ...]) -> EvidenceFact:
    scope = _json(row["scope"])
    occurred_at: datetime | None = row["occurred_at"]
    aggregation = _json(row["aggregation"])
    return EvidenceFact(
        label=row["label"],
        epistemic_type=row["epistemic_type"],
        fact_type=row["fact_type"],
        text=row["text"],
        scope=Scope(Visibility(scope["visibility"]), frozenset(scope.get("room_ids") or ())),
        aggregation=aggregation,
        occurred_at=occurred_at,
        sources=sources,
    )
