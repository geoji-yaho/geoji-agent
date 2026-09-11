"""기억 포트 (01 §3.6). recall 은 원문이 아니라 참조만 돌려준다(proposal2 §16.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class MemoryCandidate:
    """recall 후보 참조. payload 는 담지 않는다."""

    source_type: str
    source_id: str
    source_version: int
    score: float


@dataclass(frozen=True)
class RoomStrictness:
    """04 §3 `strictness{category_guilty_rate, n}`. n < 3 이면 RoomRecall.strictness 가 None."""

    category_guilty_rate: float
    n: int


@dataclass(frozen=True)
class RoomRecall:
    rules_hit: list[str] = field(default_factory=list)
    style_example_refs: list[str] = field(default_factory=list)
    strictness: RoomStrictness | None = None


@runtime_checkable
class MemoryPort(Protocol):
    async def recall_user(
        self,
        user_id: str,
        category: str,
        before: datetime,
        limit: int,
    ) -> list[MemoryCandidate]: ...

    async def recall_room(self, room_id: str, category: str) -> RoomRecall: ...

    async def retain_verdict(self, event_id: str, verdict_payload: dict[str, Any]) -> int: ...

    async def retain_comment(self, event_id: str, comment_payload: dict[str, Any]) -> int: ...

    async def delete_user(self, user_id: str) -> int: ...

    async def delete_room(self, room_id: str) -> int: ...

    async def delete_post(self, post_id: str) -> int: ...
