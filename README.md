# 떼거지 AI 파트

지출이 등록되면 심문관·조서·양형관·서기·검수관 다섯 역할이 판결문을 만든다.
이 저장소는 그 역할과 계약을 구현한다. 산출물은 Docker 이미지다.

정본은 `docs/plans/` 다. 문서는 번호와 절로 부른다(`01 §3.7`, `10 §5`).

## 실행 절차

Python 3.12 와 [uv](https://docs.astral.sh/uv/) 가 필요하다. 시스템 Python 은 쓰지 않는다.

```bash
uv sync && uv run pytest tests/contracts tests/unit -q
uv run python -c "from geoji_ai.contracts.llm_schemas import writer_schema; import json; print(json.dumps(writer_schema(['spicy'], ['CONVERSION']), ensure_ascii=False)[:400])"
uv run uvicorn geoji_ai.api.app:app --port 8100 & curl -s localhost:8100/health/live
```

- `/health/live` 는 프로세스가 살아 있으면 200 이다.
- `/health/ready` 는 벤더 키가 없으면 503 과 `{"status":"not_ready","missing":[...]}` 를 낸다.
  DB 검사는 작업 2 에서 붙는다.

로컬 Postgres(작업 2 의 `ai.jobs` 큐·통합 테스트용)는 compose 로 띄운다.

```bash
docker compose -f docker-compose.dev.yml up -d postgres
```

### 게이트

```bash
uv run ruff check .
uv run ruff format .
uv run pytest -q
```

## 계약 정본

`contracts/*.schema.json` 7종이 계약 정본이고 백엔드·프론트가 읽는다. 파일은 pydantic 미러
(`src/geoji_ai/contracts/`)에서 생성한다. **정본 JSON 을 손으로 고치지 않는다.** 미러를 고치고
재생성한다. 테스트가 "재생성 = 커밋본" 을 단언한다.

```bash
uv run python tools/gen_contracts.py
```

## 설정

환경변수 이름 = 필드 이름이다. 값은 `.env`(커밋하지 않는다) 또는 환경에서 온다.
키 이름만 담은 `.env.example` 을 복사해서 쓴다. 정본은 01 §3.7 이다.

**비밀값**은 로그·git·응답 어디에도 원문이 나가지 않는다. 설정을 찍을 때는
`core.config.redact()` 를 거친다.

| 키 | 기본값 | 비고 |
|---|---|---|
| `APP_ENV` | `development` | `production` 이면 기동 검사가 엄격해진다 |
| `DATABASE_URL` | — | **비밀값**. 공유 Supabase Postgres(D-20) |
| `BACKEND_INTERNAL_URL` | — | 백엔드 내부 API(10 §4) |
| `SERVICE_AUTH_TOKEN` | — | **비밀값** |
| `OPENAI_API_KEY` | — | **비밀값**. 없으면 `/health/ready` 503 |
| `XAI_API_KEY` | — | **비밀값**. 없으면 `/health/ready` 503 |
| `XAI_BASE_URL` | `https://api.x.ai/v1` | |
| `MODEL_JUDGMENT` | `gpt-5.6-luna` | 양형관 |
| `MODEL_WRITER` | `grok-4.20-0309-non-reasoning` | 서기·드립. 추론 모델(`grok-4.6`·`4.5`·`4.3`)이면 기동 실패 |
| `MODEL_EVALUATOR_HELL` | `gpt-5.6-luna` | 지옥맛 검수관 |
| `PROMPT_BUNDLE_VERSION` | `bundle-v1` | `prompts/` 파일 해시로 만든다 |
| `GUARDRAIL_POLICY_VERSION` | `guardrail-v2` | `guardrail-v1` \| `guardrail-v2`(D-07). **production 에서는 환경변수에 직접 적어야 한다.** 안 적고 기본값에 기대면 기동 실패 |
| `INTAKE_TIMEOUT_SECONDS` | `4` | 심문관 |
| `FIRST_RESULT_TARGET_SECONDS` | `10` | 첫 결과 목표 |
| `REPAIR_PATH_BUDGET_SECONDS` | `15` | 복구 경로 예산 |
| `SENTENCING_NODE_TIMEOUT_SECONDS` | `3` | 작업 3 실측 후 조정 |
| `WRITER_NODE_TIMEOUT_SECONDS` | `6` | |
| `EVALUATOR_NODE_TIMEOUT_SECONDS` | `4` | 작업 3 실측 후 조정 |
| `WORKER_POLL_MS` | `250` | 작업 2 |
| `JOB_LEASE_SECONDS` | `15` | 작업 2 |
| `HEARTBEAT_SECONDS` | `5` | 작업 2 |
| `WORKER_SLOTS` | `{"SENTENCE":2,"PREPARE":1,"BACKGROUND":1}` | JSON. 작업 2 |
| `FINALIZE_RESERVE_MS` | `500` | 작업 5 |
| `INLINE_CONTEXT_MIN_REMAINING_MS` | `8500` | 작업 5 |
| `IMMEDIATE_REPAIR_MAX` | `1` | |
| `TEXT_RETRY_ROUNDS` | `3` | |
| `TEXT_RETRY_TIMEOUT_SECONDS` | `20` | |
| `RECALL_CANDIDATE_LIMIT` | `20` | 작업 4 |
| `EVIDENCE_PACK_LIMIT` | `12` | 작업 4 |
| `STYLE_EXAMPLE_LIMIT` | `3` | 작업 4 |
| `MODEL_CONCURRENCY_LIMIT` | `8` | D-22 |
| `ALERT_DISCORD_WEBHOOK_URL` | — | **비밀값**. 알림 규칙은 08 §3.3 |
| `COST_ALERT_KRW_PER_DAY` | `5000` | 평가 실행(`GEOJI_EVAL=1`)분은 별도 집계 |
| `MAX_TOTAL_PROMPT_TOKENS` | `6000` | |
| `WRITER_MAX_PROMPT_TOKENS` | `8000` | |
| `INTAKE_MAX_OUTPUT_TOKENS` | `300` | 잘리면 스키마 실패로 처리한다 |
| `CONTEXT_MAX_OUTPUT_TOKENS` | `700` | |
| `BANTER_MAX_OUTPUT_TOKENS` | `1200` | |
| `SENTENCING_MAX_OUTPUT_TOKENS` | `400` | |
| `WRITER_MAX_OUTPUT_TOKENS` | `700` | 강도 1개당 |
| `EVALUATOR_MAX_OUTPUT_TOKENS` | `800` | |
| `ROOM_COMMENT_STYLE_ENABLED` | `false` | |
| `PUBLIC_HISTORY_CALLBACK_ENABLED` | `false` | |
| `REFLECT_ENABLED` | `false` | |
| `HINDSIGHT_ENABLED` | `false` | hell 을 끄는 플래그는 없다 — 정책 버전으로 통제한다 |

### 기동 검사 (`core/startup.py`)

1. production 인데 `GUARDRAIL_POLICY_VERSION` 을 환경변수로 직접 적지 않았으면(기본값 의존) `StartupError`. 목록 밖 값은 설정 생성 단계에서 이미 거부된다.
2. `MODEL_WRITER` 가 `grok-4.6`·`grok-4.5`·`grok-4.3` 이면 `StartupError`(환경 무관).
3. `OPENAI_API_KEY`·`XAI_API_KEY` 가 비면 기동은 되고 `/health/ready` 가 503 을 낸다.

## 배치

경로와 의존 방향은 `.claude/rules/code-layout.md`, 용어와 enum 은
`.claude/rules/domain-vocabulary.md`, 테스트 규칙은 `.claude/rules/testing.md` 에 있다.
