"""dossier 저장 직전 epoch 검사(04 §3.5·§4.2). 실제 Postgres."""

from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.postgres_jobs import make_engine
from geoji_ai.adapters.postgres_preparation import PostgresPreparation
from geoji_ai.application.build_evidence import build_evidence
from geoji_ai.ports.preparation import Dossier, EvidenceInvalidated
from tests.unit.test_build_evidence import load_snapshot, make_resolved, source

TABLES = ("ai.dossiers", "ai.evidence", "ai.evidence_sources")


def _dossier() -> Dossier:
    snapshot = load_snapshot()
    resolved = make_resolved(
        snapshot, sources=[source("POST", "p-mem", {"category": "교통/택시", "amount_krw": 8000})]
    )
    return build_evidence(snapshot, resolved, pack_limit=12)


async def _set_epochs(engine: AsyncEngine, epochs: dict[str, int]) -> None:
    async with engine.begin() as conn:
        for scope_key, epoch in epochs.items():
            await conn.execute(
                text("INSERT INTO ai.privacy_epochs (scope_key, epoch) VALUES (:k, :e)"),
                {"k": scope_key, "e": epoch},
            )


async def _counts(engine: AsyncEngine) -> dict[str, int]:
    async with engine.connect() as conn:
        return {
            table: int((await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())
            for table in TABLES
        }


def _role_url(url: str, role: str) -> str:
    return make_url(url).set(username=role, password=role).render_as_string(hide_password=False)


async def test_12_epoch_일치면_세_테이블에_저장한다(engine: AsyncEngine, privacy_epochs: str):
    dossier = _dossier()
    await _set_epochs(engine, dict(dossier.privacy_versions))

    saved = await PostgresPreparation(engine).save_dossier(dossier)

    assert saved == dossier.dossier_id
    n = len(dossier.facts)
    assert await _counts(engine) == {
        "ai.dossiers": 1,
        "ai.evidence": n,
        "ai.evidence_sources": sum(len(f.sources) for f in dossier.facts),
    }
    async with engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT label_map, snapshot_hash, privacy_versions FROM ai.dossiers")
                )
            )
            .mappings()
            .one()
        )
        labels = (
            await conn.execute(
                text("SELECT label, CAST(id AS text) FROM ai.evidence ORDER BY label")
            )
        ).all()
    assert row["label_map"] == dict(dossier.label_map)
    assert row["snapshot_hash"] == dossier.snapshot_hash
    assert {(p["scope_key"], p["epoch"]) for p in row["privacy_versions"]} == set(
        dossier.privacy_versions
    )
    assert {label: evidence_id for label, evidence_id in labels} == dict(dossier.label_map)


async def test_13_epoch_불일치면_EvidenceInvalidated_와_0행(
    engine: AsyncEngine, privacy_epochs: str
):
    dossier = _dossier()
    epochs = dict(dossier.privacy_versions)
    first = next(iter(epochs))
    epochs[first] += 1
    await _set_epochs(engine, epochs)

    with pytest.raises(EvidenceInvalidated) as caught:
        await PostgresPreparation(engine).save_dossier(dossier)

    assert caught.value.error_code == "EVIDENCE_INVALIDATED"
    assert caught.value.scope_keys == [first]
    assert await _counts(engine) == dict.fromkeys(TABLES, 0)


async def test_14_privacy_epochs_에_행이_없으면_0_과_일치(engine: AsyncEngine, privacy_epochs: str):
    base = _dossier()
    zero = dataclasses.replace(
        base, privacy_versions=tuple((k, 0) for k, _ in base.privacy_versions)
    )
    await PostgresPreparation(engine).save_dossier(zero)
    assert (await _counts(engine))["ai.dossiers"] == 1

    # 행이 없는데 스냅샷이 1 이면 불일치
    with pytest.raises(EvidenceInvalidated):
        await PostgresPreparation(engine).save_dossier(_dossier())
    assert (await _counts(engine))["ai.dossiers"] == 1


async def test_15_ai_worker_role_로_저장한다(
    engine: AsyncEngine, privacy_epochs: str, test_database_url: str
):
    dossier = _dossier()
    await _set_epochs(engine, dict(dossier.privacy_versions))
    worker_engine = make_engine(_role_url(test_database_url, "ai_worker"))
    try:
        async with worker_engine.connect() as conn:
            assert (await conn.execute(text("SELECT current_user"))).scalar_one() == "ai_worker"
        await PostgresPreparation(worker_engine).save_dossier(dossier)
    finally:
        await worker_engine.dispose()
    assert (await _counts(engine))["ai.dossiers"] == 1
