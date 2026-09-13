"""그래프 state 2종(05 §3.1).

05 §3.1 코드의 `Evidence` 는 `ports.preparation.EvidenceFact` 다. 05 코드에 있는데 아직 어느
모듈에도 없는 `Candidate`(드립 후보)·`CallRecord`(모델 호출 기록)는 여기 최소 dataclass 로 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.contracts.evaluation import EvaluationReport
from geoji_ai.contracts.jobs import Job
from geoji_ai.contracts.sentencing import SentencingDecision
from geoji_ai.contracts.writer import BanterStrategy, TextDraft
from geoji_ai.domain.budget import Deadline
from geoji_ai.domain.intensity import Intensity
from geoji_ai.ports.backend import BeginGenerationResult, ResolveEvidenceResponse
from geoji_ai.ports.memory import MemoryCandidate
from geoji_ai.ports.preparation import Dossier, EvidenceFact

__all__ = [
    "CallRecord",
    "Candidate",
    "Evidence",
    "PrepareState",
    "SentenceState",
]

Evidence = EvidenceFact


@dataclass(frozen=True)
class Candidate:
    """드립 후보 1개(01 §3.3 드립 후보 스키마 + 서기가 고를 id)."""

    candidate_id: str
    text: str
    strategy: BanterStrategy
    fits: tuple[str, ...]
    evidence_labels: tuple[str, ...]


@dataclass(frozen=True)
class CallRecord:
    """모델 호출 1회 기록. 호출 횟수 단언(05 §4.1)용 최소 필드."""

    role: str
    intensity: Intensity | None
    model_id: str | None
    error: str | None


class PrepareState(TypedDict):
    job: Job
    snapshot: CaseSnapshot
    candidates: list[MemoryCandidate]
    resolved: ResolveEvidenceResponse | None
    evidence: list[Evidence]
    label_map: dict[str, str]
    dossier_id: str | None
    reason_analysis: dict | None
    banter: dict[Intensity, list[Candidate]]
    status: str
    errors: list[str]


class SentenceState(TypedDict):
    job: Job
    mode: Literal["INITIAL", "REGENERATE"]
    snapshot: CaseSnapshot
    begin: BeginGenerationResult
    deadline: Deadline
    dossier: Dossier | None
    dossier_source: Literal["PREP", "INLINE", "MINIMAL"]
    banter: dict[Intensity, list[Candidate]]
    sentencing: SentencingDecision | None
    sentencing_source: Literal["AI", "RULE", "FIXED"]
    drafts: dict[Intensity, TextDraft]
    draft_sources: dict[Intensity, Literal["AI", "TEMPLATE"]]
    validation: dict[Intensity, list[str]]
    evaluation: EvaluationReport | None
    repair_count: int
    draft_hash: str | None
    calls: list[CallRecord]
    failure: str | None
