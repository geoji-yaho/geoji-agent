"""`PostgresMemory`(04 §3.2·§4.2 `test_postgres_memory`, ME-02).

① recall 결과에 payload 없음(참조 필드 5개만)
② score 정렬·limit 20
③ `occurred_at >= before` 제외, `deleted_at` 제외, fact_type 필터
④ retain_verdict 행(SPEND·VERDICT·방 VERDICT×방 수·RULE_HIT) + `processed_memory_events` 1행
⑤ 같은 event 2회 → 0행
⑥ UNIQUE 충돌 무시
⑦ 플래그 off → `style_example_refs=[]`
⑧ strictness n<3 → None
⑨ 키워드 추출(조사 제거·긴 순 3개)
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from geoji_ai.adapters.postgres_memory import PostgresMemory, extract_keywords
from geoji_ai.application.retain_memory import build_verdict_payload
from geoji_ai.ports.memory import MemoryCandidate, MemoryPort
from tests.integration.test_retain_handler import (
    AUTHOR_ID,
    POST_ID,
    ROOM_ID,
    VERDICT_ID,
    fact_rows,
    make_snapshot,
    processed_count,
    seed_epochs,
)
from tests.integration.test_worker_runtime import make_settings

ROOM_B = "room-ddegeoji-02"
USER = "user-recall"
CATEGORY = "교통/택시"
BEFORE = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


async def insert_fact(
    engine: AsyncEngine,
    *,
    bank_type: str = "user",
    bank_id: str = USER,
    fact_type: str = "SPEND",
    source_type: str = "POST",
    source_id: str | None = None,
    source_version: int = 1,
    payload: dict[str, Any] | None = None,
    occurred_at: datetime = BEFORE - timedelta(days=60),
    deleted: bool = False,
) -> str:
    """recall 테스트용 사실 한 행을 직접 넣는다. source_id 를 돌려준다."""
    source_id = source_id or f"src-{uuid4()}"
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO ai.memory_facts (id, bank_type, bank_id, fact_type, epistemic_type, "
                "source_type, source_id, source_version, payload, scope, occurred_at, deleted_at) "
                "VALUES (:id, :bt, :bi, :ft, 'DB_RECORD', :st, :si, :sv, CAST(:p AS jsonb), "
                "CAST(:s AS jsonb), :oa, CASE WHEN :deleted THEN now() END)"
            ),
            {
                "id": uuid4(),
                "bt": bank_type,
                "bi": bank_id,
                "ft": fact_type,
                "st": source_type,
                "si": source_id,
                "sv": source_version,
                "p": json.dumps(payload or {}, ensure_ascii=False),
                "s": json.dumps({"visibility": "ROOMS", "room_ids": [ROOM_ID]}),
                "oa": occurred_at,
                "deleted": deleted,
            },
        )
    return source_id


async def seed_dossier(
    conn: AsyncConnection,
    *,
    post_id: str,
    evidences: list[dict[str, Any]],
    invalidated: bool = False,
) -> str:
    """`ai.dossiers` 1 + `ai.evidence`·`ai.evidence_sources` 여러 행. dossier id 를 돌려준다.

    evidences 항목: `label, fact_type, text, scope, source_type, source_id, source_version`.
    """
    dossier_id = uuid4()
    await conn.execute(
        text(
            "INSERT INTO ai.dossiers (id, post_id, snapshot_hash, label_map, privacy_versions, "
            "invalidated_at) VALUES (:id, :post_id, 'h', '{}'::jsonb, '[]'::jsonb, "
            "CASE WHEN :inv THEN now() END)"
        ),
        {"id": dossier_id, "post_id": post_id, "inv": invalidated},
    )
    for ev in evidences:
        evidence_id = uuid4()
        await conn.execute(
            text(
                "INSERT INTO ai.evidence (id, dossier_id, label, epistemic_type, fact_type, text, "
                "scope) VALUES (:id, :d, :label, 'DB_RECORD', :ft, :text, CAST(:scope AS jsonb))"
            ),
            {
                "id": evidence_id,
                "d": dossier_id,
                "label": ev["label"],
                "ft": ev["fact_type"],
                "text": ev["text"],
                "scope": json.dumps(ev["scope"]),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO ai.evidence_sources (evidence_id, source_type, source_id, "
                "source_version) VALUES (:e, :st, :si, :sv)"
            ),
            {
                "e": evidence_id,
                "st": ev["source_type"],
                "si": ev["source_id"],
                "sv": ev["source_version"],
            },
        )
    return str(dossier_id)


def _memory(engine: AsyncEngine, **settings: Any) -> PostgresMemory:
    return PostgresMemory(engine, make_settings(**settings))


# --- recall ----------------------------------------------------------------------


def test_포트를_구현한다(engine: AsyncEngine):
    assert isinstance(_memory(engine), MemoryPort)


async def test_recall_은_payload_없이_참조_5필드만(engine: AsyncEngine):  # ①
    await insert_fact(
        engine,
        payload={"post_id": "p1", "category": CATEGORY, "reason": "비밀 사유 원문"},
    )

    found = await _memory(engine).recall_user(USER, CATEGORY, BEFORE, 20)

    assert len(found) == 1
    candidate = found[0]
    assert isinstance(candidate, MemoryCandidate)
    assert {f.name for f in dataclasses.fields(candidate)} == {
        "source_type",
        "source_id",
        "source_version",
        "score",
        "fact_type",
    }
    assert not hasattr(candidate, "payload")
    assert "비밀 사유 원문" not in repr(candidate)


async def test_recall_은_score_순_limit_20(engine: AsyncEngine):  # ②
    # 점수 4: 카테고리 2 + 키워드 1 + 30일 안 1
    best = await insert_fact(
        engine,
        payload={"post_id": "best", "category": CATEGORY, "reason": "늦잠 자서 택시"},
        occurred_at=BEFORE - timedelta(days=3),
    )
    # 점수 3: 카테고리 2 + 30일 안 1
    second = await insert_fact(
        engine,
        payload={"post_id": "second", "category": CATEGORY, "reason": "비 와서"},
        occurred_at=BEFORE - timedelta(days=5),
    )
    # VERDICT 는 같은 post 의 SPEND 에서 카테고리·사유를 가져온다 → 점수 4
    verdict = await insert_fact(
        engine,
        fact_type="VERDICT",
        source_type="VERDICT",
        payload={"post_id": "best", "result": "guilty"},
        occurred_at=BEFORE - timedelta(days=2),
    )
    # 점수 0: 다른 카테고리·60일 전. 25개로 limit 을 넘긴다.
    for index in range(25):
        await insert_fact(engine, payload={"post_id": f"old-{index}", "category": "식비"})

    found = await _memory(engine).recall_user(USER, CATEGORY, BEFORE, 20, reason="택시를 늦잠")

    assert len(found) == 20
    scores = [c.score for c in found]
    assert scores == sorted(scores, reverse=True)
    assert {found[0].source_id, found[1].source_id} == {best, verdict}
    assert found[0].score == 4.0 and found[1].score == 4.0
    assert (found[2].source_id, found[2].score) == (second, 3.0)
    assert found[3].score == 0.0


async def test_recall_기본_limit_은_20(engine: AsyncEngine):  # ②
    for index in range(22):
        await insert_fact(engine, payload={"post_id": f"p-{index}", "category": CATEGORY})

    found = await _memory(engine).recall_user(USER, CATEGORY, BEFORE)

    assert len(found) == 20


async def test_recall_은_before_이후_삭제분_다른_fact_type_을_뺀다(engine: AsyncEngine):  # ③
    kept = await insert_fact(engine, payload={"post_id": "k", "category": CATEGORY})
    mitigation = await insert_fact(
        engine, fact_type="MITIGATION", payload={"post_id": "k", "text": "감경"}
    )
    await insert_fact(engine, payload={"post_id": "at"}, occurred_at=BEFORE)
    await insert_fact(engine, payload={"post_id": "after"}, occurred_at=BEFORE + timedelta(1))
    await insert_fact(engine, payload={"post_id": "deleted"}, deleted=True)
    await insert_fact(engine, fact_type="COMMENT", source_type="COMMENT", payload={})
    await insert_fact(engine, fact_type="RULE_HIT", source_type="RULE", payload={})
    await insert_fact(engine, bank_id="someone-else", payload={"post_id": "other"})
    await insert_fact(engine, bank_type="room", bank_id=USER, payload={"post_id": "room"})

    found = await _memory(engine).recall_user(USER, CATEGORY, BEFORE, 20)

    assert {c.source_id for c in found} == {kept, mitigation}


async def test_recall_room_rules_hit_은_90일_안(engine: AsyncEngine):
    recent = await insert_fact(
        engine,
        bank_type="room",
        bank_id=ROOM_ID,
        fact_type="RULE_HIT",
        source_type="RULE",
        payload={"post_id": "p", "category": CATEGORY, "rule_text": "택시 금지", "rule_version": 1},
        occurred_at=datetime.now(UTC) - timedelta(days=10),
    )
    await insert_fact(
        engine,
        bank_type="room",
        bank_id=ROOM_ID,
        fact_type="RULE_HIT",
        source_type="RULE",
        payload={"post_id": "p", "category": CATEGORY},
        occurred_at=datetime.now(UTC) - timedelta(days=91),
    )

    recall = await _memory(engine).recall_room(ROOM_ID, CATEGORY)

    assert [c.source_id for c in recall.rules_hit] == [recent]
    assert recall.rules_hit[0].fact_type == "RULE_HIT"


async def test_플래그_off_면_style_example_refs_빈_목록(engine: AsyncEngine):  # ⑦
    for index in range(5):
        await insert_fact(
            engine,
            bank_type="room",
            bank_id=ROOM_ID,
            fact_type="COMMENT",
            source_type="COMMENT",
            source_id=f"c-{index}",
            payload={"comment_id": f"c-{index}"},
            occurred_at=datetime.now(UTC) - timedelta(days=index),
        )

    off = await _memory(engine, ROOM_COMMENT_STYLE_ENABLED=False).recall_room(ROOM_ID, CATEGORY)
    on = await _memory(engine, ROOM_COMMENT_STYLE_ENABLED=True).recall_room(ROOM_ID, CATEGORY)

    assert off.style_example_refs == []
    # 대조: 켜면 `STYLE_EXAMPLE_LIMIT`(3) 만큼 최신 순.
    assert on.style_example_refs == ["c-0", "c-1", "c-2"]


async def test_strictness_는_n_3_미만이면_None(engine: AsyncEngine):  # ⑧
    memory = _memory(engine)

    async def add(result: str) -> None:
        await insert_fact(
            engine,
            bank_type="room",
            bank_id=ROOM_ID,
            fact_type="VERDICT",
            source_type="VERDICT",
            payload={"post_id": str(uuid4()), "category": CATEGORY, "result": result},
        )

    await add("guilty")
    await add("notGuilty")
    await insert_fact(  # 다른 카테고리는 세지 않는다
        engine,
        bank_type="room",
        bank_id=ROOM_ID,
        fact_type="VERDICT",
        source_type="VERDICT",
        payload={"post_id": "x", "category": "식비", "result": "guilty"},
    )
    assert (await memory.recall_room(ROOM_ID, CATEGORY)).strictness is None

    await add("guilty")
    strictness = (await memory.recall_room(ROOM_ID, CATEGORY)).strictness
    assert strictness is not None
    assert strictness.n == 3
    assert abs(strictness.category_guilty_rate - 2 / 3) < 1e-9


def test_키워드_추출은_조사를_떼고_긴_순_3개():  # ⑨
    assert extract_keywords("늦잠 자서 택시를 탔는데 기분이 좋았다") == ["탔는데", "좋았다", "늦잠"]
    # 에서·으로 제거. 2자 검사가 조사 제거보다 먼저라(§3.2 순서) "집으로" → "집" 은 남는다.
    assert extract_keywords("회사에서 집으로 택시") == ["회사", "택시", "집"]
    assert extract_keywords("아주 오래된 택시를 새벽에 불렀다") == ["오래된", "불렀다", "아주"]
    assert extract_keywords("a 나 택시") == ["택시"]  # 1자 토큰 제외
    assert extract_keywords("택시를 택시가 택시") == ["택시"]  # 중복 제거
    assert extract_keywords(None) == []
    assert extract_keywords("") == []


# --- retain ----------------------------------------------------------------------


def _two_room_snapshot():
    base = make_snapshot()
    audience = base.audience.model_dump() | {"room_ids": [ROOM_ID, ROOM_B]}
    room_snapshots = [
        {"room_id": ROOM_ID, "intensity": "spicy", "rule_version": 1},
        {"room_id": ROOM_B, "intensity": "hell", "rule_version": 1},
    ]
    privacy_versions = [pv.model_dump() for pv in base.privacy_versions] + [
        {"scope_key": f"room:{ROOM_B}", "epoch": 1}
    ]
    return make_snapshot(
        audience=audience, room_snapshots=room_snapshots, privacy_versions=privacy_versions
    )


_EPOCHS_TWO_ROOMS = {f"user:{AUTHOR_ID}": 1, f"room:{ROOM_ID}": 1, f"room:{ROOM_B}": 1}


async def test_retain_verdict_행과_processed_1행(  # ④
    engine: AsyncEngine, privacy_epochs: str
):
    await seed_epochs(engine, _EPOCHS_TWO_ROOMS)
    async with engine.begin() as conn:
        # 방 A 규칙에 걸린 RULE Evidence 1개. 무효화된 옛 dossier 는 쓰지 않는다.
        await seed_dossier(
            conn,
            post_id=POST_ID,
            evidences=[
                {
                    "label": "F2",
                    "fact_type": "RULE",
                    "text": "옛 규칙",
                    "scope": {"visibility": "ROOMS", "room_ids": [ROOM_ID]},
                    "source_type": "RULE",
                    "source_id": "rule-old",
                    "source_version": 1,
                }
            ],
            invalidated=True,
        )
        await seed_dossier(
            conn,
            post_id=POST_ID,
            evidences=[
                {
                    "label": "F2",
                    "fact_type": "RULE",
                    "text": "출근길 택시 금지",
                    "scope": {"visibility": "ROOMS", "room_ids": [ROOM_ID]},
                    "source_type": "RULE",
                    "source_id": "rule-taxi",
                    "source_version": 3,
                },
                {
                    "label": "F0",
                    "fact_type": "SPEND",
                    "text": "택시 12,000원",
                    "scope": {"visibility": "ROOMS", "room_ids": [ROOM_ID, ROOM_B]},
                    "source_type": "POST",
                    "source_id": POST_ID,
                    "source_version": 1,
                },
            ],
        )
    payload = build_verdict_payload(_two_room_snapshot())
    assert payload is not None

    written = await _memory(engine).retain_verdict(str(uuid4()), payload)

    rows = await fact_rows(engine)
    assert written == len(rows) == 5
    assert sorted((r["bank_type"], r["bank_id"], r["fact_type"]) for r in rows) == [
        ("room", ROOM_ID, "RULE_HIT"),
        ("room", ROOM_ID, "VERDICT"),
        ("room", ROOM_B, "VERDICT"),
        ("user", AUTHOR_ID, "SPEND"),
        ("user", AUTHOR_ID, "VERDICT"),
    ]
    rule = next(r for r in rows if r["fact_type"] == "RULE_HIT")
    assert (rule["source_type"], rule["source_id"], rule["source_version"]) == (
        "RULE",
        "rule-taxi",
        3,
    )
    assert rule["payload"] == {
        "post_id": POST_ID,
        "rule_text": "출근길 택시 금지",
        "rule_version": 3,
        "category": CATEGORY,
    }
    assert rule["scope"] == {"visibility": "ROOMS", "room_ids": [ROOM_ID]}
    room_b = next(r for r in rows if r["bank_id"] == ROOM_B)
    assert room_b["source_id"] == VERDICT_ID
    assert room_b["payload"]["intensity"] == "hell"
    assert room_b["scope"] == {"visibility": "ROOMS", "room_ids": [ROOM_B]}
    assert await processed_count(engine) == 1


async def test_retain_verdict_dossier_없으면_RULE_HIT_0(engine: AsyncEngine, privacy_epochs: str):
    await seed_epochs(engine, _EPOCHS_TWO_ROOMS)
    payload = build_verdict_payload(_two_room_snapshot())
    assert payload is not None

    written = await _memory(engine).retain_verdict(str(uuid4()), payload)

    assert written == 4
    assert all(r["fact_type"] != "RULE_HIT" for r in await fact_rows(engine))


async def test_같은_event_2회면_0행(engine: AsyncEngine, privacy_epochs: str):  # ⑤
    await seed_epochs(engine, _EPOCHS_TWO_ROOMS)
    payload = build_verdict_payload(_two_room_snapshot())
    assert payload is not None
    memory = _memory(engine)
    event_id = str(uuid4())

    first = await memory.retain_verdict(event_id, payload)
    second = await memory.retain_verdict(event_id, payload)

    assert (first, second) == (4, 0)
    assert len(await fact_rows(engine)) == 4
    assert await processed_count(engine) == 1


async def test_UNIQUE_충돌은_무시한다(engine: AsyncEngine, privacy_epochs: str):  # ⑥
    await seed_epochs(engine, _EPOCHS_TWO_ROOMS)
    payload = build_verdict_payload(_two_room_snapshot())
    assert payload is not None
    memory = _memory(engine)

    first = await memory.retain_verdict(str(uuid4()), payload)
    # 다른 event 가 같은 사실을 다시 가져왔다(재발행). 오류 없이 0행.
    second = await memory.retain_verdict(str(uuid4()), payload)

    assert (first, second) == (4, 0)
    assert len(await fact_rows(engine)) == 4
    assert await processed_count(engine) == 2
