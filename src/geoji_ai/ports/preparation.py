"""재판 준비 저장 포트(04 §3.4·§3.5, 05 §3.2·§3.3).

`build_evidence` 가 만든 `Dossier` 를 `ai.dossiers`·`ai.evidence`·`ai.evidence_sources`
에 한 트랜잭션으로 저장한다. 저장 직전 epoch 가 어긋나면 `EvidenceInvalidated`.

그래프 B 는 `save_prep` 으로 dossier 와 `ai.trial_prep(DOSSIER_READY)` 을 같이 쓰고,
드립까지 되면 `save_banter` 로 `COMPLETE` 로 올린다. 그래프 C 는 `load_valid_prep` 으로 읽는다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from geoji_ai.domain.intensity import Intensity
from geoji_ai.domain.visibility import Scope

if TYPE_CHECKING:
    from geoji_ai.contracts.case import CaseSnapshot
    from geoji_ai.graphs.states import Candidate

__all__ = [
    "EVIDENCE_TEXT_MAX",
    "Dossier",
    "EvidenceFact",
    "EvidenceInvalidated",
    "PrepSaveResult",
    "PrepStatus",
    "PreparationPort",
    "ValidPrep",
]

#: `ai.evidence.text` CHECK(char_length ≤ 500).
EVIDENCE_TEXT_MAX = 500

#: `ai.trial_prep.status` CHECK(002).
PrepStatus = Literal["DOSSIER_READY", "COMPLETE", "INVALIDATED"]


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


@dataclass(frozen=True)
class PrepSaveResult:
    """`save_prep` 결과. `reused` 면 같은 `(post_id, input_hash, prompt_version)` 행이 이미 있어
    아무것도 새로 쓰지 않았다. `status` 는 그 행의 현재 상태다."""

    prep_id: str
    status: PrepStatus
    reused: bool


@dataclass(frozen=True)
class ValidPrep:
    """그래프 C 가 쓸 수 있는 사전 준비(05 §3.3 조건 통과). 드립이 없으면 `banter` 는 빈 dict."""

    dossier: Dossier
    banter: dict[Intensity, list[Candidate]] = field(default_factory=dict)
    prep_id: str = ""


@runtime_checkable
class PreparationPort(Protocol):
    async def save_dossier(self, dossier: Dossier) -> str:
        """저장한 dossier id. epoch 불일치면 `EvidenceInvalidated`."""
        ...

    async def save_prep(
        self, dossier: Dossier, snapshot: CaseSnapshot, prompt_version: str
    ) -> PrepSaveResult:
        """한 트랜잭션: epoch 재확인(불일치면 `EvidenceInvalidated`) → 같은 키 행이 있으면 그대로
        반환(`reused=True`) → 없으면 dossier·evidence·sources + `trial_prep(DOSSIER_READY)`."""
        ...

    async def save_banter(self, prep_id: str, banter: Mapping[Intensity, list[Candidate]]) -> bool:
        """`banter_json IS NULL ∧ status='DOSSIER_READY'` 일 때만 채우고 `COMPLETE`.

        채웠으면 True."""
        ...

    async def load_valid_prep(
        self, snapshot: CaseSnapshot, prompt_version: str
    ) -> ValidPrep | None:
        """post_id·input_hash·prompt_version 일치 ∧ 무효화 안 됨 ∧ dossier epoch == 스냅샷."""
        ...

    async def approved_banter_examples(
        self, intensity: Intensity, category: str, limit: int
    ) -> list[str]:
        """`ai.banter_examples approved=true` 중 강도 일치, 카테고리 일치 우선 `limit` 개의 문장."""
        ...

    async def stale_scopes(self, privacy_versions: Iterable[tuple[str, int]]) -> list[str]:
        """현재 `ai.privacy_epochs` 와 epoch 가 다른 scope_key(04 §3.5 (b) finalize 직전).

        빈 목록이면 일치. 아무것도 쓰지 않는다."""
        ...
