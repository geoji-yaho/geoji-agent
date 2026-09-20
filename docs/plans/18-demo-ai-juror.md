# 🛠️ [Tech Spec] 기술 명세서: 작업 18 — 데모 AI 배심원(떼거지봇) · `JURY_VOTE` 잡 · 템플릿 글 (9/20)

> 근거: 9/20 사용자 인터뷰(4라운드, 아래 §1 결정 표). 먼저 실패시킬 케이스(모델 정상 표 · 모델 실패 템플릿 표 · 마감된 글 skip · 남의 id 로 투표 403 · 공유 안 된 방 skip).
> **선행 문서: 02(큐)·05(그래프)·06(프롬프트).** 백엔드 몫은 **19**(`19-backend-handoff-ai-juror.md`), 상시 계약은 10. 이 문서는 **우리 쪽**만 다룬다.
> 완료 기준: **사람 1명 + 떼거지봇 방에서 사람이 글을 올리면 5~10초 안에 떼거지봇 표(방 강도 말투 사유)가 붙고 평결이 확정돼 판결문까지 간다. 모델이 죽어도 템플릿 사유로 표는 들어간다.**

## 1. 개요 및 구현 목표

### 목적:
심사 데모에서 방을 만든 사람이 친구를 모으지 않아도 재판이 돈다. '데모용 AI 유저 추가' 버튼을 누르면 고정 AI 사용자 **떼거지봇**이 방 멤버가 되고 템플릿 글 2개(지출 1·살지말지 1)가 올라간다. 이후 그 방에 사람이 글을 올리면 떼거지봇이 **상황을 보고** 표와 사유를 만들어 투표한다. LLM 이 들어가는 이유는 표와 사유가 글의 금액·항목·사유·방 강도에 맞아야 하기 때문이다.

### 핵심 플로우:
1. (백엔드) 사람이 게시물을 등록 → `PostCreator` 트랜잭션이 공유 방 중 **떼거지봇이 멤버이고 작성자가 떼거지봇이 아닌 방마다** `ai.jobs` 에 `JURY_VOTE` 1개 INSERT(PREPARE 와 같은 트랜잭션)
2. (워커, `JURY` 슬롯) claim → `GET /internal/v1/ai-jobs/{job_id}/snapshot`(기존, `jury=null`) → `payload.room_id` 의 강도 → `juror` 역할로 LLM 1회 → 코드 검증(평결 enum·사유 1~60자) → 실패면 템플릿 표
3. (워커 → 백엔드) `POST /internal/v1/posts/{post_id}/jury-votes` → 백엔드가 `PostVoteService.cast(postId, 떼거지봇, …)` → 정족수 min(2, 가능 인원) 규칙으로 사람 1 + 봇 1 방이면 그 자리에서 평결 확정 → 기존 D-24 게이트 → SENTENCE → 판결문
4. (백엔드) 떼거지봇 명의 템플릿 글은 사람이 한 표 던지면(가능 인원 1) 확정 → 같은 경로로 판결문

### 현상태
- 서버: `votes.voter_id → profiles.id`, `PostVoteService.cast` 는 JWT 없이 사용자 id 로 부를 수 있다(시드 러너가 이미 이 방식). 워커가 표를 넣을 내부 API 는 없다. `ai.jobs.kind` 는 DB CHECK 4종
- AI: `LLMRole` 6종, `JobKind` 4종, 슬롯 3종. 스냅샷 계약은 `jury` 가 null 인 경우를 이미 허용(PREPARE)

### 9/20 인터뷰 결정 (사용자)

| # | 결정 | 채택 | 버린 안 |
|---|---|---|---|
| 1 | 일정 | **오늘(9/20) 안에 에이전트·백엔드·프론트 전부** | 심사 기간 중 배포, 에이전트만 |
| 2 | 표 전달 경로 | **새 잡 종류 `JURY_VOTE` → 워커 → 백엔드 내부 API** | 백엔드가 AI API 동기 호출(intake 식), PREPARE 잡에 얹기 |
| 3 | AI 유저 | **고정 1명, 전 방 공용.** 닉네임 **떼거지봇**. Supabase auth 사용자 1개를 관리자가 만들고 id 를 백엔드 환경변수 `GEOJI_AI_JUROR_USER_ID` 로 | 2~3명 페르소나, 방마다 생성 |
| 4 | 템플릿 글 | **백엔드가 고정 템플릿으로 등록**(시드 러너와 같은 `SubmissionService` 경로). 내용은 **시드 글 재사용**: 지출 `심야 택시 32,000원 · 교통/택시 · "막차가 끊겨서 어쩔 수 없었어요"`, 살지말지 `무선 이어폰 189,000원 · 쇼핑/패션 · "기존 이어폰 한쪽이 안 들려요"` | 에이전트가 LLM 으로 생성, 이번엔 빼기 |
| 5 | 투표 시점 | **바로**(워커가 집는 대로, 등록 뒤 5~10초) | 지연, 마감 직전 |
| 6 | 성향·말투 | **LLM 이 상황 보고 판단, 말투는 방 강도**(서기 강도 원칙 재사용) | 유죄 편향, 규칙 표 + LLM 사유 |
| 7 | 모델 실패 | **템플릿 사유로 그래도 투표**(`source=TEMPLATE`) | 투표 안 함, 재시도 뒤 템플릿 |
| 8 | 투표 대상 | **추가된 뒤 그 방에 새로 올라오는 글마다**(방마다 한 표). 기존 글은 그대로 | 추가 시점 일괄 |
| 9 | 워커 입력 | **기존 snapshot API 재사용**(`jury=null`, `room_snapshots` 는 공유 방 전부) | 새 조회 API, payload 에 내용 통째 |
| 10 | 사유 길이 | **사람 표처럼 1~2문장 60자 이내** | 120자 |
| 11 | 마이그레이션 | **006 새 번호, AI 가 운영 DB 에 적용**. 005 는 P1 예약 유지 | 005 사용 |
| 12 | 모델 | **새 역할 `juror` → xAI `MODEL_WRITER`** | OpenAI 판단 모델, writer 역할 재사용 |
| 13 | 프론트 전달 | **이 문서 §2 다른 파트에 요청 표**(백엔드는 19) | 10 §0.1, 구두 |
| 14 | 이름·계약 | 아래 §3.1·§3.6 제안 그대로 | — |

## 2. 작업 범위 (Scope Boundary)

### In-Scope

| # | 항목 | 등급 | 난이도 |
|---|---|---|---|
| 3.1 | `JURY_VOTE` 잡 계약(payload·route·슬롯)과 006 마이그레이션 | Critical | S |
| 3.2 | `juror` 역할(라우터·가짜 LLM fixture)과 출력 계약 `JurorVote` | Critical | S |
| 3.3 | 프롬프트 `prompts/juror-v1.md` + 강도 섹션 결합 | High | S |
| 3.4 | 그래프 D(`graphs/jury_vote.py`): 스냅샷 → 모델 → 검증 → 템플릿 폴백 | Critical | S |
| 3.5 | 핸들러(`application/jury_vote_case.py`)와 백엔드 거부 처리 | Critical | S |
| 3.6 | 백엔드 포트·어댑터 `cast_jury_vote` + 가짜 백엔드 라우트 | Critical | S |
| 3.7 | 설정·문서(`JUROR_*`, `WORKER_SLOTS.JURY`, README·`.env.example`, `scripts/enqueue_job.py`) | Medium | S |

### Out-of-Scope
- 떼거지봇 댓글, 떼거지봇 글에 대한 떼거지봇 투표(작성자 제외 규칙으로 원래 불가), 여러 페르소나 → P1(09)
- 표 사유를 서기 입력에 넣기 → 9/18 제안(archive/10 9/19 판 §14 "투표 사유") 회신 뒤
- 템플릿 글 내용 생성·게시물 등록 → 백엔드(19 §6·§7)
- '데모용 AI 유저 추가' 버튼 → 프론트(아래 표)

### 다른 파트에 요청

| 대상 | 요청 | 기한 |
|---|---|---|
| 백엔드 | 19 §0 체크리스트 전부: Supabase auth 사용자 **떼거지봇** 1개 + `profiles` 행, `GEOJI_AI_JUROR_USER_ID`, `JobKind.JURY_VOTE(60, 2, null)` + `PostCreator` INSERT, snapshot 이 `JURY_VOTE` 도 받기, `POST /internal/v1/posts/{postId}/jury-votes`(19 §5), `POST /api/rooms/{roomId}/ai-member`(19 §6), 테스트 DDL `001_ai_jobs.sql` 복사본 CHECK 에 `JURY_VOTE` | 9/20 |
| 프론트 | 방 화면의 '링크로 초대' 옆에 **'데모용 AI 유저 추가'** 버튼. 누르면 `POST /api/rooms/{roomId}/ai-member`(JWT, 요청자가 방 멤버) → `201 {userId, nickname: "떼거지봇", postIds: [2개]}`. 멱등이라 다시 누르면 `200` 같은 모양. 성공 뒤 멤버 목록과 방 피드를 다시 불러온다(글 2개가 바로 보인다). 오류: 404 `{"message"}`(멤버 아님·방 없음), 503 `{"code":"AI_JUROR_NOT_CONFIGURED"}`(백엔드에 봇 id 미설정) → "지금은 추가할 수 없어요" 토스트. 멤버 목록에 떼거지봇이 이미 있으면 버튼을 '추가됨' 으로 비활성화해도 되고 그대로 두어도 된다(서버가 멱등). 이후 사람이 글을 올리면 5~10초 뒤 피드에 떼거지봇 표가 붙고 사람 1 + 봇 1 방이면 바로 평결·판결문으로 넘어간다. 기존 폴링 그대로 | 9/20 |

### 팀 결정 대기
- 없음. §1 표로 전부 확정

## 3. 기술 상세 설계 (Technical Design)

### 3.1 `JURY_VOTE` 잡 계약 (`contracts/jobs.py`, `workers/dispatch.py`, `core/config.py`, `database/migrations/006_jury_vote_kind.sql`, `adapters/postgres_migrations.py`)

10 §3 표의 `JURY_VOTE` 행(백엔드 INSERT 조건은 19 §3):

| kind / event_type | dedupe_key | priority | max_attempts | deadline_at | payload | aggregate_id / version |
|---|---|---:|---:|---|---|---|
| `JURY_VOTE` / `jury.vote_requested` | `jury-vote:{post_id}:{room_id}:{voter_id}` | 60 | 2 | null | `{post_id, post_version, room_id, voter_id}` | `post_id` / `post_version` |

- `JobKind` Literal 에 `"JURY_VOTE"`, `JuryVotePayload`(`extra="forbid"`, 네 키 모두 필수 문자열·정수). `_PAYLOAD_BY_KIND` 에 등록
- `JOB_ROUTES["jury.vote_requested"]`, `HANDLERS["JURY_VOTE"] = JuryVoteHandler()`
- 슬롯: `SLOT_KINDS["JURY"] = ("JURY_VOTE",)`, `WORKER_SLOTS` 기본값에 `"JURY": 1`. BACKGROUND 에 넣지 않는 이유: TEXT_RETRY(최대 60초) 뒤에 줄을 서면 데모의 5~10초 약속이 깨진다
- **006**: `ALTER TABLE ai.jobs DROP CONSTRAINT jobs_kind_check; ALTER TABLE ai.jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN ('PREPARE','SENTENCE','TEXT_RETRY','RETAIN','JURY_VOTE'));`(001 의 열 CHECK 자동 이름). 러너 `MAX_OWNED_VERSION` 3 → **6**, 그리고 백엔드 소유 번호 `BACKEND_OWNED_VERSIONS = {4}` 를 명시적으로 건너뛴다(004 초안이 저장소에 들어와도 적용하지 않는다). 005 파일은 없으므로 001·002·003·006 이 적용된다. 통합 테스트 `test_러너는_004_를_읽지도_적용하지도_않는다` 의 `== 3` 단언을 새 규칙으로 고친다
- 운영 적용: `DATABASE_URL=<Session Pooler URL> uv run geoji-ai migrate` 1회(이미지 배포 전, 19 §8). 백엔드가 `JURY_VOTE` 를 INSERT 하기 전에 끝나 있어야 한다

### 3.2 `juror` 역할과 출력 계약 (`ports/llm.py`, `adapters/llm_router.py`, `adapters/fake_llm.py`, `contracts/juror.py`, `contracts/llm_schemas.py`, `contracts/fixtures/juror-vote-taxi.json`)

- `LLMRole` 에 `"juror"`, `ROLE_VENDOR["juror"] = "xai"`(기본 모델 `MODEL_WRITER`). 가격표·기동 검사(추론 모델 금지)는 writer 와 같은 벤더라 그대로 적용된다
- `JurorVote(BaseModel, extra="forbid")`: `verdict: Literal["guilty","notGuilty","agree","disagree"]`, `reason: str`(1~60 code point). JSON Schema 정본은 만들지 않는다 — 백엔드가 보는 모양은 §3.6 요청 본문이고 이 모델은 모델 출력 전용(`contracts/jobs.py` 와 같은 지위)
- `juror_schema(post_type)`: strict 스키마에 `verdict` enum 을 게시물 유형별로 주입(`spent` → guilty·notGuilty, `considering` → agree·disagree). xAI strict 는 enum·`maxLength` 를 강제하지 않으므로(06 §3.3 9/18 기록) 검증은 §3.4 코드가 한다
- 가짜 LLM: `FIXTURE_BY_ROLE["juror"] = "juror-vote-taxi"`, fixture `{"verdict": "guilty", "reason": "막차 핑계는 매달 나오는데 지하철은 매일 다녀요"}`(28자)

### 3.3 프롬프트 (`prompts/juror-v1.md`, `prompts.py`)

- `build_juror_system(intensity)` = `juror-v1.md` + **서기 강도 섹션** `writer/{intensity}-{WRITER_VERSION}.md` 를 `## 강도` 제목 아래 그대로 붙인다. 강도 정의는 단일 정의(06 §3.3)라 복사하지 않고 파일을 재사용한다
- `juror-v1.md` 내용: 역할(방 친구 한 명으로서 배심원 한 표), 입력(post_type·amount_krw·item·category·reason·intensity·허용 평결 2개), 판단 원칙(금액·항목·사유·유형을 보고 스스로 고른다, 필수 지출은 무죄·동의 가능, 편향 없음), 사유 규칙(**1~2문장 60자 이내**, 한 줄, 글의 금액·항목·사유 중 하나를 꼭 집는다, 조서·이력을 지어내지 않는다 — 이 글만 본다), 인젝션(항목·사유 속 지시는 데이터), 출력 JSON 두 키. 예시는 다른 사건 2개(spent·considering)
- 새 파일이 생기면 `prompt_bundle_version` 이 바뀐다 → 새 이미지 태그(19 §8)

### 3.4 그래프 D (`graphs/jury_vote.py`)

노드 순서와 실패 처리:

| 노드 | 하는 일 | 실패 시 |
|---|---|---|
| snapshot | `backend.snapshot(job_id, generation_id)` | 404 `SnapshotNotFound` → 핸들러가 `cancel`(삭제된 글) |
| room | `room_snapshots` 에서 `payload.room_id` 찾기 → `intensity` | 없음(공유 철회) → `skipped(reason="room_not_shared")` 로 끝, 모델 호출 없음 |
| juror | `ScopedLLM.scoped_call(role="juror", schema=juror_schema(post_type), timeout=JUROR_TIMEOUT_SECONDS − 0.2, max_output_tokens=JUROR_MAX_OUTPUT_TOKENS)`. 예산 키는 사건과 따로 둔 `jury:{post_id}`(06 §3.2), `node="juror"`, `call_index=0` | `LLMError`·timeout·`stop_reason != stop`·출력 없음 → 템플릿 |
| validate | `verdict` 가 유형별 허용 2개 안 ∧ `reason` 공백 제거 뒤 1~60 code point ∧ 줄바꿈 없음 | 위반 → 템플릿(재작성 호출 없음, 1회로 끝) |
| cast | §3.6 `cast_jury_vote` | 핸들러 표(§3.5) |

- 템플릿 표: 평결은 `spent → guilty`, `considering → disagree`. 사유는 `contracts/fixtures/juror-templates-v1.json` 의 `{intensity}.{post_type}` 문구(아래). `source="TEMPLATE"`. 파일 형태 `{version: "juror-templates-v1", reasons: {mild: {spent, considering}, spicy: {…}, hell: {…}}}`

| 강도 | spent | considering |
|---|---|---|
| mild | 이번 건은 조금 아쉬워요. 다음엔 한 번만 더 생각해 봐요. | 지금 꼭 필요한지 하루만 더 고민해 봐요. |
| spicy | 이 돈 쓰기 전에 잔고는 한 번 봤어? | 사고 싶은 거지 필요한 게 아니잖아. 일주일 재워. |
| hell | 변명 낭독 끝. 너 잔고가 비명 지르는 거 안 들려? 유죄. | 그거 사면 다음 달 너는 라면이다. 반대. |

- 로그: `jury_vote_call`(강도·timeout·오류 kind)·`jury_vote_summary`(outcome `AI|TEMPLATE|SKIPPED`, verdict, reason 길이, fallback_reason). 사유 원문은 로그에 남기지 않는다(다른 노드와 같은 규칙)
- 지표: `jury_vote_total{outcome}` 카운터(`application/instrument.count`)

### 3.5 핸들러 (`application/jury_vote_case.py`)

`PrepareHandler` 와 같은 뼈대. 백엔드 응답별 job 처리:

| 응답 | job |
|---|---|
| 201 `{vote_id}` · skip(방 없음) | `complete` |
| 409 `VOTING_CLOSED` · `ALREADY_VOTED` | `complete`(로그 `jury_vote_skipped`) — 마감·확정·중복은 정상 |
| 409 `STALE_GENERATION` | `complete`(결과 폐기, 다른 핸들러와 같음) |
| 404 `NOT_FOUND`(snapshot 또는 cast) | `cancel(SNAPSHOT_NOT_FOUND)` — 삭제된 글 |
| 403 `NOT_AI_JUROR` | `fail("NOT_AI_JUROR", retry_after_s=None)` — 설정 불일치, 재시도해도 같으므로 attempts 소진 뒤 알림 |
| 401 · 그 밖의 403 | `fail("BACKEND_AUTH", 60)` |
| 422 `INVALID_REQUEST` | `fail("SCHEMA_INVALID", None)` |
| `BackendUnavailable` | `fail("BACKEND_UNAVAILABLE", retry_after_s)` |
| 그 밖의 예외 | 상위로(워커 `HANDLER_ERROR`) |

### 3.6 백엔드 포트·어댑터 (`ports/backend.py`, `adapters/backend_http.py`, `tests/fakes/backend_app.py`)

- `JuryVoteRequest(_Model)`: `job_id, generation_id, room_id, voter_id, verdict, reason, source: Literal["AI","TEMPLATE"]`. `JuryVoteResult(_Model)`: `vote_id: str`
- `BackendPort.cast_jury_vote(post_id, req) -> JuryVoteResult`
- `BackendHttp`: `POST /internal/v1/posts/{post_id}/jury-votes`, 헤더 5종·재전송 규칙은 §4.7 그대로, read timeout **2초**. 4xx 는 재전송하지 않는다(백엔드는 같은 표를 `ALREADY_VOTED` 로 멱등 처리)
- 가짜 백엔드: 같은 경로. 검증 순서 job RUNNING·generation·lease(아니면 409 `STALE_GENERATION`) → `voter_id == AI_JUROR_USER_ID`(아니면 403 `NOT_AI_JUROR`) → 글 없음 404 → 마감·확정 409 `VOTING_CLOSED` → 중복 409 `ALREADY_VOTED` → verdict 유형 불일치·reason 길이 422 `INVALID_REQUEST` → 201. 시나리오 훅으로 각 거부를 재현한다

### 3.7 설정·문서·도구 (`core/config.py`, `README.md`, `.env.example`, `scripts/enqueue_job.py`)

| 키 | 기본 | 뜻 |
|---|---|---|
| `JUROR_TIMEOUT_SECONDS` | `10` | 모델 1회 상한(서기 노드와 같음, grok p90 5.8초) |
| `JUROR_MAX_OUTPUT_TOKENS` | `120` | 두 키 JSON 이면 충분 |
| `WORKER_SLOTS` | `{"SENTENCE":2,"PREPARE":1,"BACKGROUND":1,"JURY":1}` | 슬롯 추가. `.env` 에 옛 값을 적어 뒀다면 `JURY` 를 더해야 봇이 투표한다 |

- `scripts/enqueue_job.py` 에 `JURY_VOTE` 추가(로컬 재현용, `--room-id --voter-id`)
- README 설정 표에 세 키, `.env.example` 에 이름

## 4. 완료 기준 (DoD)

### 4.1 정량 목표

| 지표 | 목표 | 측정 |
|---|---|---|
| 모델 정상 → AI 표 | fixture 평결·사유 그대로 `source=AI` 로 cast | `tests/unit/test_jury_vote_graph.py` |
| 모델 실패 4종(timeout·rate limit·refusal·스키마) → 템플릿 표 | 강도·유형별 문구, `source=TEMPLATE`, 모델 재호출 0 | `tests/unit/test_jury_vote_graph.py` |
| 검증 실패(허용 밖 평결·61자·빈 사유) → 템플릿 | 같음 | `tests/unit/test_jury_vote_graph.py` |
| 백엔드 거부 → job 상태 | §3.5 표 그대로 | `tests/unit/test_jury_vote_handler.py` |
| 요청 모양·헤더·재전송 | §3.6 | `tests/unit/test_backend_http.py` |
| payload·route | §3.1 | `tests/unit/test_jobs_contract.py` |
| 프롬프트 결합 | 강도 섹션 포함, 세 강도 다름 | `tests/unit/test_prompts.py` |
| 006 적용 | CHECK 에 `JURY_VOTE`, 004 건너뜀 | `tests/integration/test_migrations.py` |
| 게이트 | `uv run ruff check . && uv run pytest -q --ignore=tests/integration` 통과(Windows 기존 실패 36건 제외) | — |

### 4.2 검증 테스트 시나리오

`tests/unit/test_jury_vote_graph.py`
- [ ] spent 글·spicy 방·가짜 LLM OK → `guilty`·fixture 사유·`source=AI`·`room_id`·`voter_id` 가 payload 값
- [ ] considering 글 → 스키마 enum 이 agree·disagree, 가짜 출력이 `guilty` 면 검증 실패 → 템플릿 `disagree`
- [ ] TIMEOUT·RATE_LIMIT·REFUSAL·SCHEMA_MISMATCH 각각 → 템플릿, 모델 호출 1회
- [ ] 사유 61자·빈 문자열·줄바꿈 포함 → 템플릿
- [ ] 템플릿 문구가 강도(mild·spicy·hell)×유형(spent·considering) 6칸 그대로
- [ ] `payload.room_id` 가 `room_snapshots` 에 없음 → 모델 호출 0, cast 호출 0, `skipped`
- [ ] `llm=None`(키 없음) → 템플릿

`tests/unit/test_jury_vote_handler.py`
- [ ] 201 → `complete`
- [ ] 409 `VOTING_CLOSED`·`ALREADY_VOTED`·`STALE_GENERATION` → `complete`
- [ ] snapshot 404 → `cancel`
- [ ] 403 `NOT_AI_JUROR` → `fail` 재시도 없음(retry_after None)
- [ ] 401 → `fail("BACKEND_AUTH", 60)`
- [ ] `BackendUnavailable` → `fail("BACKEND_UNAVAILABLE")`

`tests/unit/test_backend_http.py`
- [ ] `cast_jury_vote` 경로·본문 7키·헤더 5종, 4xx 재전송 없음, 5xx 같은 바이트 2회 재전송

`tests/unit/test_jobs_contract.py`
- [ ] `JuryVotePayload` 네 키 필수·알 수 없는 키 거부, `build_dedupe_key` 가 `jury-vote:{post_id}:{room_id}:{voter_id}`
- [ ] `SLOT_KINDS["JURY"] == ("JURY_VOTE",)`, `handler_for("JURY_VOTE")`

`tests/unit/test_prompts.py`
- [ ] `build_juror_system("hell")` 이 juror 본문 + hell 섹션, 세 강도 결과가 서로 다름

`tests/unit/test_fake_llm.py`·`tests/contracts/`
- [ ] `juror` 역할 fixture 로드, `juror-templates-v1.json` 6칸 모두 1~60자

`tests/integration/test_migrations.py`(DB 있을 때)
- [ ] 001~003·006 적용 뒤 `kind='JURY_VOTE'` INSERT 통과, 004 파일은 건너뜀

### 4.3 동작 확인 가이드 (수동)

```bash
# 1. 가짜 백엔드 + 워커로 한 표
uv run uvicorn tests.fakes.backend_app:app --port 18080 &
BACKEND_INTERNAL_URL=http://127.0.0.1:18080 uv run geoji-ai worker &
uv run scripts/enqueue_job.py JURY_VOTE --post-id P --post-version 1 --room-id R --voter-id BOT
# 워커 로그에 jury_vote_summary outcome=AI, 가짜 백엔드 로그에 201

# 2. 운영 마이그레이션(이미지 배포 전 1회)
DATABASE_URL=<Session Pooler URL> uv run geoji-ai migrate   # 006 적용 확인

# 3. 운영 확인: 사람 1 + 떼거지봇 방에서 글 등록 → 5~10초 뒤 피드에 떼거지봇 표
docker logs geoji-ai-worker | grep jury_vote_summary
```

### 최종 완료 기준
- [ ] §4.2 전부 초록, 게이트 통과
- [ ] 006 운영 적용, 새 이미지 태그 전달(19 §8)
- [ ] 백엔드 19 §0 반영 뒤 운영에서 떼거지봇 표 1건 확인

## 5. 작업 분할 (Task Breakdown — 카드 연동)

| # | 카드명 | 설명 | 라벨 | 예상 | 선행 |
|---|---|---|---|---|---|
| JV-01 | 잡 계약·슬롯·006 | §3.1 | Critical | 0.25d | — |
| JV-02 | juror 역할·출력 계약·fixture | §3.2 | Critical | 0.25d | — |
| JV-03 | 프롬프트 | §3.3 | High | 0.25d | JV-02 |
| JV-04 | 그래프 D + 템플릿 | §3.4 | Critical | 0.5d | JV-02·JV-03 |
| JV-05 | 핸들러·포트·어댑터·가짜 백엔드 | §3.5·§3.6 | Critical | 0.5d | JV-01·JV-04 |
| JV-06 | 설정·README·enqueue 도구 | §3.7 | Medium | 0.25d | JV-01 |

**JV-01** — [ ] `JuryVotePayload`·route / [ ] `SLOT_KINDS`·`WORKER_SLOTS` / [ ] 006 + 러너 상한·건너뛰기 / [ ] 통합 테스트 단언 수정
**JV-02** — [ ] `LLMRole`·`ROLE_VENDOR` / [ ] `JurorVote`·`juror_schema` / [ ] fixture 2개
**JV-03** — [ ] `juror-v1.md` / [ ] `build_juror_system`
**JV-04** — [ ] 노드 5개 / [ ] 템플릿 폴백 / [ ] 로그·지표
**JV-05** — [ ] `JuryVoteHandler` / [ ] `cast_jury_vote` 포트·HTTP / [ ] 가짜 백엔드 라우트·시나리오
**JV-06** — [ ] 설정 3키 / [ ] README·`.env.example` / [ ] `enqueue_job.py`
