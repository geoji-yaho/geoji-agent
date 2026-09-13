"""DB 로 계산하는 지표와 trial trace(08 §3.3, 10 §4.5 제안).

컬럼은 DDL 001(`ai.jobs`)·002(`ai.dossiers`·`ai.evidence`·`ai.evidence_sources`)·
003(`ai.llm_calls`).
백분위는 `percentile_disc`(nearest-rank) — `telemetry.metrics.percentile` 과 같은 값이다.

`queue_wait_seconds` 한계: `ai.jobs` 에 claim 시각 컬럼이 없다. claim 된 적 있는 행(`attempts > 0`,
RUNNING 이후 상태)의 `updated_at - available_at` 으로 근사한다. heartbeat·완료·재시도가
`updated_at` 을 다시 쓰므로 실제 대기의 **상한**이다(RUNNING 은 heartbeat 전까지만 정확).

trace 는 라벨·코드·개수·시각만 낸다. `evidence.text`(원문), evidence·dossier·generation id,
`label_map` 의 값(evidence id), `scope.room_ids`, `evidence_sources.source_id` 는 내보내지 않는다.
출처는 `source_type` 별 서로 다른 `source_id` 개수로만 낸다.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

__all__ = ["QUEUE_OLDEST_AGE_SECONDS", "PostgresTelemetry"]

#: 알림(queue oldest age)용 DB 게이지. §3.3 지표 표 밖이라 `METRICS` 에 없다.
QUEUE_OLDEST_AGE_SECONDS = "queue_oldest_age_seconds"

_PCT = (
    "percentile_disc(0.5) WITHIN GROUP (ORDER BY v) AS p50, "
    "percentile_disc(0.95) WITHIN GROUP (ORDER BY v) AS p95, "
    "percentile_disc(0.99) WITHIN GROUP (ORDER BY v) AS p99"
)

_QUEUE_WAIT_SQL = text(
    f"""
    SELECT kind, count(*) AS count, sum(v) AS sum, {_PCT}
    FROM (
        SELECT kind, GREATEST(0, EXTRACT(EPOCH FROM (updated_at - available_at)))::float8 AS v
        FROM ai.jobs
        WHERE status IN ('RUNNING', 'SUCCEEDED', 'FAILED') AND attempts > 0
          AND (CAST(:since AS timestamptz) IS NULL OR updated_at >= CAST(:since AS timestamptz))
    ) t
    GROUP BY kind ORDER BY kind
    """
)

_QUEUE_OLDEST_SQL = text(
    """
    SELECT kind, EXTRACT(EPOCH FROM (now() - min(available_at)))::float8 AS value
    FROM ai.jobs
    WHERE status = 'QUEUED' AND available_at <= now()
    GROUP BY kind ORDER BY kind
    """
)

_LLM_DURATION_SQL = text(
    f"""
    SELECT node, vendor, count(*) AS count, sum(v) AS sum, {_PCT}
    FROM (
        SELECT node, vendor, EXTRACT(EPOCH FROM (finished_at - started_at))::float8 AS v
        FROM ai.llm_calls
        WHERE started_at IS NOT NULL AND finished_at IS NOT NULL
          AND (CAST(:since AS timestamptz) IS NULL OR started_at >= CAST(:since AS timestamptz))
    ) t
    GROUP BY node, vendor ORDER BY node, vendor
    """
)

_CASE_COST_SQL = text(
    f"""
    SELECT vendor, count(*) AS count, sum(v)::float8 AS sum, {_PCT}
    FROM (
        SELECT vendor, post_id, sum(actual_micro_usd)::float8 AS v
        FROM ai.llm_calls
        WHERE actual_micro_usd IS NOT NULL
          AND (CAST(:since AS timestamptz) IS NULL OR started_at >= CAST(:since AS timestamptz))
        GROUP BY vendor, post_id
    ) t
    GROUP BY vendor ORDER BY vendor
    """
)

_UNKNOWN_CALLS_SQL = text(
    """
    SELECT vendor, count(*) AS value
    FROM ai.llm_calls
    WHERE status = 'UNKNOWN'
      AND (CAST(:since AS timestamptz) IS NULL OR started_at >= CAST(:since AS timestamptz))
    GROUP BY vendor ORDER BY vendor
    """
)

_LATEST_DOSSIER_SQL = text(
    """
    SELECT id, label_map, created_at, invalidated_at
    FROM ai.dossiers WHERE post_id = :post_id
    ORDER BY created_at DESC, id LIMIT 1
    """
)

_EVIDENCE_SQL = text(
    """
    SELECT label, epistemic_type, fact_type, scope, occurred_at, invalidated_at
    FROM ai.evidence WHERE dossier_id = :dossier_id
    """
)

_SOURCES_SQL = text(
    """
    SELECT e.label, s.source_type, count(DISTINCT s.source_id) AS count
    FROM ai.evidence e JOIN ai.evidence_sources s ON s.evidence_id = e.id
    WHERE e.dossier_id = :dossier_id
    GROUP BY e.label, s.source_type ORDER BY e.label, s.source_type
    """
)

_TIMELINE_SQL = text(
    """
    SELECT node, call_index, vendor, model_id, status, prompt_tokens, completion_tokens,
           estimated_max_micro_usd, actual_micro_usd, started_at, finished_at
    FROM ai.llm_calls WHERE post_id = :post_id
    ORDER BY started_at NULLS LAST, node, call_index
    """
)


def _label_key(label: str) -> tuple[Any, ...]:
    """`E2` < `E10` 가 되도록 숫자 조각을 숫자로 비교한다."""
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", label))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _histogram(name: str, labels: dict[str, str], row: Any) -> dict[str, Any]:
    return {
        "name": name,
        "kind": "histogram",
        "source": "db",
        "labels": labels,
        "count": int(row["count"]),
        "sum": float(row["sum"]),
        "p50": float(row["p50"]),
        "p95": float(row["p95"]),
        "p99": float(row["p99"]),
    }


def _value(name: str, kind: str, labels: dict[str, str], value: float) -> dict[str, Any]:
    return {"name": name, "kind": kind, "source": "db", "labels": labels, "value": value}


class PostgresTelemetry:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def _rows(self, sql: Any, **params: Any) -> list[Any]:
        async with self._engine.connect() as conn:
            return list((await conn.execute(sql, params)).mappings().all())

    async def queue_wait_seconds(self, since: datetime | None = None) -> list[dict[str, Any]]:
        rows = await self._rows(_QUEUE_WAIT_SQL, since=since)
        return [_histogram("queue_wait_seconds", {"kind": r["kind"]}, r) for r in rows]

    async def queue_oldest_age_seconds(self) -> list[dict[str, Any]]:
        rows = await self._rows(_QUEUE_OLDEST_SQL)
        return [
            _value(QUEUE_OLDEST_AGE_SECONDS, "gauge", {"kind": r["kind"]}, float(r["value"]))
            for r in rows
        ]

    async def oldest_queued_age_seconds(self) -> float | None:
        """kind 를 가리지 않은 최대값. `QueueAgeWatch.observe` 에 넣는다.

        대기 job 이 없으면 `None`.
        """
        series = await self.queue_oldest_age_seconds()
        return max((s["value"] for s in series), default=None)

    async def llm_duration_seconds(self, since: datetime | None = None) -> list[dict[str, Any]]:
        rows = await self._rows(_LLM_DURATION_SQL, since=since)
        return [
            _histogram("llm_duration_seconds", {"node": r["node"], "vendor": r["vendor"]}, r)
            for r in rows
        ]

    async def case_cost_micro_usd(self, since: datetime | None = None) -> list[dict[str, Any]]:
        """사건(post)·벤더별 실지출 합의 분포. `actual_micro_usd` 가 있는 호출만."""
        rows = await self._rows(_CASE_COST_SQL, since=since)
        return [_histogram("case_cost_micro_usd", {"vendor": r["vendor"]}, r) for r in rows]

    async def unknown_calls(self, since: datetime | None = None) -> list[dict[str, Any]]:
        rows = await self._rows(_UNKNOWN_CALLS_SQL, since=since)
        return [
            _value("unknown_calls", "counter", {"vendor": r["vendor"]}, int(r["value"]))
            for r in rows
        ]

    async def snapshot_series(self, since: datetime | None = None) -> list[dict[str, Any]]:
        return [
            *await self.queue_wait_seconds(since),
            *await self.queue_oldest_age_seconds(),
            *await self.llm_duration_seconds(since),
            *await self.case_cost_micro_usd(since),
            *await self.unknown_calls(since),
        ]

    async def trace(self, post_id: str) -> dict[str, Any] | None:
        """post 의 최신 dossier·출처 개수·노드 타임라인·비용. 둘 다 없으면 `None`."""
        dossier_rows = await self._rows(_LATEST_DOSSIER_SQL, post_id=post_id)
        call_rows = await self._rows(_TIMELINE_SQL, post_id=post_id)
        if not dossier_rows and not call_rows:
            return None
        dossier = await self._dossier(dossier_rows[0]) if dossier_rows else None
        return {
            "post_id": post_id,
            "dossier": dossier,
            "timeline": [_timeline_item(r) for r in call_rows],
            "cost": _cost(call_rows),
        }

    async def _dossier(self, row: Any) -> dict[str, Any]:
        dossier_id = row["id"]
        evidence_rows = await self._rows(_EVIDENCE_SQL, dossier_id=dossier_id)
        source_rows = await self._rows(_SOURCES_SQL, dossier_id=dossier_id)
        sources: dict[str, list[dict[str, Any]]] = {}
        for s in source_rows:
            sources.setdefault(s["label"], []).append(
                {"source_type": s["source_type"], "count": int(s["count"])}
            )
        evidence = []
        for e in sorted(evidence_rows, key=lambda r: _label_key(r["label"])):
            scope = _json(e["scope"]) or {}
            evidence.append(
                {
                    "label": e["label"],
                    "epistemic_type": e["epistemic_type"],
                    "fact_type": e["fact_type"],
                    "scope": {
                        "visibility": scope.get("visibility"),
                        "room_count": len(scope.get("room_ids") or []),
                    },
                    "occurred_at": _iso(e["occurred_at"]),
                    "invalidated": e["invalidated_at"] is not None,
                    "sources": sources.get(e["label"], []),
                }
            )
        label_map = _json(row["label_map"]) or {}
        return {
            "labels": sorted(label_map, key=_label_key),
            "created_at": _iso(row["created_at"]),
            "invalidated": row["invalidated_at"] is not None,
            "evidence": evidence,
        }


def _timeline_item(r: Any) -> dict[str, Any]:
    started, finished = r["started_at"], r["finished_at"]
    duration_ms = (
        round((finished - started).total_seconds() * 1000)
        if started is not None and finished is not None
        else None
    )
    return {
        "node": r["node"],
        "call_index": r["call_index"],
        "vendor": r["vendor"],
        "model_id": r["model_id"],
        "status": r["status"],
        "started_at": _iso(started),
        "finished_at": _iso(finished),
        "duration_ms": duration_ms,
        "prompt_tokens": r["prompt_tokens"],
        "completion_tokens": r["completion_tokens"],
        "actual_micro_usd": r["actual_micro_usd"],
    }


def _cost(rows: list[Any]) -> dict[str, int]:
    unknown = [r for r in rows if r["status"] == "UNKNOWN"]
    return {
        "actual_micro_usd": sum(int(r["actual_micro_usd"] or 0) for r in rows),
        "unknown_calls": len(unknown),
        "unknown_estimated_max_micro_usd": sum(int(r["estimated_max_micro_usd"]) for r in unknown),
        "calls": len(rows),
    }
