"""`MemoryPort` 의 Postgres 구현(04 §3.2).

- recall 은 **참조만** 돌려준다(`MemoryCandidate`). payload 는 밖으로 내보내지 않는다
- retain 은 `processed_memory_events` INSERT · 늦은 retain epoch 검사(§3.5) · `memory_facts`
  INSERT 를 **한 트랜잭션**에서 한다. UNIQUE 충돌은 `ON CONFLICT DO NOTHING`. 커밋 뒤 epoch 를
  한 번 더 보고 달라졌으면 방금 넣은 행을 소프트 삭제한다(잠금 없는 검사의 창 보완)
- delete 는 `deleted_at = now()` 소프트 삭제. 물리 삭제는 P1

뱅크는 `bank_type`(`user`·`room`) + `bank_id`(원래 ID) 두 컬럼이다(§3.2 의 `user/{user_id}`).
retain payload 모양은 `application.retain_memory` 가 만든다:
`{"privacy_versions": [{scope_key, epoch}], "facts": [행], "rule_hit": {...} | None}`.

`invalidate_scope` 는 `database/sql/invalidate_scope.sql`(백엔드 스케줄러 몫)을 호출자가 연
트랜잭션에서 돌린다. 워커는 부르지 않는다 — 테스트와 로컬 확인용이다.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.types import Text, Uuid

from geoji_ai.core.config import Settings
from geoji_ai.core.logging import get_logger
from geoji_ai.ports.memory import MemoryCandidate, RoomRecall, RoomStrictness

__all__ = [
    "INVALIDATE_SCOPE_SQL_PATH",
    "JOSA",
    "KEYWORD_LIMIT",
    "RECENCY_DAYS",
    "RULES_HIT_DAYS",
    "STOPWORDS",
    "STRICTNESS_MIN_N",
    "PostgresMemory",
    "extract_keywords",
    "invalidate_scope",
    "invalidate_statements",
]

log = get_logger(__name__)

#: `<저장소 루트>/database/sql/invalidate_scope.sql`.
INVALIDATE_SCOPE_SQL_PATH: Path = (
    Path(__file__).resolve().parents[3] / "database" / "sql" / "invalidate_scope.sql"
)

# --- 키워드(§3.2) ----------------------------------------------------------------

#: §3.2 조사 목록. 긴 것부터 벗긴다(`에서` 가 `서` 보다 먼저).
JOSA: tuple[str, ...] = tuple(
    sorted(
        ("은", "는", "이", "가", "을", "를", "에", "에서", "도", "로", "으로", "서"),
        key=len,
        reverse=True,
    )
)
#: 계획서에 목록이 없다(spec 해석 표). 빈 목록으로 둔다.
STOPWORDS: frozenset[str] = frozenset()
KEYWORD_MIN_CHARS = 2
KEYWORD_LIMIT = 3

#: recency 가산 창(§3.2 "30일 안 1").
RECENCY_DAYS = 30
#: `rules_hit` 조회 창(§3.2 "90일"). 기준은 `now()`.
RULES_HIT_DAYS = 90
#: `strictness` 는 n 이 이보다 작으면 None(§3.2 "n < 3 이면 null").
STRICTNESS_MIN_N = 3

_USER_RECALL_FACT_TYPES = ("SPEND", "VERDICT", "MITIGATION")


def extract_keywords(reason: str | None) -> list[str]:
    """공백 분리 → 2자 이상 → 조사 제거 → 불용어 제거 → 긴 순 3개. 형태소 분석 없음."""
    if not reason:
        return []
    keywords: list[str] = []
    for token in reason.split():
        if len(token) < KEYWORD_MIN_CHARS:
            continue
        for josa in JOSA:
            if token.endswith(josa) and len(token) > len(josa):
                token = token[: -len(josa)]
                break
        if token in STOPWORDS or token in keywords:
            continue
        keywords.append(token)
    # sorted 는 안정 정렬이라 길이가 같으면 원문 순서를 지킨다.
    return sorted(keywords, key=len, reverse=True)[:KEYWORD_LIMIT]


def _like_pattern(keyword: str) -> str:
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# --- SQL -----------------------------------------------------------------------

# 사용자 뱅크의 VERDICT·MITIGATION 에는 category·reason 이 없다(§3.3 payload). 같은 뱅크의
# 같은 post SPEND 사실에서 가져와 점수를 매긴다.
RECALL_USER_SQL = """
SELECT f.source_type, f.source_id, f.source_version, f.fact_type,
  (CASE WHEN COALESCE(f.payload->>'category', s.category) = :category THEN 2 ELSE 0 END
   + CASE WHEN COALESCE(f.payload->>'reason', s.reason) ILIKE ANY(:patterns) THEN 1 ELSE 0 END
   + CASE WHEN f.occurred_at >= CAST(:before AS timestamptz) - make_interval(days => :recency_days)
          THEN 1 ELSE 0 END) AS score
FROM ai.memory_facts f
LEFT JOIN LATERAL (
  SELECT sp.payload->>'category' AS category, sp.payload->>'reason' AS reason
  FROM ai.memory_facts sp
  WHERE sp.bank_type = f.bank_type AND sp.bank_id = f.bank_id AND sp.fact_type = 'SPEND'
    AND sp.deleted_at IS NULL AND sp.payload->>'post_id' = f.payload->>'post_id'
  ORDER BY sp.source_version DESC LIMIT 1) s ON true
WHERE f.bank_type = 'user' AND f.bank_id = :user_id AND f.deleted_at IS NULL
  AND f.occurred_at < CAST(:before AS timestamptz) AND f.fact_type = ANY(:fact_types)
ORDER BY score DESC, f.occurred_at DESC, f.source_type, f.source_id, f.fact_type
LIMIT :limit
"""

RULES_HIT_SQL = """
SELECT source_type, source_id, source_version, fact_type,
  (2 + CASE WHEN occurred_at >= now() - make_interval(days => :recency_days) THEN 1 ELSE 0 END)
    AS score
FROM ai.memory_facts
WHERE bank_type = 'room' AND bank_id = :room_id AND fact_type = 'RULE_HIT' AND deleted_at IS NULL
  AND payload->>'category' = :category AND occurred_at >= now() - make_interval(days => :days)
ORDER BY score DESC, occurred_at DESC, source_id
LIMIT :limit
"""

STYLE_REFS_SQL = """
SELECT source_id FROM ai.memory_facts
WHERE bank_type = 'room' AND bank_id = :room_id AND fact_type = 'COMMENT' AND deleted_at IS NULL
ORDER BY occurred_at DESC, source_id
LIMIT :limit
"""

STRICTNESS_SQL = """
SELECT count(*) AS n, count(*) FILTER (WHERE payload->>'result' = 'guilty') AS guilty
FROM ai.memory_facts
WHERE bank_type = 'room' AND bank_id = :room_id AND fact_type = 'VERDICT' AND deleted_at IS NULL
  AND payload->>'category' = :category
"""

INSERT_EVENT_SQL = """
INSERT INTO ai.processed_memory_events (event_id) VALUES (:event_id)
ON CONFLICT (event_id) DO NOTHING
"""

EPOCHS_SQL = "SELECT scope_key, epoch FROM ai.privacy_epochs WHERE scope_key = ANY(:keys)"

INSERT_FACT_SQL = """
INSERT INTO ai.memory_facts (id, bank_type, bank_id, fact_type, epistemic_type, source_type,
  source_id, source_version, payload, scope, occurred_at)
VALUES (:id, :bank_type, :bank_id, :fact_type, :epistemic_type, :source_type, :source_id,
  :source_version, CAST(:payload AS jsonb), CAST(:scope AS jsonb), :occurred_at)
ON CONFLICT (bank_type, bank_id, source_type, source_id, source_version, fact_type) DO NOTHING
RETURNING id
"""

REVOKE_FACTS_SQL = """
UPDATE ai.memory_facts SET deleted_at = now() WHERE id = ANY(:ids) AND deleted_at IS NULL
"""

# 해석 표 "RULE_HIT 행": 해당 post 의 무효화 안 된 최신 dossier 의 RULE Evidence.
DOSSIER_RULES_SQL = """
WITH latest AS (
  SELECT id FROM ai.dossiers WHERE post_id = :post_id AND invalidated_at IS NULL
  ORDER BY created_at DESC, id LIMIT 1)
SELECT e.text, e.scope, s.source_id, s.source_version
FROM ai.evidence e
JOIN latest d ON e.dossier_id = d.id
JOIN ai.evidence_sources s ON s.evidence_id = e.id AND s.source_type = 'RULE'
WHERE e.fact_type = 'RULE' AND e.invalidated_at IS NULL
ORDER BY e.label, s.source_id, s.source_version
"""

DELETE_USER_SQL = """
UPDATE ai.memory_facts SET deleted_at = now()
WHERE deleted_at IS NULL
  AND ((bank_type = 'user' AND bank_id = :id)
       OR (bank_type = 'room' AND payload->>'author_id' = :id))
"""

DELETE_ROOM_SQL = """
UPDATE ai.memory_facts SET deleted_at = now()
WHERE deleted_at IS NULL AND bank_type = 'room' AND bank_id = :id
"""

DELETE_POST_SQL = """
UPDATE ai.memory_facts SET deleted_at = now()
WHERE deleted_at IS NULL AND payload->>'post_id' = :id
"""

_COMMENT_RE = re.compile(r"--[^\n]*")


def invalidate_statements(path: Path | None = None) -> list[str]:
    """무효화 SQL 파일을 문장 목록으로. 주석을 지우고 `;` 로 나눈다."""
    source = (INVALIDATE_SCOPE_SQL_PATH if path is None else path).read_text(encoding="utf-8")
    body = _COMMENT_RE.sub("", source)
    return [statement.strip() for statement in body.split(";") if statement.strip()]


async def invalidate_scope(
    conn: AsyncConnection, *, source_type: str, source_id: str, scope_key: str
) -> None:
    """무효화 SQL 을 호출자 트랜잭션에서 차례로 실행한다(백엔드 스케줄러 자리)."""
    values = {"t": source_type, "id": source_id, "scope_key": scope_key}
    for sql in invalidate_statements():
        statement = text(sql)
        params = {name: values[name] for name in statement._bindparams}
        await conn.execute(statement, params)


def _json_value(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str | bytes) else value


def _as_datetime(value: datetime | str) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def _candidate(row: Mapping[str, Any]) -> MemoryCandidate:
    return MemoryCandidate(
        source_type=row["source_type"],
        source_id=row["source_id"],
        source_version=int(row["source_version"]),
        score=float(row["score"]),
        fact_type=row["fact_type"],
    )


class PostgresMemory:
    """`ports.memory.MemoryPort` 의 Postgres 구현."""

    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self._engine = engine
        self._settings = settings

    # --- recall -----------------------------------------------------------------

    async def recall_user(
        self,
        user_id: str,
        category: str,
        before: datetime,
        limit: int | None = None,
        *,
        reason: str | None = None,
    ) -> list[MemoryCandidate]:
        """§3.2 점수. `reason` 은 현재 사건 사유다 — 키워드 ILIKE 항에 쓴다(포트 밖 선택 인자)."""
        statement = text(RECALL_USER_SQL).bindparams(
            bindparam("patterns", type_=ARRAY(Text)),
            bindparam("fact_types", type_=ARRAY(Text)),
        )
        params = {
            "user_id": user_id,
            "category": category,
            "before": before,
            "patterns": [_like_pattern(k) for k in extract_keywords(reason)],
            "fact_types": list(_USER_RECALL_FACT_TYPES),
            "recency_days": RECENCY_DAYS,
            "limit": self._settings.RECALL_CANDIDATE_LIMIT if limit is None else limit,
        }
        async with self._engine.connect() as conn:
            result = await conn.execute(statement, params)
            return [_candidate(row) for row in result.mappings()]

    async def recall_room(self, room_id: str, category: str) -> RoomRecall:
        async with self._engine.connect() as conn:
            rules = await conn.execute(
                text(RULES_HIT_SQL),
                {
                    "room_id": room_id,
                    "category": category,
                    "recency_days": RECENCY_DAYS,
                    "days": RULES_HIT_DAYS,
                    "limit": self._settings.RECALL_CANDIDATE_LIMIT,
                },
            )
            rules_hit = [_candidate(row) for row in rules.mappings()]

            style_refs: list[str] = []
            if self._settings.ROOM_COMMENT_STYLE_ENABLED:
                style = await conn.execute(
                    text(STYLE_REFS_SQL),
                    {"room_id": room_id, "limit": self._settings.STYLE_EXAMPLE_LIMIT},
                )
                style_refs = [row[0] for row in style]

            counts = (
                (
                    await conn.execute(
                        text(STRICTNESS_SQL), {"room_id": room_id, "category": category}
                    )
                )
                .mappings()
                .one()
            )
        n = int(counts["n"])
        strictness = (
            RoomStrictness(category_guilty_rate=int(counts["guilty"]) / n, n=n)
            if n >= STRICTNESS_MIN_N
            else None
        )
        return RoomRecall(rules_hit=rules_hit, style_example_refs=style_refs, strictness=strictness)

    # --- retain -----------------------------------------------------------------

    async def retain_verdict(self, event_id: str, verdict_payload: dict[str, Any]) -> int:
        return await self._retain(event_id, verdict_payload)

    async def retain_comment(self, event_id: str, comment_payload: dict[str, Any]) -> int:
        return await self._retain(event_id, comment_payload)

    async def _retain(self, event_id: str, payload: Mapping[str, Any]) -> int:
        """새로 넣어 살아 있는 `memory_facts` 행 수. 처리한 event·epoch 불일치면 0."""
        facts: list[Mapping[str, Any]] = list(payload.get("facts") or [])
        privacy_versions: Sequence[Mapping[str, Any]] = payload.get("privacy_versions") or []
        rule_hit: Mapping[str, Any] | None = payload.get("rule_hit")
        event_uuid = uuid.UUID(str(event_id))

        async with self._engine.connect() as conn:
            tx = await conn.begin()
            try:
                # 늦은 retain (1): 이미 처리한 event 면 아무것도 쓰지 않는다.
                inserted = await conn.execute(text(INSERT_EVENT_SQL), {"event_id": event_uuid})
                if not inserted.rowcount:
                    await tx.rollback()
                    return 0
                # 늦은 retain (3): 스냅샷 epoch 와 지금 epoch 가 하나라도 다르면 쓰지 않는다.
                if not await self._epochs_match(conn, privacy_versions):
                    await tx.rollback()
                    return 0
                if rule_hit is not None:
                    facts.extend(await self._rule_hit_facts(conn, rule_hit))
                ids: list[uuid.UUID] = []
                for fact in facts:
                    result = await conn.execute(text(INSERT_FACT_SQL), self._fact_params(fact))
                    ids.extend(result.scalars())
                await tx.commit()
            except BaseException:
                if tx.is_active:
                    await tx.rollback()
                raise

        if ids and await self._revoke_if_epochs_moved(ids, privacy_versions):
            return 0
        return len(ids)

    async def _revoke_if_epochs_moved(
        self, ids: Sequence[uuid.UUID], privacy_versions: Sequence[Mapping[str, Any]]
    ) -> bool:
        """커밋 뒤 epoch 재확인. 달라졌으면 방금 넣은 행을 소프트 삭제하고 True.

        `ai_worker` 는 `privacy_epochs` 를 잠글 수 없어(SELECT 만) 트랜잭션 안 검사 뒤 커밋 전에
        백엔드가 epoch 를 올리고 무효화 SQL 을 커밋하면, 그 UPDATE 는 미커밋 행을 보지 못한다.
        백엔드는 epoch 를 무효화 SQL 이전 또는 같은 트랜잭션에서 올리므로, 커밋 뒤 다시 보면 그
        창에 들어온 삭제를 놓치지 않는다(코디네이터 9/14, 검증 M1).
        """
        async with self._engine.begin() as conn:
            if await self._epochs_match(conn, privacy_versions):
                return False
            statement = text(REVOKE_FACTS_SQL).bindparams(bindparam("ids", type_=ARRAY(Uuid)))
            await conn.execute(statement, {"ids": list(ids)})
        log.warning("retain_revoked_epoch_moved", rows=len(ids))
        return True

    @staticmethod
    async def _epochs_match(
        conn: AsyncConnection, privacy_versions: Sequence[Mapping[str, Any]]
    ) -> bool:
        expected = {str(pv["scope_key"]): int(pv["epoch"]) for pv in privacy_versions}
        if not expected:
            return True
        statement = text(EPOCHS_SQL).bindparams(bindparam("keys", type_=ARRAY(Text)))
        result = await conn.execute(statement, {"keys": list(expected)})
        current = {row[0]: int(row[1]) for row in result}
        # 테이블에 행이 없으면 epoch 0(해석 표).
        return all(current.get(key, 0) == epoch for key, epoch in expected.items())

    @staticmethod
    async def _rule_hit_facts(
        conn: AsyncConnection, rule_hit: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        """dossier RULE Evidence 마다, 그 Evidence scope 의 방(∩ audience)에 RULE_HIT 한 행."""
        audience = set(rule_hit.get("room_ids") or [])
        result = await conn.execute(text(DOSSIER_RULES_SQL), {"post_id": rule_hit["post_id"]})
        facts: list[dict[str, Any]] = []
        for row in result.mappings():
            scope = _json_value(row["scope"]) or {}
            for room_id in scope.get("room_ids") or []:
                if room_id not in audience:
                    continue
                facts.append(
                    {
                        "bank_type": "room",
                        "bank_id": room_id,
                        "fact_type": "RULE_HIT",
                        "epistemic_type": "DB_RECORD",
                        "source_type": "RULE",
                        "source_id": row["source_id"],
                        "source_version": int(row["source_version"]),
                        "payload": {
                            "post_id": rule_hit["post_id"],
                            "rule_text": row["text"],
                            "rule_version": int(row["source_version"]),
                            "category": rule_hit["category"],
                        },
                        "scope": {"visibility": "ROOMS", "room_ids": [room_id]},
                        "occurred_at": rule_hit["occurred_at"],
                    }
                )
        return facts

    @staticmethod
    def _fact_params(fact: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": uuid.uuid4(),
            "bank_type": fact["bank_type"],
            "bank_id": fact["bank_id"],
            "fact_type": fact["fact_type"],
            "epistemic_type": fact["epistemic_type"],
            "source_type": fact["source_type"],
            "source_id": fact["source_id"],
            "source_version": int(fact["source_version"]),
            "payload": json.dumps(fact["payload"], ensure_ascii=False),
            "scope": json.dumps(fact["scope"], ensure_ascii=False),
            "occurred_at": _as_datetime(fact["occurred_at"]),
        }

    # --- delete -----------------------------------------------------------------

    async def delete_user(self, user_id: str) -> int:
        """사용자 뱅크 전부 + 방 뱅크에 그 사용자가 작성자로 남은 사실(`payload.author_id`)."""
        return await self._soft_delete(DELETE_USER_SQL, user_id)

    async def delete_room(self, room_id: str) -> int:
        return await self._soft_delete(DELETE_ROOM_SQL, room_id)

    async def delete_post(self, post_id: str) -> int:
        """`payload.post_id` 가 그 post 인 사실 전부(SPEND·VERDICT·RULE_HIT·COMMENT)."""
        return await self._soft_delete(DELETE_POST_SQL, post_id)

    async def _soft_delete(self, sql: str, identifier: str) -> int:
        async with self._engine.begin() as conn:
            result = await conn.execute(text(sql), {"id": identifier})
            return int(result.rowcount or 0)
