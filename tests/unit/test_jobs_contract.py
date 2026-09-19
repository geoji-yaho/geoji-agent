"""어댑터와 규약이 포트·설정에서 떨어져 나가지 않게 묶는다(02 §3.2·§3.3·§3.4).

DB 를 부르지 않는다. 게이트에 타입 체커가 없어(ruff 만) 이름이 어긋나면 런타임까지
가므로, 값이 아니라 **모양**을 단언한다.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from geoji_ai.adapters.postgres_jobs import PostgresJobs
from geoji_ai.contracts.jobs import JuryVotePayload, TextRetryPayload
from geoji_ai.core.config import Settings
from geoji_ai.domain.intensity import Intensity
from geoji_ai.ports.jobs import JobsPort
from geoji_ai.workers.dispatch import (
    DEFAULT_ROUTE_BY_KIND,
    JOB_ROUTES,
    ROUTES_BY_KIND,
    SLOT_KINDS,
    build_dedupe_key,
    handler_for,
)

KINDS = ("PREPARE", "SENTENCE", "TEXT_RETRY", "RETAIN", "JURY_VOTE")


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


def test_슬롯이_다섯_kind_를_빠짐없이_한_번씩_덮는다():
    covered = [kind for kinds in SLOT_KINDS.values() for kind in kinds]
    assert sorted(covered) == sorted(KINDS)
    assert len(covered) == len(set(covered))


def test_JOB_ROUTES_는_02_3_4_표_다섯_행과_18_3_1_한_행이다():
    assert set(JOB_ROUTES) == {
        "post.created",
        "verdict.confirmed",
        "sentence.finalized",
        "verdict.text_retry",
        "comment.approved",
        "jury.vote_requested",
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
    assert (
        build_dedupe_key(
            JOB_ROUTES["jury.vote_requested"],
            {"post_id": "p1", "post_version": 1, "room_id": "r1", "voter_id": "bot1"},
        )
        == "jury-vote:p1:r1:bot1"
    )


# --- JURY_VOTE(18 §3.1 = 10 §3 새 행) ---------------------------------------------

_JURY_BASE = {"post_id": "p1", "post_version": 1, "room_id": "r1", "voter_id": "bot1"}


def test_JURY_VOTE_payload_는_네_키를_그대로_받는다():
    payload = JuryVotePayload.model_validate(_JURY_BASE)
    assert (payload.post_id, payload.post_version, payload.room_id, payload.voter_id) == (
        "p1",
        1,
        "r1",
        "bot1",
    )


@pytest.mark.parametrize("missing", sorted(_JURY_BASE))
def test_JURY_VOTE_payload_는_네_키가_모두_필수다(missing: str):
    body = {key: value for key, value in _JURY_BASE.items() if key != missing}
    with pytest.raises(ValidationError):
        JuryVotePayload.model_validate(body)


def test_JURY_VOTE_payload_는_모르는_필드를_거부한다():
    with pytest.raises(ValidationError):
        JuryVotePayload.model_validate({**_JURY_BASE, "extra": 1})


def test_JURY_VOTE_규약이_18_3_1_표와_같다():
    route = JOB_ROUTES["jury.vote_requested"]
    assert (route.kind, route.priority, route.max_attempts) == ("JURY_VOTE", 60, 2)
    assert route.deadline_after_s is None
    assert route.aggregate_fields == ("post_id", "post_version")
    assert DEFAULT_ROUTE_BY_KIND["JURY_VOTE"] is route


def test_JURY_슬롯과_핸들러가_붙어_있다():
    # BACKGROUND 에 넣지 않는다 — TEXT_RETRY 뒤에 줄을 서면 데모의 5~10초 약속이 깨진다.
    assert SLOT_KINDS["JURY"] == ("JURY_VOTE",)
    assert "JURY_VOTE" not in SLOT_KINDS["BACKGROUND"]
    assert type(handler_for("JURY_VOTE")).__name__ == "JuryVoteHandler"


# --- TEXT_RETRY payload `intensities[]`(10 §3 표, 10 §4.5 10번 제안) ----------------------

_RETRY_BASE = {"verdict_id": "v1", "verdict_version": 1, "round": 1}


def test_TEXT_RETRY_intensities_없으면_None_이다():
    assert TextRetryPayload.model_validate(_RETRY_BASE).intensities is None


def test_TEXT_RETRY_intensities_는_소문자_강도_목록이다():
    payload = TextRetryPayload.model_validate({**_RETRY_BASE, "intensities": ["hell", "spicy"]})
    assert payload.intensities == [Intensity.hell, Intensity.spicy]


@pytest.mark.parametrize("bad", [[], ["HELL"], ["extreme"], "hell"])
def test_TEXT_RETRY_intensities_빈_목록과_모르는_값은_거부한다(bad):
    with pytest.raises(ValidationError):
        TextRetryPayload.model_validate({**_RETRY_BASE, "intensities": bad})


def test_TEXT_RETRY_payload_는_모르는_필드를_거부한다():
    with pytest.raises(ValidationError):
        TextRetryPayload.model_validate({**_RETRY_BASE, "extra": 1})


def test_마감_규약이_백엔드_JobKind_와_같다():
    """9/16: SENTENCE 90초(백엔드 `JobKind.SENTENCE`), TEXT_RETRY 60초(설정과 같은 값).

    PREPARE·RETAIN 은 마감 없음. 10 §3 표.
    """
    assert JOB_ROUTES["verdict.confirmed"].deadline_after_s == 90
    assert JOB_ROUTES["verdict.text_retry"].deadline_after_s == 60
    assert JOB_ROUTES["post.created"].deadline_after_s is None
    assert JOB_ROUTES["sentence.finalized"].deadline_after_s is None
    assert JOB_ROUTES["comment.approved"].deadline_after_s is None
    assert JOB_ROUTES["jury.vote_requested"].deadline_after_s is None
