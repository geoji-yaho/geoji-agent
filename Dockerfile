# geoji-ai 운영 이미지. ai-api·ai-worker 가 같은 이미지를 쓰고 command 로 갈린다(10 §16.3).
# 태그 규칙은 runbook §6 의 ai-YYYYMMDD-N.
FROM python:3.12-slim AS base

# uv 로 설치한다(빌드 백엔드가 uv_build 다)
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# 의존성 먼저 — 소스만 바뀌면 이 레이어는 캐시된다
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# 런타임에 읽는 자산: 프롬프트·계약 스키마·마이그레이션(geoji-ai migrate)·픽스처
COPY src ./src
COPY prompts ./prompts
COPY contracts ./contracts
COPY database ./database
COPY scripts ./scripts

RUN uv sync --frozen --no-dev

# 루트로 돌지 않는다
RUN useradd --create-home --uid 10001 geoji && chown -R geoji:geoji /app /opt/venv
USER geoji

EXPOSE 8000

# 기본은 API. 워커는 compose 에서 command 를 덮어쓴다(geoji-ai worker)
CMD ["uvicorn", "geoji_ai.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
