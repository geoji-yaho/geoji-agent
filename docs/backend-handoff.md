# 백엔드 전달 사항

AI 파트(`geoji-agent`)가 백엔드 담당에게 알려야 하는 것을 모아 둔다. 계약 자체는 `docs/plans/10-backend-contract.md`(10)에 있고, 이 파일은 **그 계약을 실제로 붙이고 띄울 때 알아야 하는 것**만 담는다. 환경변수, 엔드포인트, 복사해 갈 파일, 바뀐 값, 우리가 백엔드에 기다리는 회신.

읽는 법. 위가 최신이다. 상태가 `미전달` 인 줄을 백엔드에 보내고 `전달 9/NN` 으로 바꾼다. 백엔드가 반영하면 `반영` 으로.

## 1. 배포 환경변수 (EC2 compose 에 넣을 것)

`ai-api`·`ai-worker` 컨테이너 둘 다 같은 목록을 받는다. 키 이름 전체는 저장소 루트 `.env.example`, 기본값·설명은 `README.md` 설정 표. 여기는 **직접 적어야 하는 것**만.

| 키 | 값 | 없으면 | 비고 |
|---|---|---|---|
| `APP_ENV` | `production` | 개발 모드로 뜬다(기동 검사 느슨) | 9/11 확정 |
| `GUARDRAIL_POLICY_VERSION` | `guardrail-v2` | **기동 실패.** production 은 이 값을 환경에 직접 적어야 한다 | 01 §3.7 ①, 10 §15.3 D-07. 팀 비준 뒤 `v1` 로 내릴 수 있다 |
| `OPENAI_API_KEY` · `XAI_API_KEY` | AI 파트가 전달 | 뜨긴 하나 `/health/ready` 가 503 | 10 §15.3 키·결제. 로그 금지 |
| `DATABASE_URL` | Supabase Session Pooler 접속 문자열(`ai_api`/`ai_worker` role) | 작업 2 부터 `/health/ready` 503 | 10 §2, D-20 |
| `BACKEND_INTERNAL_URL` | 백엔드 내부 API 베이스 URL | 작업 3 부터 판결 흐름 실패 | 10 §4 |
| `SERVICE_AUTH_TOKEN` | 내부 API 서비스 토큰(양쪽 같은 값) | 작업 3 부터 401 | 10 §4 "서비스 인증 필수" |
| `ALERT_DISCORD_WEBHOOK_URL` | 팀 디스코드 웹훅 | 알림 없음 | 10 §15.3 |

## 2. 엔드포인트

| 경로 | 응답 | 용도 | 시점 |
|---|---|---|---|
| `GET /health/live` | 200 `{"status":"ok"}` | 프로세스 생존. compose healthcheck 에 쓴다 | 지금 |
| `GET /health/ready` | 200 또는 503 `{"status":"not_ready","missing":[키 이름]}` | 키·DB 준비 여부. 503 이면 트래픽을 보내지 않는다 | 지금(키), 작업 2(DB) |
| `POST /internal/v1/intake` | `IntakeResult` | 심문관. 백엔드가 호출 | 작업 7 |

## 3. 복사해 갈 파일

| 파일 | 어디에 | 왜 |
|---|---|---|
| `contracts/fixtures/templates-v1.json` | 백엔드 저장소, 버전 고정 | watchdog·generation-failed 폴백 문구(10 §10). 치환 토큰 `{n}`·`{m}`·`{sentence_label}` (9/11 `{형량 라벨}` → `{sentence_label}` 로 바뀜) |
| `contracts/*-v1.schema.json` 7종 | 참조 | 계약 정본. enum 은 프론트 값(`mild/spicy/hell`, `guilty/notGuilty/agree/disagree/dismissed`, `probation/oneDay/life`, `spent/considering`). 서버의 대문자 enum 은 백엔드가 맞춘다(10 §15.2 D-21) |

## 4. 계약에서 백엔드가 알아야 할 것

| 날짜 | 항목 | 내용 | 출처 | 상태 |
|---|---|---|---|---|
| 9/11 | `CaseSnapshot` 모양 | **평면**이다. `post_id`·`author_id`·`item`… 이 최상위. 10 §4.1 의 `post{…}` 중첩이 아니라 01 §3.2 를 따른다 | 01 §3.2, 10 §4.1 | 미전달 |
| 9/11 | `guilty_ratio` | 0..1 소수(`0.75`). 백분율 아님 | 10 §4.1 | 미전달 |
| 9/11 | `SentencingDecision.reason_source`·`TextDraft.source` | finalize 본문에 들어간다. 둘 다 `AI \| TEMPLATE` 필수 | 00 §8.3, 10 §5 | 미전달 |
| 9/11 | `IntakeResult.mode` | 결과에 `mode`(`INITIAL \| FINAL_CHECK`)가 들어간다. `FINAL_CHECK` 는 `NEEDS_CLARIFICATION` 을 내지 않는다 | 01 §3.2 | 미전달 |
| 9/11 | 길이·배열 상한 | 계약에 없던 상한을 채웠다(라벨 ≤16·`^F\d+$`, `statement` 1~300자 등). 목록은 01 §3.2 공통 규칙 | 01 §3.2 | 미전달 |

## 5. 백엔드 회신을 기다리는 것

| 항목 | 무엇 | 기한 | 출처 |
|---|---|---|---|
| `CaseSnapshot` 필드 | `post_version`·`audience_version`·`privacy_versions`·`rule_version`·`policy{…}` 를 채울 수 있는가. 9/11 서버(`d0f9fd5`)에 전부 없음 | 9/9(지남) | 10 §14 |
| `ai` 스키마 role | `ai_api`·`ai_worker` role·grants, Session Pooler 접속 정보 | 9/9(지남) | 10 §14 D-20 |
| §4.6 오류 코드 표 채택 | `generation-failed` 의 `error_code` 표 | 9/10 | 10 §14 |
