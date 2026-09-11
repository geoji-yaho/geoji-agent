"""어댑터와 규약이 포트·설정에서 떨어져 나가지 않게 묶는다(02 §3.2·§3.3·§3.4).

DB 를 부르지 않는다. 게이트에 타입 체커가 없어(ruff 만) 이름이 어긋나면 런타임까지
가므로, 값이 아니라 **모양**을 단언한다.
"""

from __future__ import annotations

import inspect

from geoji_ai.adapters.postgres_jobs import PostgresJobs
from geoji_ai.core.config import Settings
from geoji_ai.ports.jobs import JobsPort
from geoji_ai.workers.dispatch import (
    DEFAULT_ROUTE_BY_KIND,
    JOB_ROUTES,
    ROUTES_BY_KIND,
    SLOT_KINDS,
    build_dedupe_key,
)

KINDS = ("PREPARE", "SENTENCE", "TEXT_RETRY", "RETAIN")


def test_PostgresJobs_는_JobsPort_를_따른다():
    # `JobsPort` 는 runtime_checkable Protocol 이라 메서드 존재를 본다.
    # 01 소유라 포트를 못 고치므로, 어댑터가 어긋나면 여기서 잡힌다.
    jobs = PostgresJobs(engine=None, lease_s=15)  # type: ignore[arg-type]
    assert isinstance(jobs, JobsPort)


def test_어댑터_메서드_시그니처가_포트와_같다():
    for name in ("claim", "heartbeat", "complete", "fail", "release"):
        port_sig = inspect.signature(getattr(JobsPort, name))
        adapter_sig = inspect.signature(getattr(PostgresJobs, name))
        assert list(port_sig.parameters) == list(adapter_sig.parameters), name


def test_슬롯_이름이_설정과_dispatch_에서_같다():
    # `WORKER_SLOTS` 는 환경변수(JSON)로 덮을 수 있다. 기본값이 어긋나면 슬롯 loop 이 죽는다.
    assert set(Settings(_env_file=None).WORKER_SLOTS) <= set(SLOT_KINDS)


def test_슬롯이_네_kind_를_빠짐없이_한_번씩_덮는다():
    covered = [kind for kinds in SLOT_KINDS.values() for kind in kinds]
    assert sorted(covered) == sorted(KINDS)
    assert len(covered) == len(set(covered))


def test_JOB_ROUTES_는_02_3_4_표_다섯_행이다():
    assert set(JOB_ROUTES) == {
        "post.created",
        "verdict.confirmed",
        "sentence.finalized",
        "verdict.text_retry",
        "comment.approved",
    }
    for event_type, route in JOB_ROUTES.items():
        assert route.event_type == event_type
        assert route.kind in KINDS
        assert route.max_attempts > 0
    assert set(ROUTES_BY_KIND) == set(KINDS)
    assert set(DEFAULT_ROUTE_BY_KIND) == set(KINDS)
    # RETAIN 은 행이 둘이고 기본은 판결 저장이다.
    assert len(ROUTES_BY_KIND["RETAIN"]) == 2
    assert DEFAULT_ROUTE_BY_KIND["RETAIN"].event_type == "sentence.finalized"


def test_dedupe_key_가_규약_문자열과_같다():
    assert (
        build_dedupe_key(
            JOB_ROUTES["post.created"],
            {"post_id": "p1", "post_version": 1, "audience_version": 2},
        )
        == "prepare:p1:1:2"
    )
    assert (
        build_dedupe_key(
            JOB_ROUTES["verdict.confirmed"], {"verdict_id": "v1", "verdict_version": 3}
        )
        == "sentence:v1:3"
    )
    assert (
        build_dedupe_key(JOB_ROUTES["sentence.finalized"], {"verdict_id": "v1", "version": 3})
        == "retain:verdict:v1:3"
    )
    assert (
        build_dedupe_key(
            JOB_ROUTES["verdict.text_retry"],
            {"verdict_id": "v1", "verdict_version": 3, "round": 2},
        )
        == "text-retry:v1:3:2"
    )
    assert (
        build_dedupe_key(JOB_ROUTES["comment.approved"], {"comment_id": "c1", "version": 1})
        == "retain:comment:c1:1"
    )
