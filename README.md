# 떼거지 AI 파트

지출이 등록되면 심문관·조서·양형관·서기·검수관 다섯 역할이 판결문을 만든다.
이 저장소는 그 역할과 계약을 구현한다. 산출물은 Docker 이미지다.

정본은 `docs/plans/` 다. 문서는 번호와 절로 부른다(`01 §3.7`, `10 §5`).

## 실행 절차

### 9/16 전달 상태

이 저장소에는 AI 변경과 검증 도구를 반영한다. 프론트·백엔드 변경은 임시 checkout에서
검증한 참고 패치이며, 해당 저장소에 커밋·푸시하지 않았다.
[백엔드 요청사항](docs/plans/10-backend-contract.md)과
[프론트 요청사항](docs/plans/archive/16-frontend-handoff.md)을 각 담당자가 검토해 반영한다.

9/17 후속: 카드 본문 1항목 수용과 짧은 기본 문구는 최신 server `c970ab4`에서
`codex/card-text-contract` 브랜치로 수정했다. 백엔드 전체 빌드·536개 테스트를 통과했으며,
위 9/16 참고 패치와 별개다. main 머지·운영 배포는 아직 확인하지 않았다.
백엔드 푸시는 현재 계정의 쓰기 권한이 없어 거절됐다. 동일한 로컬 커밋 `93323a9`을
[백엔드 카드 패치](docs/plans/evidence/20260917/backend-card-text.patch)로 제공한다.
적용 방법은 [백엔드 계약서 §5](docs/plans/10-backend-contract.md)에 있다.

로컬에서 `b-meme`으로 만든 이미지를 LLM 키 없이 등록하도록 **백엔드 업로드 API 반영을 요청**했다.
관리자 인증·스토리지·짤 카탈로그를 관리하는 백엔드 담당자가 반영할 사항이며,
에이전트에 중복 업로드 API는 추가하지 않았다(10 §16.5).
로컬 이미지 위치는 [B급 밈 자산 색인](outputs/b-meme/README.md)에 정리했다. 기존 백엔드
전달 목록 10장과 이후 변환된 17장을 구분하며, 전달용 JSON만으로 S3·DB 등록을 단정하지 않는다.

### 개발 실행

Python 3.12 와 [uv](https://docs.astral.sh/uv/) 가 필요하다. 시스템 Python 은 쓰지 않는다.

```bash
uv sync && uv run pytest tests/contracts tests/unit -q
uv run python -c "from geoji_ai.contracts.llm_schemas import writer_schema; import json; print(json.dumps(writer_schema(['spicy'], ['CONVERSION']), ensure_ascii=False)[:400])"
uv run uvicorn geoji_ai.api.app:app --host 127.0.0.1 --port 8100 --http h11 & curl -s localhost:8100/health/live
```

- `/health/live` 는 프로세스가 살아 있으면 200 이다.
- `/health/ready` 는 벤더 키나 `DATABASE_URL` 이 없으면 503 과
  `{"status":"not_ready","missing":[...]}` 를 낸다. `DATABASE_URL` 이 있는데 2초 안에 `SELECT 1` 이
  안 되면 503 과 `{"status":"not_ready","db":"unreachable"}` 이다.

2026-09-15 백엔드는 로컬 연결에 `--http h11`이 필요했다. 9/16 로컬 구현에서는 Java의
HTTP 클라이언트 3곳에 HTTP/1.1을 명시했고 Uvicorn 기본 `auto`로 실제 연결을 검증했다.
이 변경을 적용하지 않은 백엔드에는 위 `--http h11` 우회를 사용한다.

백엔드·워커 동시 실행, 전원 투표 시뮬레이션, 짤·카드 저장의 구현 현황과 다음 검증은
[로컬 구현·재실행 가이드](docs/plans/15-local-e2e-implementation.md)를 참고한다.
키 없는 워커의 `TEMPLATE_READY`는 폴백 검증이며 실제 LLM 생성 성공을 뜻하지 않는다.

### 실제 Spring + 테스트 모델 E2E

Docker, JDK 25, 빌드된 백엔드 JAR(`./gradlew bootJar -x test`)가 필요하다. 새 전용 DB만 만들며 외부 모델 키를 제거한다.
macOS·Linux·Windows 에서 같은 명령이다(9/16 Windows 이식: 프로세스 그룹·종료·java 경로·글꼴만 OS 별).
`--java-home` 은 `JAVA_HOME` 이 있으면 생략한다.

```bash
uv run scripts/run_local_e2e.py --backend /path/to/geoji-server --frontend /path/to/geoji-web --keep
# 출력된 geoji-e2e-* 디렉터리로 Vite 실행(Node 24, pnpm 필요)
uv run scripts/start_local_e2e_web.py /path/to/geoji-e2e-run --frontend /path/to/geoji-web
# 브라우저: http://localhost:3800
uv run scripts/stop_local_e2e.py /path/to/geoji-e2e-run
```

`--keep` 없이 실행하면 자동 종료한다. `report.json`은 검증 결과이며, `state.json`에는
짧게 사용하는 로컬 JWT·DB 비밀번호가 있어 공유하지 않는다.

### 실제 모델로 판결문 보기 (유료, 실측)

`scripts/run_local_live_e2e.py` 는 같은 스택을 **실제 키**(루트 `.env` 의 `OPENAI_API_KEY`·`XAI_API_KEY`)로 띄워
게시물 1건을 등록하고 배심원 3명이 투표한 뒤 판결문을 stdout 과 `report.json` 의 `verdict` 에 남긴다.
인자 없이 실행하면 비용 없는 계획 출력이다. 호출 수·예약 상한을 넘으면 그 호출은 `BUDGET` 으로 막힌다.
server main 에 아직 없는 짤 관리자 API·PNG 렌더(10 §16.6)는 404 면 건너뛰고 `report.json` 의 `skipped` 에 적는다.

```bash
uv run scripts/run_local_live_e2e.py                                   # 계획만 출력
uv run scripts/run_local_live_e2e.py --execute-approved \
  --backend /path/to/geoji-server --frontend /path/to/geoji-web \
  --intensity hell --keep                                              # mild|spicy|hell, 기본 12회·$0.15
# 끝나면 "===== 판결문 =====" 블록이 찍힌다. --keep 이면 같은 디렉터리로 웹을 띄워 화면에서도 본다
uv run scripts/start_local_e2e_web.py /path/to/geoji-e2e-run --frontend /path/to/geoji-web
uv run scripts/stop_local_e2e.py /path/to/geoji-e2e-run
```

Windows 에서 한글이 깨지면 `PYTHONUTF8=1` 을 앞에 둔다. 다른 강도를 보려면 `--intensity` 로 방 강도를 바꿔
다시 돌린다(판결문은 방 강도로만 나온다). 예산은 `--max-calls`·`--cap-usd`.

### 큐와 워커 (02 §4.3)

로컬 Postgres(`ai.jobs` 큐·통합 테스트용)는 compose 로 띄운다. 운영은 Supabase 다(D-20).

```bash
docker compose -f docker-compose.dev.yml up -d postgres
uv run geoji-ai migrate                    # database/migrations 의 001~003 적용
uv run geoji-ai worker --reaper &          # 로컬 1개. --reaper 는 개발 전용이다
uv run scripts/enqueue_job.py --kind PREPARE --post p1 --version 1 --audience 1
psql "$DATABASE_URL" -c "select kind,status,attempts,owner_id,lease_until,last_error_code from ai.jobs order by created_at desc limit 5"
```

`geoji-ai migrate` 는 `database/migrations/NNN_*.sql` 을 번호 순으로 적용하고
`ai.schema_migrations` 에 기록한다. 이미 적용된 번호는 건너뛴다. **004 부터는 백엔드 소유라
우리 러너가 적용하지 않는다**(02 §3.6). `ai_worker`·`backend` role 은 Supabase 에서 백엔드가 만든다
(10 §2). 로컬에서는 통합 테스트가 만든다.

### M2 수직 흐름 (03 §4.3)

가짜 백엔드(`tests/fakes/backend_app.py`)에 판결 `v1`/`p1` 이 심어져 있다. 워커와 가짜 백엔드는
같은 로컬 토큰을 쓴다(값은 아무거나. 비어 있으면 워커 호출이 401 이다).

```bash
SERVICE_AUTH_TOKEN=local-dev uv run uvicorn tests.fakes.backend_app:app --port 8200 &   # 가짜 백엔드
BACKEND_INTERNAL_URL=http://localhost:8200 SERVICE_AUTH_TOKEN=local-dev uv run geoji-ai worker --reaper &
uv run scripts/enqueue_job.py --kind SENTENCE --verdict v1 --version 1 --post p1
curl -s localhost:8200/posts/p1/verdict | jq '.sentence_status, .text_status, .sentence_source, .view.source'   # FINAL TEMPLATE_READY RULE TEMPLATE
uv run scripts/probe_writer_latency.py --provider openai --role sentencing --n 5   # 실측은 키가 있을 때만
```

### 현재 카드 문구 세 강도 점검

현재 운영 코드와 같은 요청 조립·프롬프트·JSON 스키마를 사용한다. 합성 택시비 12,000원,
늦잠 사유에 유죄·징역 1일을 가정하고 순한맛·매운맛·지옥맛 서기만 점검한다.

```bash
uv run python scripts/probe_verdict_cards.py --out /tmp/cards-dry.json
uv run python scripts/probe_verdict_cards.py --fake --out /tmp/cards-fake.json
# XAI_API_KEY를 실행 환경 또는 git에서 제외된 .env에 설정한 뒤 실행 (유료 최대 3회)
uv run python scripts/probe_verdict_cards.py --execute --out /tmp/cards-live.json
```

기본 실행은 모델을 호출하지 않는다. `--execute`는 `MODEL_WRITER`,
`WRITER_MAX_OUTPUT_TOKENS`, `WRITER_NODE_TIMEOUT_SECONDS`를 그대로 사용하고 자동 재시도하지 않는다.
보고서에는 원본 구조화 응답, 메타데이터, 형식 검사, 사용량·비용·지연시간이 담긴다.
키 누락·호출 실패·형식 위반은 종료 코드 1이다. 종료 코드 0은 **카드 형식 검사 통과**이며,
근거 라벨 존재 여부는 별도 표시한다. 양형·검수·최종 저장·화면 줄바꿈까지 검증하지 않는다.
`--fake`는 고정 fixture이며 실제 생성 결과가 아니다.
기존 `probe_writer_latency.py`는 과거 고정 프롬프트 비교용이므로 현재 카드 검증에는 이 도구를 쓴다.

### 역할 간 핸드오프 추적 (9/19)

판결문이 이상할 때 어느 역할이 무엇을 받고 무엇을 넘겼는지 한 파일로 본다. 심문관 → 조서·드립 →
양형관·서기·검수관·finalize 를 같은 사건으로 잇고 호출마다 시스템 프롬프트·입력·출력을 적는다.
백엔드·DB 는 메모리 fake(`tests/fakes/pipeline.py`)라 아무것도 저장하지 않는다.

```bash
uv run scripts/trace_pipeline.py --out trace.md                          # FakeLLM, 무료
uv run scripts/trace_pipeline.py --execute --out trace.md                # 실제 모델, 약 8~10회 호출
uv run scripts/trace_pipeline.py --execute --case 5 --first-spend --intensity hell --out trace.md
```

같은 러너로 도는 단위 테스트가 `tests/unit/test_agent_handoff.py` 다. 역할마다 "받는 것"과 "넘기는 것"을
단언하고, 알려진 끊김(심문관 결과·조서 사유 분석이 어디에도 안 감, 드립이 방 강도로만 생성)은
`test_gap_*` 로 못박아 두었다. `GEOJI_HANDOFF_TRACE=경로` 를 주면 fake 추적 마크다운을 저장한다.

### 게이트

```bash
uv run ruff check .
uv run ruff format .
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres uv run pytest -q
```

`tests/integration/` 은 실제 Postgres 를 쓴다(02 §4.2). 접속은 **`TEST_DATABASE_URL`** 환경변수로만
받는다. 설정 필드가 아니라 pytest 만 읽으므로 `.env.example` 에는 주석 줄로 있다. 값이 없으면 통합
테스트는 **실패**한다 — 조용히 넘기지 않는다. 테스트는 `ai` 스키마를 매번 지우고 다시 만드니
운영 DB 를 가리키지 않는다.

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
| `GUARDRAIL_POLICY_VERSION` | `guardrail-v2` | `guardrail-v1` \| `guardrail-v2`(D-07). 검사표 내용은 9/16 개정(지옥맛 비속어 제한 해제). **production 에서는 환경변수에 직접 적어야 한다.** 안 적고 기본값에 기대면 기동 실패 |
| `INTAKE_TIMEOUT_SECONDS` | `4` | 심문관 |
| `FIRST_RESULT_TARGET_SECONDS` | `90` | 첫 결과 목표. 9/16 백엔드 SENTENCE 마감 90초에 맞춤(옛 10) |
| `REPAIR_PATH_BUDGET_SECONDS` | `15` | 복구 경로 예산 |
| `SENTENCING_NODE_TIMEOUT_SECONDS` | `12` | 9/16 luna 실측 p90 5.7초·백엔드 관측 8.6초(옛 3) |
| `WRITER_NODE_TIMEOUT_SECONDS` | `10` | grok 실측 p90 5.8초 + repair 여유(옛 6) |
| `JUROR_TIMEOUT_SECONDS` | `10` | 데모 AI 배심원 모델 1회 상한(서기 노드와 같다). 작업 18 |
| `EVALUATOR_NODE_TIMEOUT_SECONDS` | `30` | 9/16 luna 실측 13.9~17.3초, reasoning 900~1,400 토큰(옛 4 — 늘 TIMEOUT 이었다) |
| `WORKER_POLL_MS` | `250` | 작업 2 |
| `JOB_LEASE_SECONDS` | `15` | 작업 2 |
| `HEARTBEAT_SECONDS` | `5` | 작업 2 |
| `WORKER_SLOTS` | `{"SENTENCE":2,"PREPARE":1,"BACKGROUND":1,"JURY":1}` | JSON. 작업 2. `JURY` 는 작업 18 — `.env` 에 옛 값을 적어 뒀다면 `JURY` 를 더해야 봇이 투표한다 |
| `WORKER_SHUTDOWN_DEADLINE_SECONDS` | `10` | 종료 시 진행 중 핸들러에 주는 시간. 작업 2 |
| `REAPER_INTERVAL_SECONDS` | `5` | `--reaper` 의 lease 회수 주기. 작업 2 |
| `FINALIZE_RESERVE_MS` | `500` | 작업 5 |
| `INLINE_CONTEXT_MIN_REMAINING_MS` | `8500` | 작업 5 |
| `IMMEDIATE_REPAIR_MAX` | `1` | |
| `TEXT_RETRY_ROUNDS` | `3` | |
| `TEXT_RETRY_TIMEOUT_SECONDS` | `60` | 서기 10 + 검수 30 + finalize. 백엔드 TEXT_RETRY 마감도 60 이어야 한다(옛 20) |
| `RECALL_CANDIDATE_LIMIT` | `20` | 작업 4 |
| `EVIDENCE_PACK_LIMIT` | `12` | 작업 4 |
| `STYLE_EXAMPLE_LIMIT` | `3` | 작업 4 |
| `MODEL_CONCURRENCY_LIMIT` | `8` | D-22 |
| `ALERT_DISCORD_WEBHOOK_URL` | — | **비밀값**. 알림 규칙은 08 §3.3 |
| `COST_ALERT_KRW_PER_DAY` | `5000` | 평가 실행(`GEOJI_EVAL=1`)분은 별도 집계 |
| `MAX_TOTAL_PROMPT_TOKENS` | `6000` | |
| `WRITER_MAX_PROMPT_TOKENS` | `8000` | |
| `INTAKE_MAX_OUTPUT_TOKENS` | `300` | 잘리면 스키마 실패로 처리한다 |
| `CONTEXT_MAX_OUTPUT_TOKENS` | `1500` | 9/16 luna reasoning 포함(옛 700) |
| `BANTER_MAX_OUTPUT_TOKENS` | `1200` | |
| `SENTENCING_MAX_OUTPUT_TOKENS` | `2000` | 9/16 luna reasoning 이 400 을 다 먹어 형량이 RULE 로 떨어졌다(옛 400) |
| `WRITER_MAX_OUTPUT_TOKENS` | `700` | 강도 1개당 |
| `JUROR_MAX_OUTPUT_TOKENS` | `120` | 두 키 JSON 이면 충분하다. 작업 18 |
| `EVALUATOR_MAX_OUTPUT_TOKENS` | `3000` | OpenAI 는 reasoning 토큰을 포함해 센다(옛 800 이면 잘림) |
| `ROOM_COMMENT_STYLE_ENABLED` | `false` | |
| `PUBLIC_HISTORY_CALLBACK_ENABLED` | `false` | |
| `REFLECT_ENABLED` | `false` | |
| `HINDSIGHT_ENABLED` | `false` | hell 을 끄는 플래그는 없다 — 정책 버전으로 통제한다 |

### 기동 검사 (`core/startup.py`)

1. production 인데 `GUARDRAIL_POLICY_VERSION` 을 환경변수로 직접 적지 않았으면(기본값 의존) `StartupError`. 목록 밖 값은 설정 생성 단계에서 이미 거부된다.
2. `MODEL_WRITER` 가 `grok-4.6`·`grok-4.5`·`grok-4.3` 이면 `StartupError`(환경 무관).
3. `OPENAI_API_KEY`·`XAI_API_KEY` 가 비면 기동은 되고 `/health/ready` 가 503 을 낸다.

### 배포 시 키 주입

`Settings`는 이미 **프로세스 환경변수 → 로컬 `.env` → 기본값** 순서로 읽는다.
배포 환경에는 `.env` 파일을 만들 필요가 없다. 배포 플랫폼의 비밀값 설정을 통해
AI API와 worker 프로세스에 아래 이름으로 주입한다.

| 변수 | 주입 대상/용도 |
| --- | --- |
| `OPENAI_API_KEY`, `XAI_API_KEY` | AI API와 worker. 실제 텍스트 모델 호출 |
| `DATABASE_URL` | AI API와 worker. `postgresql+asyncpg://...` 연결 |
| `SERVICE_AUTH_TOKEN` | AI API·worker·백엔드에 같은 내부 인증 토큰 |
| `BACKEND_INTERNAL_URL` | AI 프로세스에서 접근할 백엔드 호스트 루트. `/internal/v1`을 붙이지 않음 |
| `APP_ENV=production`, `GUARDRAIL_POLICY_VERSION=guardrail-v2` | AI API와 worker의 운영 기동 검사 |

키 설정 여부만 확인하며 실제 값이나 모델 호출 없이 검사할 수 있다:

```bash
uv run python - <<'PY'
from geoji_ai.core.config import Settings, secret_value
s = Settings(_env_file=None)
print({name: bool(secret_value(s, name)) for name in ("OPENAI_API_KEY", "XAI_API_KEY")})
PY
```

키 변경 후 **API와 worker 모두 재시작**한다. 실행 중인 설정·벤더 클라이언트는 자동 갱신되지 않는다.
기동 후 `/health/ready`는 키 존재와 DB 연결을 확인한다. 200이어도 모델 계정 권한/결제까지
검증한 것은 아니며, 실제 호출 검증은 승인된 실측 실행기로 별도 수행한다.
이미 만들어진 b-meme 파일의 백엔드 등록·검수·활성화는 두 LLM 키 및 AI readiness와 독립이다.

## 배치

경로와 의존 방향은 `.claude/rules/code-layout.md`, 용어와 enum 은
`.claude/rules/domain-vocabulary.md`, 테스트 규칙은 `.claude/rules/testing.md` 에 있다.
