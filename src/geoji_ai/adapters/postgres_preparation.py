"""dossier 저장 어댑터(04 §3.4·§3.5).

`ports.preparation.PreparationPort` 의 Postgres 구현. 한 트랜잭션 안에서
`ai.privacy_epochs` 를 읽어 스냅샷 epoch 와 비교하고, 어긋나면 아무것도 쓰지 않고
`EvidenceInvalidated` 를 올린다(트랜잭션 롤백). 일치하면 `ai.dossiers` →
`ai.evidence` → `ai.evidence_sources` 순으로 INSERT 한다.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.types import Text

from geoji_ai.domain.privacy import mismatched_scopes
from geoji_ai.domain.visibility import Scope
from geoji_ai.ports.preparation import Dossier, EvidenceFact, EvidenceInvalidated

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


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _scope_json(scope: Scope) -> str:
    return _dumps({"visibility": str(scope.visibility), "room_ids": sorted(scope.room_ids)})


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


class PostgresPreparation:
    """`PreparationPort` 의 Postgres 구현."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def save_dossier(self, dossier: Dossier) -> str:
        dossier_id = uuid.UUID(dossier.dossier_id)
        keys = [scope_key for scope_key, _ in dossier.privacy_versions]
        async with self._engine.begin() as conn:
            current: dict[str, int] = {}
            if keys:
                statement = text(EPOCH_SQL).bindparams(bindparam("keys", type_=ARRAY(Text)))
                rows = await conn.execute(statement, {"keys": keys})
                current = {row.scope_key: int(row.epoch) for row in rows}
            mismatched = mismatched_scopes(dossier.privacy_versions, current)
            if mismatched:
                raise EvidenceInvalidated(mismatched)

            await conn.execute(
                text(INSERT_DOSSIER_SQL),
                {
                    "id": dossier_id,
                    "post_id": dossier.post_id,
                    "snapshot_hash": dossier.snapshot_hash,
                    "label_map": _dumps(dict(dossier.label_map)),
                    "privacy_versions": _dumps(
                        [{"scope_key": k, "epoch": e} for k, e in dossier.privacy_versions]
                    ),
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
        return dossier.dossier_id
