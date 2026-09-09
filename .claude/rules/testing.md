---
description: 테스트 디렉터리 약속, fake provider 원칙, 프롬프트 변경 관문, 실측 스크립트
paths:
  - "src/**"
  - "tests/**"
  - "scripts/**"
  - "prompts/**"
---

# 테스트와 모델 호출

## 케이스가 먼저다

- 계획서 헤더의 "먼저 실패시킬 케이스"가 곧 테스트 목록이다. §4.2가 이를 `tests/<layer>/test_<topic>.py`별 체크박스로 푼다
- 새 기능은 케이스를 먼저 쓰고 설계를 쓴다. 테스트 파일 이름은 계획서 §4.1 측정 열과 §4.2 제목에 있는 것을 그대로 쓴다

## 디렉터리

```
tests/contracts/     거부 케이스, fixture 로드, pydantic 동등성
tests/unit/
tests/integration/   실제 Postgres
tests/evaluations/   골든셋 회귀, 심문 평가
tests/fakes/         가짜 백엔드 backend_app.py
```

## 벤더 호출

- 테스트와 CI는 fake provider만 쓴다. 실제 벤더 호출은 실측 스크립트와 작업 6의 골든셋 회귀에서만
- 키는 루트 `.env`(`XAI_API_KEY`, `OPENAI_API_KEY`). 코드에 박지 않고 `.env.example`에는 이름만
- 실측은 `uv run scripts/probe_writer_latency.py --model … --n N [--split] [--dry-run]`. 결과 JSON은 `scripts/probe_out/<YYYYMMDD-HHMMSS>-<provider>-<model>[-split].json`으로 남기고 커밋한다
- 검수 기준점은 파일명으로 인용한다. `v5.3 = 20260907-150704-*.json`

## 프롬프트 변경

- 골든셋 회귀와 사람 검수 두 관문을 지난다. LLM 심사 점수 하나로 통과시키지 않는다
- 작업 6 이후 프롬프트 파일 소유는 06이다. 변경은 회귀 뒤에서만
