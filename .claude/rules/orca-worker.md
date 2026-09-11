---
description: orca 코디네이터가 워크트리에 띄운 무인 워커 세션이 절차와 무관하게 지키는 것. 질문 경로, 공유 자원, 통합 테스트, 편집 범위
---

# orca 워커

프롬프트 첫머리에 Task ID·Dispatch ID 가 든 orca preamble 이 있으면 워커다. 절차는 `geoji-harness` 스킬 "orca 워커 모드", 마무리는 `git-workflow` 룰 "orca 워커" 절. 여기는 절차와 무관하게 늘 지키는 것이다. preamble 이 없는 세션은 이 룰을 무시한다.

## 질문

- 사람이 이 터미널을 보지 않는다. 물을 것은 preamble 의 `ask` 명령으로 코디네이터에게. 훅이 `AskUserQuestion`을 막는다
- 답 없이 임시값을 넣지 않는다. 계획서에 없는 값은 사람 세션에서도 묻는 것이 규칙이고, 워커에서는 그 상대가 코디네이터다
- 타임아웃이면 같은 message ID 로 `--resume`. 질문을 새로 만들지 않는다

## 공유 자원. 스펙에 없으면 ask

여러 워커가 동시에 건드리면 머지에서 부딪히거나 다른 워커의 테스트를 깨는 것들이다.

- `pyproject.toml`(의존성)과 `uv.lock`
- `tests/conftest.py`, `src/geoji_ai/__init__.py`, `src/geoji_ai/core/config.py`
- `contracts/*.schema.json`과 `schema_version`. 소유는 01
- `database/migrations/` 번호. 계획서에 있는 번호만 쓴다. 새 번호를 만들지 않는다
- `prompts/`. 작업 6 이후 소유는 06, 변경은 회귀 뒤에만
- `docs/plans/`. 카드 체크와 결정 기록은 사용자만

## 테스트

- `tests/integration`은 실제 Postgres 다(02 §4.2). 워커 둘이 같은 DB 의 `ai` 스키마를 재생성하면 서로의 테스트를 깬다. 스펙이 `DATABASE_URL`을 주지 않았으면 돌리지 않는다. 게이트는 `uv run pytest -q --ignore=tests/integration`
- 실제 벤더 키를 넣어 통과시키지 않는다(testing 룰)

## 편집 범위

- 스펙 Ownership 의 `owned` 밖과 `forbidden` 안은 훅이 막는다. 필요하면 `ask`로 범위를 넓혀 받는다. 우회하지 않는다
- `_workspace/`는 늘 쓸 수 있다. 커밋되지 않는다

## 형제 저장소

- 워크트리는 `~/orca/workspaces/` 아래라 `../geoji-web`이 없다. `CLAUDE.md`의 `GEOJIBANG_ROOT` 규칙대로 찾는다
