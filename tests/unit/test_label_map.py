"""label_map(04 §4.1): 라벨 ↔ evidence UUID, map 밖 라벨은 근거 없음."""

from __future__ import annotations

import uuid

from geoji_ai.application.build_evidence import build_evidence, resolve_label
from tests.unit.test_build_evidence import load_snapshot, make_resolved, source


def _dossier():
    snapshot = load_snapshot()
    resolved = make_resolved(
        snapshot,
        sources=[
            source("POST", "p-1", {"category": "교통/택시", "amount_krw": 8000}),
            source("VERDICT", "v-1", {"result": "guilty"}),
        ],
    )
    return build_evidence(snapshot, resolved, pack_limit=12)


def test_09_라벨_UUID_왕복():
    dossier = _dossier()
    reverse = {evidence_id: label for label, evidence_id in dossier.label_map.items()}
    assert len(reverse) == len(dossier.label_map)
    for fact in dossier.facts:
        evidence_id = resolve_label(dossier.label_map, fact.label)
        assert evidence_id is not None
        assert str(uuid.UUID(evidence_id)) == evidence_id
        assert reverse[evidence_id] == fact.label


def test_10_map_밖_라벨은_None():
    dossier = _dossier()
    assert resolve_label(dossier.label_map, "F99") is None
    assert resolve_label(dossier.label_map, "") is None


def test_11_라벨은_F0부터_연속():
    dossier = _dossier()
    n = len(dossier.facts)
    assert n == 5
    assert [f.label for f in dossier.facts] == [f"F{i}" for i in range(n)]
    assert list(dossier.label_map) == [f"F{i}" for i in range(n)]
