"""백엔드 클라이언트(03 §3.2·§4.2, VF-02).

httpx `MockTransport` 로 응답을 위조한다. ⑦ 은 가짜 백엔드(`ASGITransport`)로 finalize
commit record 멱등을 본다. "먼저 실패시킬 케이스" — finalize 응답 유실 → 같은 요청
재전송이 같은 200.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import structlog
from pydantic import ValidationError

from geoji_ai.adapters.backend_http import (
    CONNECT_TIMEOUT_S,
    RETRY_BACKOFF_S,
    TIMEOUTS_S,
    BackendHttp,
    BackendRejected,
    BackendUnavailable,
    bind_trace,
)
from geoji_ai.contracts.finalize import FinalizeRequest
from geoji_ai.ports.backend import EvidenceCandidate, ResolveEvidenceRequest
from tests.conftest import load_fixture
from tests.fakes.backend_app import FAKE_SERVICE_TOKEN, create_fake_backend

TOKEN = "tok-very-secret-9f3a"
BASE_URL = "http://backend.test"
JOB_ID = "job-1"
GENERATION_ID = "gen-1"
VERDICT_ID = "7a1d9c40-3b52-4e18-9f0a-2c6d8b4e1f31"


def finalize_request(
    *, job_id: str = JOB_ID, generation_id: str = GENERATION_ID, draft_hash: str = "a" * 64
) -> FinalizeRequest:
    case = load_fixture("case-snapshot-taxi")
    return FinalizeRequest.model_validate(
        {
            "schema_version": 1,
            "job_id": job_id,
            "generation_id": generation_id,
            "verdict_version": 1,
            "expected_text_version": 0,
            "dossier_id": "dossier-taxi",
            "privacy_versions": case["privacy_versions"],
            "draft_hash": draft_hash,
            "sentencing": load_fixture("sentencing-taxi"),
            "draft": load_fixture("writer-draft-taxi"),
            "evaluation": load_fixture("evaluation-taxi-pass"),
            "evaluation_draft_hash": draft_hash,
            "prompt_bundle_version": "bundle-v1",
            "guardrail_policy_version": "guardrail-v2",
            "model_ids": {
                "sentencing": "gpt-5.6-luna",
                "writer": "grok-4.20-0309-non-reasoning",
                "evaluator": "gpt-5.6-luna",
            },
        }
    )


def _ok_body(path: str) -> dict[str, Any]:
    if path.endswith("/snapshot"):
        return load_fixture("case-snapshot-taxi")
    if path.endswith("/resolve-evidence"):
        return {
            "sources": [],
            "aggregates": {
                "burn_rate": 0.41,
                "tier": "FAKE",
                "no_spend_days": 0,
                "repeat_same_category_30d": 0,
                "excludes_post_id": "p1",
                "window": {"start_at": "2026-08-08T08:52:00Z", "end_at": "2026-09-07T08:52:00Z"},
                "rule_version": 1,
            },
            "room_rules": [],
            "recent_verdicts": [],
            "style_comments": [],
        }
    if path.endswith("/begin-generation"):
        return {"fixed_sentencing": None, "text_version": 0, "deadline_at": "2026-09-07T09:30:00Z"}
    if path.endswith("/finalize"):
        return {"verdict_id": VERDICT_ID, "text_version": 1, "committed_at": "2026-09-07T09:21:00Z"}
    return {"verdict_id": VERDICT_ID}


class Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)


def make_backend(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[BackendHttp, Recorder]:
    recorder = Recorder()

    def _record(request: httpx.Request) -> httpx.Response:
        request.read()
        recorder.requests.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_record))
    return BackendHttp(BASE_URL, TOKEN, client=client, sleep=recorder.sleep), recorder


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=_ok_body(request.url.path))


Call = Callable[[BackendHttp], Awaitable[Any]]

CALLS: dict[str, Call] = {
    "snapshot": lambda b: b.snapshot(JOB_ID, GENERATION_ID),
    "resolve_evidence": lambda b: b.resolve_evidence(
        JOB_ID,
        GENERATION_ID,
        ResolveEvidenceRequest(
            candidates=[
                EvidenceCandidate(source_type="post", source_id="p0", source_version=1, score=0.9)
            ],
            include=["rules", "aggregates"],
        ),
    ),
    "begin_generation": lambda b: b.begin_generation(
        VERDICT_ID, job_id=JOB_ID, generation_id=GENERATION_ID, verdict_version=1
    ),
    "finalize": lambda b: b.finalize(VERDICT_ID, finalize_request()),
    "generation_failed": lambda b: b.generation_failed(
        VERDICT_ID, job_id=JOB_ID, generation_id=GENERATION_ID, error_code="AI_NOT_READY"
    ),
}


# --- ① 헤더 5종 · X-Request-Id · 로그에 토큰 없음 -----------------------------------


async def test_다섯_메서드_모두_헤더_5종을_붙이고_로그에_토큰이_없다(
    capsys: pytest.CaptureFixture[str],
):
    responses = iter([httpx.Response(503)])

    def handler(request: httpx.Request) -> httpx.Response:
        # 첫 요청만 503 으로 재전송 로그를 한 번 남기게 한다.
        return next(responses, None) or _ok(request)

    backend, recorder = make_backend(handler)
    bind_trace("trace-abc")
    with structlog.testing.capture_logs() as logs:
        for call in CALLS.values():
            await call(backend)

    assert len(recorder.requests) == len(CALLS) + 1
    for request in recorder.requests:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.headers["x-trace-id"] == "trace-abc"
        assert request.headers["x-job-id"] == JOB_ID
        assert request.headers["x-generation-id"] == GENERATION_ID
        assert request.headers["x-request-id"]
    request_ids = [r.headers["x-request-id"] for r in recorder.requests]
    assert len(set(request_ids)) == len(request_ids)

    captured = capsys.readouterr()
    emitted = repr(logs) + captured.out + captured.err
    assert "backend_retry" in emitted
    assert TOKEN not in emitted


# --- ② transport 오류 → 같은 본문 2회 재전송 → BackendUnavailable ---------------------


@pytest.mark.parametrize(
    "error", [httpx.ConnectError("boom"), httpx.ReadTimeout("slow")], ids=["connect", "timeout"]
)
@pytest.mark.parametrize("method", ["begin_generation", "finalize", "generation_failed"])
async def test_transport_오류는_같은_본문으로_2회_재전송한_뒤_BackendUnavailable(
    error: httpx.TransportError, method: str
):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    backend, recorder = make_backend(handler)
    with pytest.raises(BackendUnavailable) as caught:
        await CALLS[method](backend)

    assert caught.value.retry_after_s == 5
    assert caught.value.error_code == "BACKEND_UNAVAILABLE"
    assert len(recorder.requests) == 3
    bodies = {request.content for request in recorder.requests}
    assert len(bodies) == 1 and next(iter(bodies))
    assert recorder.sleeps == list(RETRY_BACKOFF_S) == [0.2, 0.6]


# --- ③ 5xx 도 재전송, 3번째 200 이면 성공 -------------------------------------------


async def test_5xx_는_재전송하고_세_번째_200_이면_성공한다():
    statuses = iter([500, 503])

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses, None)
        return httpx.Response(status) if status is not None else _ok(request)

    backend, recorder = make_backend(handler)
    result = await CALLS["begin_generation"](backend)

    assert result.text_version == 0
    assert len(recorder.requests) == 3
    assert len({request.content for request in recorder.requests}) == 1
    assert recorder.sleeps == [0.2, 0.6]


# --- ④ 4xx → BackendRejected, 재전송 없음 ------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (409, "STALE_GENERATION"),
        (409, "EVIDENCE_INVALIDATED"),
        (409, "DEADLINE_EXCEEDED"),
        (409, "IDEMPOTENCY_CONFLICT"),
        (422, "INVALID_DRAFT"),
        (401, "UNAUTHORIZED"),
        (403, "FORBIDDEN"),
    ],
)
async def test_4xx_는_BackendRejected_로_올리고_재전송하지_않는다(status: int, code: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"code": code})

    backend, recorder = make_backend(handler)
    with pytest.raises(BackendRejected) as caught:
        await CALLS["finalize"](backend)

    assert (caught.value.status, caught.value.code) == (status, code)
    assert len(recorder.requests) == 1
    assert recorder.sleeps == []


# --- ⑤ 알 수 없는 필드 → 검증 오류 ---------------------------------------------------


@pytest.mark.parametrize("method", ["snapshot", "begin_generation", "finalize"])
async def test_응답에_알_수_없는_필드가_있으면_검증_오류(method: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**_ok_body(request.url.path), "surprise": 1})

    backend, _ = make_backend(handler)
    with pytest.raises(ValidationError):
        await CALLS[method](backend)


# --- ⑥ 타임아웃 = 03 §3.2 표 ---------------------------------------------------------


EXPECTED_TIMEOUTS = {
    "snapshot": 2.0,
    "resolve_evidence": 2.0,
    "begin_generation": 1.0,
    "finalize": 3.0,
    "generation_failed": 1.0,
}


def test_타임아웃_상수가_03_표와_같다():
    assert CONNECT_TIMEOUT_S == 0.5
    assert TIMEOUTS_S == EXPECTED_TIMEOUTS


@pytest.mark.parametrize("method", list(EXPECTED_TIMEOUTS))
async def test_요청마다_메서드별_타임아웃이_실린다(method: str):
    backend, recorder = make_backend(_ok)
    await CALLS[method](backend)

    timeout = recorder.requests[0].extensions["timeout"]
    assert timeout["connect"] == 0.5
    assert timeout["read"] == EXPECTED_TIMEOUTS[method]


# --- ⑦ finalize 재전송 → 가짜 백엔드 commit record ----------------------------------


class LoseFirstFinalizeResponse(httpx.AsyncBaseTransport):
    """첫 finalize 는 백엔드에 닿아 커밋되지만 응답이 유실된다(timeout)."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner
        self.finalize_bodies: list[bytes] = []
        self._lost = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if request.url.path.endswith("/finalize"):
            self.finalize_bodies.append(request.content)
            if not self._lost:
                self._lost = True
                await response.aread()
                raise httpx.ReadTimeout("response lost", request=request)
        return response


async def _begin_on_fake() -> tuple[Any, BackendHttp, LoseFirstFinalizeResponse]:
    app = create_fake_backend()
    fake = app.state.fake
    fake.seed_verdict(
        VERDICT_ID, post_id="p1", deadline_at=datetime.now(UTC) + timedelta(seconds=60)
    )
    transport = LoseFirstFinalizeResponse(httpx.ASGITransport(app=app))
    backend = BackendHttp(
        "http://fake-backend",
        FAKE_SERVICE_TOKEN,
        client=httpx.AsyncClient(transport=transport),
        sleep=Recorder().sleep,
    )
    await backend.begin_generation(
        VERDICT_ID, job_id=JOB_ID, generation_id=GENERATION_ID, verdict_version=1
    )
    return fake, backend, transport


async def test_finalize_응답_유실_뒤_같은_본문_재전송은_같은_200():
    fake, backend, transport = await _begin_on_fake()
    req = finalize_request()

    first = await backend.finalize(VERDICT_ID, req)
    again = await backend.finalize(VERDICT_ID, req)

    # 첫 시도는 커밋됐지만 응답이 유실돼 재전송했다. 저장은 한 번뿐이다.
    assert len(transport.finalize_bodies) == 3
    assert len(set(transport.finalize_bodies)) == 1
    assert first == again
    assert first.text_version == 1
    state = fake.verdicts[VERDICT_ID]
    assert state.text_version == 1
    assert state.sentence_status == "FINAL"
    assert state.sentence_source == "AI"
    assert len(fake.retain_jobs) == 1
    await backend.aclose()


async def test_finalize_같은_generation_다른_본문은_409_IDEMPOTENCY_CONFLICT():
    _, backend, _ = await _begin_on_fake()
    await backend.finalize(VERDICT_ID, finalize_request())

    with pytest.raises(BackendRejected) as caught:
        await backend.finalize(VERDICT_ID, finalize_request(draft_hash="b" * 64))

    assert (caught.value.status, caught.value.code) == (409, "IDEMPOTENCY_CONFLICT")
    await backend.aclose()
