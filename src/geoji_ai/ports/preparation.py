"""재판 준비 저장 포트(04 §3.4·§3.5).

`build_evidence` 가 만든 `Dossier` 를 `ai.dossiers`·`ai.evidence`·`ai.evidence_sources`
에 한 트랜잭션으로 저장한다. 저장 직전 epoch 가 어긋나면 `EvidenceInvalidated`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from geoji_ai.domain.visibility import Scope

__all__ = [
    "EVIDENCE_TEXT_MAX",
    "Dossier",
    "EvidenceFact",
    "EvidenceInvalidated",
    "PreparationPort",
]

#: `ai.evidence.text` CHECK(char_length ≤ 500).
EVIDENCE_TEXT_MAX = 500


class EvidenceInvalidated(Exception):
    """스냅샷 `privacy_versions` 와 현재 epoch 가 다르다. 아무것도 저장하지 않았다."""

    error_code = "EVIDENCE_INVALIDATED"

    def __init__(self, scope_keys: list[str]) -> None:
        self.scope_keys = list(scope_keys)
        super().__init__(f"{self.error_code}: epoch 불일치 scope {len(self.scope_keys)}개")


@dataclass(frozen=True)
class EvidenceFact:
    """근거 한 줄. `sources` 는 `(source_type, source_id, source_version)` 들."""

    label: str
    epistemic_type: str
    fact_type: str
    text: str
    scope: Scope
    aggregation: dict[str, Any] | None
    occurred_at: datetime | None
    sources: tuple[tuple[str, str, int], ...]

    def __post_init__(self) -> None:
        if len(self.text) > EVIDENCE_TEXT_MAX:
            raise ValueError(f"근거 문장이 {EVIDENCE_TEXT_MAX}자를 넘는다: {len(self.text)}")


@dataclass(frozen=True)
class Dossier:
    """`label_map` 은 라벨 → evidence UUID. `privacy_versions` 는 `(scope_key, epoch)` 들."""

    dossier_id: str
    post_id: str
    snapshot_hash: str
    facts: tuple[EvidenceFact, ...]
    label_map: Mapping[str, str]
    privacy_versions: tuple[tuple[str, int], ...]


@runtime_checkable
class PreparationPort(Protocol):
    async def save_dossier(self, dossier: Dossier) -> str:
        """저장한 dossier id. epoch 불일치면 `EvidenceInvalidated`."""
        ...
