"""백엔드 내부 API httpx 어댑터(03 §3.2, 10 §4·§5).

`ports.backend.BackendPort` 를 구현한다. 규약:

- 헤더 5종: `Authorization: Bearer`, `X-Trace-Id`, `X-Request-Id`(호출마다 uuid4),
  `X-Job-Id`, `X-Generation-Id`. trace_id 는 포트 시그니처에 자리가 없어
  `bind_trace(trace_id)` 로 이 모듈의 컨텍스트에 묶는다(워커 `run_job` 이 부른다).
- 타임아웃: connect 0.5s 공통, 메서드별 read 는 `TIMEOUTS_S`.
- 재전송: transport 오류·timeout·5xx 에 **같은 본문 바이트**를 최대 2회(200ms·600ms).
  그래도 실패면 `BackendUnavailable`. 4xx 는 재전송하지 않는다.
- 4xx 는 `BackendRejected(status, code)` 로 올리기만 한다. 폐기·알림 판단은 핸들러 몫이다.
- 응답은 포트 모델로 검증한다(`extra="forbid"`). 검증 실패는 pydantic 오류 그대로 올린다.

토큰과 헤더는 로그에 남기지 않는다. 로그에는 메서드 이름·시도 번호·사유(예외 타입 또는
상태 코드)만 싣는다.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.contracts.finalize import FinalizeRequest
from geoji_ai.core.logging import get_logger
from geoji_ai.ports.backend import (
    BeginGenerationResult,
    FinalizeResult,
    GenerationErrorCode,
    JuryVoteRequest,
    JuryVoteResult,
    ResolveEvidenceRequest,
    ResolveEvidenceResponse,
    SnapshotNotFound,
)

__all__ = [
    "BACKEND_UNAVAILABLE",
    "CONNECT_TIMEOUT_S",
    "RETRY_BACKOFF_S",
    "TIMEOUTS_S",
    "BackendHttp",
    "BackendRejected",
    "BackendUnavailable",
    "bind_trace",
]

log = get_logger(__name__)

#: 03 §3.2 connect 타임아웃(공통).
CONNECT_TIMEOUT_S = 0.5

#: 03 §3.2 메서드별 타임아웃(초).
TIMEOUTS_S: dict[str, float] = {
    "snapshot": 2.0,
    "resolve_evidence": 2.0,
    "begin_generation": 1.0,
    "finalize": 3.0,
    "generation_failed": 1.0,
    # 18 §3.6(19 §5). 4xx 는 재전송하지 않는다 — 백엔드가 같은 표를 `ALREADY_VOTED` 로 받는다.
    "cast_jury_vote": 2.0,
}

#: 03 §3.2 재전송 백오프. 길이가 곧 재전송 횟수(2회)다.
RETRY_BACKOFF_S: tuple[float, ...] = (0.2, 0.6)

#: 재전송이 다 실패했을 때 job 에 남기는 오류 코드와 재시도 간격(03 §3.2).
BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
_UNAVAILABLE_RETRY_AFTER_S = 5

_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "backend_trace_id", default=None
)

M = TypeVar("M", bound=BaseModel)


def bind_trace(trace_id: str) -> None:
    """이 실행 문맥의 백엔드 호출에 `X-Trace-Id` 를 붙인다."""
    _trace_id.set(trace_id)


class BackendRejected(Exception):
    """백엔드가 4xx 로 거부했다. `code` 는 응답 본문의 오류 코드다."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(f"backend rejected: {status} {code}")
        self.status = status
        self.code = code


class BackendUnavailable(Exception):
    """transport 오류·timeout·5xx 가 재전송 뒤에도 이어졌다."""

    def __init__(self) -> None:
        super().__init__(BACKEND_UNAVAILABLE)
        self.error_code = BACKEND_UNAVAILABLE
        self.retry_after_s = _UNAVAILABLE_RETRY_AFTER_S


def _error_code(response: httpx.Response) -> str:
    """거부 응답 본문에서 오류 코드를 꺼낸다. 없으면 `HTTP_<status>`."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        for key in ("code", "error_code"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
        detail = body.get("detail")
        if isinstance(detail, dict) and isinstance(detail.get("code"), str):
            return detail["code"]
    return f"HTTP_{response.status_code}"


def _json_bytes(body: dict[str, Any]) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class BackendHttp:
    """`BackendPort` 의 httpx 구현."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = client if client is not None else httpx.AsyncClient()
        self._sleep = sleep

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- BackendPort ----------------------------------------------------------

    async def snapshot(self, job_id: str, generation_id: str) -> CaseSnapshot:
        try:
            response = await self._send(
                "snapshot",
                "GET",
                f"/internal/v1/ai-jobs/{job_id}/snapshot",
                job_id=job_id,
                generation_id=generation_id,
            )
        except BackendRejected as exc:
            if exc.status == 404 and exc.code == "NOT_FOUND":
                raise SnapshotNotFound() from exc
            raise
        return self._parse(response, CaseSnapshot)

    async def resolve_evidence(
        self, job_id: str, generation_id: str, req: ResolveEvidenceRequest
    ) -> ResolveEvidenceResponse:
        response = await self._send(
            "resolve_evidence",
            "POST",
            f"/internal/v1/ai-jobs/{job_id}/resolve-evidence",
            job_id=job_id,
            generation_id=generation_id,
            body=req.model_dump_json().encode("utf-8"),
        )
        return self._parse(response, ResolveEvidenceResponse)

    async def begin_generation(
        self, verdict_id: str, *, job_id: str, generation_id: str, verdict_version: int
    ) -> BeginGenerationResult:
        body = {
            "job_id": job_id,
            "generation_id": generation_id,
            "verdict_version": verdict_version,
        }
        response = await self._send(
            "begin_generation",
            "POST",
            f"/internal/v1/verdicts/{verdict_id}/begin-generation",
            job_id=job_id,
            generation_id=generation_id,
            body=_json_bytes(body),
        )
        return self._parse(response, BeginGenerationResult)

    async def finalize(self, verdict_id: str, req: FinalizeRequest) -> FinalizeResult:
        response = await self._send(
            "finalize",
            "POST",
            f"/internal/v1/verdicts/{verdict_id}/finalize",
            job_id=req.job_id,
            generation_id=req.generation_id,
            body=req.model_dump_json(by_alias=True).encode("utf-8"),
        )
        return self._parse(response, FinalizeResult)

    async def generation_failed(
        self,
        verdict_id: str,
        *,
        job_id: str,
        generation_id: str,
        error_code: GenerationErrorCode,
    ) -> None:
        body = {"job_id": job_id, "generation_id": generation_id, "error_code": error_code}
        await self._send(
            "generation_failed",
            "POST",
            f"/internal/v1/verdicts/{verdict_id}/generation-failed",
            job_id=job_id,
            generation_id=generation_id,
            body=_json_bytes(body),
        )

    async def cast_jury_vote(self, post_id: str, req: JuryVoteRequest) -> JuryVoteResult:
        response = await self._send(
            "cast_jury_vote",
            "POST",
            f"/internal/v1/posts/{post_id}/jury-votes",
            job_id=req.job_id,
            generation_id=req.generation_id,
            body=req.model_dump_json().encode("utf-8"),
        )
        return self._parse(response, JuryVoteResult)

    # --- 내부 -----------------------------------------------------------------

    def _headers(self, job_id: str, generation_id: str, *, has_body: bool) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Trace-Id": _trace_id.get() or str(uuid.uuid4()),
            "X-Request-Id": str(uuid.uuid4()),
            "X-Job-Id": job_id,
            "X-Generation-Id": generation_id,
        }
        if has_body:
            headers["Content-Type"] = "application/json"
        return headers

    async def _send(
        self,
        name: str,
        method: str,
        path: str,
        *,
        job_id: str,
        generation_id: str,
        body: bytes | None = None,
    ) -> httpx.Response:
        """재전송 규약을 탄 요청. 본문 바이트는 한 번 만들어 모든 시도에 그대로 쓴다."""
        timeout = httpx.Timeout(TIMEOUTS_S[name], connect=CONNECT_TIMEOUT_S)
        url = f"{self._base_url}{path}"
        attempts = len(RETRY_BACKOFF_S) + 1
        reason = ""
        for attempt in range(attempts):
            # X-Request-Id 는 시도마다 새로 만든다.
            headers = self._headers(job_id, generation_id, has_body=body is not None)
            try:
                response = await self._client.request(
                    method, url, content=body, headers=headers, timeout=timeout
                )
            except httpx.TransportError as exc:
                # `httpx.TimeoutException` 도 `TransportError` 다.
                reason = type(exc).__name__
            else:
                if response.status_code < 400:
                    return response
                if response.status_code < 500:
                    raise BackendRejected(response.status_code, _error_code(response))
                reason = f"HTTP_{response.status_code}"
            if attempt + 1 < attempts:
                log.warning("backend_retry", method=name, attempt=attempt + 1, reason=reason)
                await self._sleep(RETRY_BACKOFF_S[attempt])
        log.warning("backend_unavailable", method=name, attempts=attempts, reason=reason)
        raise BackendUnavailable()

    @staticmethod
    def _parse(response: httpx.Response, model: type[M]) -> M:
        return model.model_validate_json(response.content)
