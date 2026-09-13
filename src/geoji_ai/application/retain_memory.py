"""RETAIN 핸들러(04 §3.3·§3.5, ME-03).

흐름: snapshot → 늦은 retain → 행 규칙(§3.3 표) → `memory.retain_verdict`/`retain_comment`
→ complete.

늦은 retain 3조건(§3.5)
1. `processed_memory_events` 에 이미 있는 event — 어댑터가 retain 트랜잭션 안에서 본다
2. snapshot 404 — 여기서 complete 하고 끝낸다
3. snapshot `privacy_versions` 와 지금 `ai.privacy_epochs` 가 하나라도 다름 — 어댑터가 같은
   트랜잭션 안에서 본다(job payload 에 epoch 가 없어서 스냅샷 값과 비교한다)

백엔드가 RETAIN 확장 필드(`verdict_final`·`comment`)를 주지 않으면 행 0 으로 complete 한다.
재시도해도 같은 스냅샷이라 fail 하지 않는다.

`MITIGATION` 행은 만들지 않는다 — 양형관 `mitigating` 라벨의 출처가 스냅샷에 없다(미결).
`RULE_HIT` 행은 어댑터가 해당 post 의 최신 dossier RULE Evidence 에서 만든다.

application 은 어댑터를 import 하지 않는다. 백엔드 예외는 `sentence_case` 와 같이 속성으로
알아본다(`status`·`error_code`).
"""

from __future__ import annotations

from typing import Any, Protocol

from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.contracts.jobs import Job
from geoji_ai.core.logging import get_logger
from geoji_ai.domain.comment_safety import Comment, check, filter_comments
from geoji_ai.ports.backend import BackendPort
from geoji_ai.ports.jobs import JobsPort
from geoji_ai.ports.memory import MemoryPort

__all__ = [
    "RetainHandler",
    "build_comment_payload",
    "build_verdict_payload",
]

log = get_logger(__name__)

SENTENCE_FINALIZED = "sentence.finalized"
COMMENT_APPROVED = "comment.approved"

_BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
_NOT_FOUND = 404


class _Context(Protocol):
    jobs: JobsPort
    backend: BackendPort
    memory: MemoryPort | None
    generation_id: str
    worker_id: str


def _scope(visibility: str, room_ids: list[str]) -> dict[str, Any]:
    return {"visibility": visibility, "room_ids": list(room_ids)}


def _privacy_versions(snapshot: CaseSnapshot) -> list[dict[str, Any]]:
    return [pv.model_dump(mode="json") for pv in snapshot.privacy_versions]


def build_verdict_payload(snapshot: CaseSnapshot) -> dict[str, Any] | None:
    """`sentence.finalized` 행(§3.3). 확장 필드(`verdict_final`·`jury`)가 없으면 None."""
    jury = snapshot.jury
    final = snapshot.verdict_final
    if jury is None or final is None:
        return None
    final_json = final.model_dump(mode="json")
    jury_json = jury.model_dump(mode="json")
    audience = snapshot.audience
    user_scope = _scope("PUBLIC" if audience.public_share_enabled else "ROOMS", audience.room_ids)
    post_source = {
        "source_type": "POST",
        "source_id": snapshot.post_id,
        "source_version": snapshot.post_version,
    }
    verdict_source = {
        "source_type": "VERDICT",
        "source_id": jury.verdict_id,
        "source_version": jury.verdict_version,
    }
    facts: list[dict[str, Any]] = [
        {
            "bank_type": "user",
            "bank_id": snapshot.author_id,
            "fact_type": "SPEND",
            "epistemic_type": "DB_RECORD",
            **post_source,
            "payload": {
                "post_id": snapshot.post_id,
                "post_type": snapshot.post_type,
                "category": snapshot.category,
                "amount_krw": snapshot.amount_krw,
                "reason": snapshot.reason,
                "spent_at": snapshot.created_at.isoformat(),
            },
            "scope": user_scope,
            "occurred_at": snapshot.created_at,
        },
        {
            "bank_type": "user",
            "bank_id": snapshot.author_id,
            "fact_type": "VERDICT",
            "epistemic_type": "DB_RECORD",
            **verdict_source,
            "payload": {
                "post_id": snapshot.post_id,
                "result": jury_json["result"],
                "guilty_ratio": jury_json["guilty_ratio"],
                "vote_counts": jury_json["vote_counts"],
                "sentence": final_json["sentence"],
                "sentencing_reason": final_json["sentencing_reason"],
                "reason_source": final_json["reason_source"],
                "banter_strategy": final_json["banter_strategy"],
                "applied_intensity": final_json["applied_intensity"],
            },
            "scope": user_scope,
            "occurred_at": jury.confirmed_at,
        },
    ]
    intensity_by_room = {room.room_id: room.intensity.value for room in snapshot.room_snapshots}
    for room_id in audience.room_ids:
        facts.append(
            {
                "bank_type": "room",
                "bank_id": room_id,
                "fact_type": "VERDICT",
                "epistemic_type": "DB_RECORD",
                **verdict_source,
                "payload": {
                    "post_id": snapshot.post_id,
                    "author_id": snapshot.author_id,
                    "category": snapshot.category,
                    "result": jury_json["result"],
                    "guilty_ratio": jury_json["guilty_ratio"],
                    "intensity": intensity_by_room.get(room_id),
                },
                "scope": _scope("ROOMS", [room_id]),
                "occurred_at": jury.confirmed_at,
            }
        )
    return {
        "privacy_versions": _privacy_versions(snapshot),
        "facts": facts,
        "rule_hit": {
            "post_id": snapshot.post_id,
            "category": snapshot.category,
            "room_ids": list(audience.room_ids),
            "occurred_at": jury.confirmed_at,
        },
    }


def build_comment_payload(snapshot: CaseSnapshot) -> dict[str, Any] | None:
    """`comment.approved` 행(§3.3). 확장 필드가 없거나 방 강도를 모르면 None.

    안전 필터 탈락이면 `facts` 가 빈 payload 를 돌려준다.
    """
    comment = snapshot.comment
    if comment is None:
        return None
    intensity = next(
        (room.intensity for room in snapshot.room_snapshots if room.room_id == comment.room_id),
        None,
    )
    if intensity is None:
        return None
    candidate = Comment(**comment.model_dump())
    kept = filter_comments([candidate], defendant_id=snapshot.author_id, room_intensity=intensity)
    facts: list[dict[str, Any]] = []
    if kept:
        facts.append(
            {
                "bank_type": "room",
                "bank_id": comment.room_id,
                "fact_type": "COMMENT",
                "epistemic_type": "USER_CLAIM",
                "source_type": "COMMENT",
                "source_id": comment.comment_id,
                "source_version": comment.version,
                "payload": {
                    "comment_id": comment.comment_id,
                    "post_id": comment.post_id,
                    "author_id": comment.author_id,
                    "content": comment.content,
                    "created_at": comment.created_at.isoformat(),
                },
                "scope": _scope("ROOMS", [comment.room_id]),
                "occurred_at": comment.created_at,
            }
        )
    else:
        reasons = check(candidate, defendant_id=snapshot.author_id, room_intensity=intensity)
        log.info("retain_comment_rejected", reasons=[reason.value for reason in reasons])
    return {"privacy_versions": _privacy_versions(snapshot), "facts": facts}


class RetainHandler:
    """`sentence.finalized`·`comment.approved` 공용. event 는 `job.event_type` 으로 가른다."""

    async def __call__(self, job: Job, ctx: _Context) -> None:
        try:
            snapshot = await ctx.backend.snapshot(job.id, ctx.generation_id)
        except Exception as exc:
            if getattr(exc, "status", None) == _NOT_FOUND:
                # 늦은 retain (2): 원본이 지워졌다.
                log.info("retain_snapshot_not_found")
                await ctx.jobs.complete(job.id, ctx.worker_id, ctx.generation_id)
                return
            if getattr(exc, "error_code", None) == _BACKEND_UNAVAILABLE:
                await ctx.jobs.fail(
                    job.id,
                    ctx.worker_id,
                    ctx.generation_id,
                    error_code=_BACKEND_UNAVAILABLE,
                    retry_after_s=getattr(exc, "retry_after_s", None),
                )
                return
            raise

        if job.event_type == SENTENCE_FINALIZED:
            payload = build_verdict_payload(snapshot)
        elif job.event_type == COMMENT_APPROVED:
            payload = build_comment_payload(snapshot)
        else:
            raise ValueError(f"RETAIN 이 모르는 event_type: {job.event_type!r}")

        if payload is None:
            log.warning("retain_snapshot_incomplete", event_type=job.event_type)
        elif payload["facts"] or payload.get("rule_hit") is not None:
            if ctx.memory is None:
                raise RuntimeError("RETAIN 핸들러에 memory 가 없다")
            if job.event_type == SENTENCE_FINALIZED:
                written = await ctx.memory.retain_verdict(job.event_id, payload)
            else:
                written = await ctx.memory.retain_comment(job.event_id, payload)
            log.info("retain_written", event_type=job.event_type, rows=written)
        await ctx.jobs.complete(job.id, ctx.worker_id, ctx.generation_id)
