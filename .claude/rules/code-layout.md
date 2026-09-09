---
description: Python 툴체인과 저장소 배치, 의존 방향
paths:
  - "src/**"
  - "tests/**"
  - "contracts/**"
  - "database/**"
  - "scripts/**"
  - "pyproject.toml"
---

# 코드 배치

## 툴체인

- uv와 Python 3.12(`pyproject.toml`의 `requires-python = "==3.12.*"`). 실행은 `uv run`. 시스템 Python은 없다
- 단독 스크립트는 PEP 723 인라인 의존성으로 쓰고 `uv run scripts/이름.py`로 돈다
- dev 의존은 `pytest`, `pytest-asyncio`, `ruff`

## 배치

이 저장소 루트가 proposal2의 `services/ai/`다. 경로는 루트 기준으로 쓴다.

```
contracts/*.schema.json         계약 정본(schema_version=1). 01 소유
contracts/fixtures/*.json       hell 예시, 정책 버전별 기대값
src/geoji_ai/contracts/         pydantic 미러. extra="forbid". 동등성 테스트로 정본과 묶는다
src/geoji_ai/domain/            intensity, lexicon, attack_angles, validation
src/geoji_ai/ports/             llm, backend, memory, jobs, ledger. Protocol 5종
src/geoji_ai/adapters/          fake_llm, openai_compat_llm, postgres 등
src/geoji_ai/application/       그래프와 유스케이스
src/geoji_ai/api/               FastAPI. /health/live, /health/ready
src/geoji_ai/workers/           ai.jobs 워커
src/geoji_ai/core/              config, logging, startup
database/migrations/001~005     001~003 AI 소유. 004 초안은 여기 두고 백엔드로 이관
tests/                          testing.md 참조
scripts/probe_*.py              실측 스크립트. 결과는 scripts/probe_out/에 커밋
```

- 계획서가 말하는 경로가 아직 없으면 그 계획서 §3 표의 파일명 그대로 만든다. 이름을 바꾸면 계획서도 같이 고친다

## 의존 방향

- `api/workers → application → domain/ports`. 거꾸로 가져오지 않는다
- `domain`은 FastAPI, LangGraph, SQLAlchemy, 벤더 SDK를 모른다
- `domain/lexicon.py`, `intensity.py`, `attack_angles.py`는 단일 정의다. 04·05·06이 가져다 쓰되 복사하지 않는다
