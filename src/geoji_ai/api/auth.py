"""서비스 인증(03 §3.4).

`Authorization: Bearer <SERVICE_AUTH_TOKEN>` 을 설정값과 timing-safe 로 비교한다.
불일치·누락·**설정값이 비어 있음**은 모두 401 이다(열어 두지 않는다).
`/health/*` 는 이 dependency 를 걸지 않는다.

헤더 값은 로그·응답 어디에도 넣지 않는다.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status

from geoji_ai.core.config import Settings, get_settings, secret_value

__all__ = ["require_service_token"]

_BEARER_PREFIX = "bearer "


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "UNAUTHORIZED"},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_service_token(request: Request) -> None:
    expected = secret_value(_settings(request), "SERVICE_AUTH_TOKEN").strip()
    if not expected:
        raise _unauthorized()
    header = request.headers.get("authorization", "")
    if not header.lower().startswith(_BEARER_PREFIX):
        raise _unauthorized()
    presented = header[len(_BEARER_PREFIX) :].strip()
    if not hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
        raise _unauthorized()
