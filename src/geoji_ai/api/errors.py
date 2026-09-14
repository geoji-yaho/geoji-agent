"""거부 응답 본문(10 §4.7, 9/14 결정).

AI API 의 거부 본문은 백엔드와 같은 `{"code": "<코드>"}` 다. FastAPI 기본 모양
(`detail.code` 중첩)은 쓰지 않는다.

- `api_error(status, code)` 로 올린 `HTTPException` → `{"code": …}`.
  상태·헤더(`WWW-Authenticate` 등) 그대로
- 그 밖의 `detail`(문자열 등, 예: 라우트 없음 404) → FastAPI 기본 동작
- 본문 스키마 위반(`RequestValidationError`) → 422 `{"code": "INVALID_REQUEST"}`.
  검증 오류 원문에는 입력값이 섞이므로 응답·로그 어디에도 싣지 않는다
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

__all__ = ["INVALID_REQUEST", "api_error", "install"]

#: 본문 스키마 위반 코드(10 §4.7, 가짜 백엔드와 같은 이름).
INVALID_REQUEST = "INVALID_REQUEST"


def api_error(status_code: int, code: str, headers: dict[str, str] | None = None) -> HTTPException:
    """거부 코드 하나를 담은 `HTTPException`. 본문 모양은 핸들러가 `{"code"}` 로 바꾼다."""
    return HTTPException(status_code=status_code, detail={"code": code}, headers=headers)


def _code_of(detail: object) -> str | None:
    if isinstance(detail, dict) and set(detail) == {"code"} and isinstance(detail["code"], str):
        return detail["code"]
    return None


async def _http_exception(request: Request, exc: StarletteHTTPException) -> Response:
    code = _code_of(exc.detail)
    if code is None:
        return await http_exception_handler(request, exc)
    return JSONResponse(status_code=exc.status_code, content={"code": code}, headers=exc.headers)


async def _validation_error(request: Request, exc: RequestValidationError) -> Response:
    return JSONResponse(status_code=422, content={"code": INVALID_REQUEST})


def install(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error)  # type: ignore[arg-type]
