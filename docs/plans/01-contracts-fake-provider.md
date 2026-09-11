# 🛠️ [Tech Spec] 기술 명세서: 작업 1 — 계약 · JSON Schema · fake provider · 프로젝트 골격 (9/8~9/9)

> 근거: `docs/proposal/proposal2.md` v2.0(통합 확정본) §6 데이터 계약, §5.1·§5.3 노드 입출력, §12 정책 버전, §13 프롬프트 자산, §14.1 설정, §19 모듈·디렉터리, §20 작업 1 + "먼저 실패시킬 케이스", 부록 A fixture. proposal2 는 git 에 없으므로(`.gitignore` 가 `docs/plans/` 만 추적) 이 문서는 계약을 전부 옮겨 적었다.
> **전제 (9/7 밤 사용자 결정 4건):** ① proposal2 의 실행 구조(공유 Postgres + `ai.jobs` 큐 + 워커 + 백엔드 finalize)를 P0 그대로 — 9/7 저녁의 "동기 응답·직접 호출·페이로드 전부" 는 폐기. **D-20(공유 Postgres) 은 9/8 확정 — Supabase 인스턴스 공유(10 §15.2).** ② 계획서는 §20 작업 묶음 1~8 + P1 로 재편 ③ D-19 는 양형 이유 템플릿 치환 ④ 백엔드 몫은 `10-backend-contract.md` 한 장.
> 이 문서가 끝나면 나머지 작업은 **계약 파일을 바꾸지 않고** 구현한다. 계약을 바꿔야 하면 이 문서로 돌아와 `schema_version` 을 올린다.

## 1. 개요 및 구현 목표

### 목적:
- 모든 후속 작업이 공유하는 **계약을 코드보다 먼저 고정**한다 — `contracts/*.schema.json` 7종, pydantic 미러, fixture(부록 A 의 hell 예시 + 정책 버전별 기대값)
- **fake provider** 로 외부 LLM 없이 상태·정합성을 검증할 수 있게 한다(proposal2 §20 "외부 LLM 호출 전에 fake provider 로 상태·정합성부터 확인"). 일반 CI 는 fake 만 쓴다
- 프로젝트 골격(`src/geoji_ai/`, 설정, 로깅, 헬스)과 **ports 5종**(llm·backend·memory·jobs·ledger)의 시그니처를 잠근다. 의존 방향 `api/workers → application → domain/ports`, `domain` 은 FastAPI·LangGraph·SQLAlchemy·벤더 SDK 에 의존하지 않는다
- 먼저 실패시킬 것(§20 작업 1): **알 수 없는 필드 · hell 누락 · 같은 intensity 중복 · 없는 Evidence 라벨 · EvaluationReport 누락 · 정책 버전 불일치**

### 핵심 플로우:
```
contracts/*.schema.json (정본, schema_version=1)
   ├─ src/geoji_ai/contracts/*.py  (pydantic 미러 — 동등성 테스트로 정본과 묶음)
   ├─ contracts/fixtures/*.json     (hell 예시 · 정책 v1/v2 기대값 · 합성 snapshot/jury/dossier)
   └─ tests/contracts/              (거부 케이스 6종 + fixture 로드)
adapters/fake_llm.py  ─ 역할별 고정 출력 + 장애 시나리오(timeout·429·거부·잘림·JSON 오류·강도 1개 실패)
ports/{llm,backend,memory,jobs,ledger}.py ─ 작업 2~6 이 구현할 인터페이스
```
- 계약 파일 이름·버전은 proposal2 §19 그대로: `intake-v1` · `case-snapshot-v1` · `sentencing-v1` · `writer-draft-v1` · `evaluation-v1` · `finalize-v1` · `verdict-view-v1`
- 모델용 strict 스키마(6 역할)는 계약에서 **파생**한다(§3.3). 서기는 강도 1개짜리 호출이므로 `texts` 가 아니라 `TextDraft` 1개를 낸다

### 현상태
| 항목 | 값 |
|---|---|
| 코드 | `scripts/probe_writer_latency.py` 611줄뿐. 패키지·서버·DB 없음 |
| 실측 | 서기(Grok) 강도별 병렬 3.1초 / p90 3.4초 / 11.7원. OpenAI 4역할은 **미실측**(작업 3) |
| 환경 | uv 0.11.26, CPython 3.14.6 설치됨. 스크립트는 `>=3.11` 인라인 의존성 |
| 저장소 | 이 저장소 = proposal2 §19 의 `services/ai/`. 경로는 저장소 루트 기준으로 쓴다(`src/geoji_ai/`, `contracts/`, `database/migrations/`) |

## 2. 작업 범위 (Scope Boundary)

### In-Scope
| # | 항목 | 등급 | 난이도 |
|---|---|:--:|:--:|
| 3.1 | 프로젝트 골격 — `pyproject.toml`(uv, Python 3.12), `src/geoji_ai/`, 설정·startup validation·로깅·헬스 | High | S |
| 3.2 | JSON Schema 7종 + 공통 규칙(알 수 없는 필드 거부, 길이·배열 제한, `schema_version=1`) | Critical | M |
| 3.3 | pydantic 계약 모듈 + 정본 동등성 테스트 + 모델용 strict 스키마 6종 파생 | Critical | S |
| 3.4 | fixture — 부록 A hell 예시·정책 v1/v2 기대값, 합성 snapshot·jury·dossier·banter | High | S |
| 3.5 | `domain/` 4모듈 — `intensity.py`·`lexicon.py`·`attack_angles.py`·`validation.py`(구조 검증) | High | S |
| 3.6 | ports 5종 + `fake_llm.py` | Critical | S |

### Out-of-Scope
- DB·큐(작업 2), 백엔드 수직 흐름·스텁·luna 실측(작업 3), 근거·메모리(작업 4), 그래프(작업 5), 실제 벤더 어댑터·예산·프롬프트 튜닝(작업 6), intake 라우트(작업 7)
- `validation.py` 의 **텍스트 규칙**(Evidence 없는 사실 문장 삭제, 문장 안 ID, 욕 목록) — 작업 5 §5.3 ⑤. 작업 1 은 구조 검증까지
- `verdict-view-v1` 의 화면 렌더(프론트). 우리는 스키마만 제공

### 다른 파트에 요청 (백엔드·프론트)
| 대상 | 요청 | 기한 |
|---|---|---|
| 팀·백엔드 | ~~D-20~~ **9/8 확정(10 §15.2): Supabase 인스턴스에 `ai` 스키마.** 남은 요청: `ai_api`·`ai_worker` role·grants 생성, Session Pooler 접속 정보 | 9/9 |
| 백엔드 | ~~D-21~~ **9/8 확정(10 §15.2): 프론트 값이 표준** — 강도 `mild/spicy/hell`, 평결 `guilty/notGuilty/agree/disagree/dismissed`, 게시물 `spent/considering`, 형량 `probation/oneDay/life`, 카테고리 11종 고정. **이 문서의 대문자 enum 은 CT-07 에서 전부 치환** | 9/9 |
| 백엔드 | `CaseSnapshot` 을 채울 수 있는지 — `post_version`·`audience_version`·`privacy_versions`·`room_snapshots.rule_version`·`policy{allowed_sentences[{code,rank}], fallback_sentence, reason_required}`·`default_intensity`. 없는 필드는 9/9 까지 회신(`10-backend-contract.md` §4) | 9/9 |
| 프론트 | `verdict-view-v1` 스키마 검토(폴링 응답 형태, `text_version` 규칙) | 9/10 |

### 팀 결정 대기
- 없음 — D-20·D-21·키·결제(AI 파트 개인 계정, 팀 정산)는 9/8 확정(10 §15)
- `GUARDRAIL_POLICY_VERSION` 기본값 — **9/8 확정 `guardrail-v2`**(D-07 팀 비준은 M3 검수 시, 미비준 시 v1). 작업 1 은 두 버전의 fixture 를 모두 만들고 기본값은 설정으로 둔다

## 3. 기술 상세 설계 (Technical Design)

### 3.1 신규 파일 (전부 신규)
| 파일 | 내용 |
|---|---|
| `pyproject.toml` | uv 프로젝트, `requires-python = "==3.12.*"`. 의존성 `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`, `openai>=1.50`, `langgraph`, `sqlalchemy[asyncio]>=2`, `asyncpg`, `httpx`, `structlog`, `jsonschema`. dev `pytest`, `pytest-asyncio`, `ruff` |
| `contracts/{intake,case-snapshot,sentencing,writer-draft,evaluation,finalize,verdict-view}-v1.schema.json` | §3.2 정본 |
| `contracts/fixtures/*.json` | §3.4 |
| `src/geoji_ai/contracts/{intake,case,sentencing,writer,evaluation,finalize,jobs}.py` | pydantic 미러. `model_config = ConfigDict(extra="forbid")` |
| `src/geoji_ai/contracts/llm_schemas.py` | 모델용 strict 스키마 6종 파생(§3.3) |
| `src/geoji_ai/domain/{intensity,lexicon,attack_angles,validation}.py` | §3.5 |
| `src/geoji_ai/ports/{llm,backend,memory,jobs,ledger}.py` | §3.6 Protocol |
| `src/geoji_ai/adapters/fake_llm.py` | §3.6 |
| `src/geoji_ai/core/{config,logging,startup}.py` | §3.7. `startup.validate()` 가 production 에서 정책·모델·키를 검사 |
| `src/geoji_ai/api/app.py`, `api/health.py` | `/health/live` · `/health/ready`(DB 는 작업 2 부터) |
| `tests/contracts/test_reject_cases.py`, `test_fixtures.py`, `test_pydantic_equivalence.py`; `tests/unit/test_{intensity,lexicon,attack_angles,validation,fake_llm}.py` | §4.2 |
| `docker-compose.dev.yml` | `postgres:16`(작업 2 가 쓴다) |

### 3.2 JSON Schema 7종 (`contracts/`, proposal2 §6·§10.1·§9.3)
공통 규칙(§6.1): 모든 객체 `additionalProperties: false`, 문자열 길이·배열 크기 제한, `schema_version` const 1, ID 는 문자열(신규 AI 테이블 UUID, 기존 업무 ID 는 opaque text), 금액 `amount_krw` 양의 정수, 시각 RFC3339 UTC. **계획서에 값이 없는 상한은 안전 상한(9/11 확정)**: 라벨 배열 ≤16·항목 패턴 `^F\d+$`, `statement[].text` 1~300, `texts` 1~3, `meme_hints.emotion` ≤30·`keywords` ≤10(항목 ≤30), `aggravating`·`mitigating` ≤20(항목 ≤100), `violations` ≤20, `Violation.path` ≤200, `problem_sentences` ≤10(항목 ≤300), `room_ids` ≤50, `privacy_versions` ≤100, `room_snapshots` ≤50, `allowed_sentences` 1~3·`rank ≥ 1`, `audience_version ≥ 1`·`epoch ≥ 0`·`rule_version ≥ 0`, `guilty_ratio` 0..1, `draft_hash`·`evaluation_draft_hash` `^[0-9a-f]{64}$`, `poll_after_ms` 0~60000, `VerdictView.view.headline` ≤30·`statement` 1~4·`sentencing_reason` ≤100. 정본 JSON 은 미러에서 생성한다(`tools/gen_contracts.py`, 9/11).

| 스키마 | 최상위 | 핵심 필드 · 제약 |
|---|---|---|
| `case-snapshot-v1` | `CaseSnapshot` | `post_id`, `author_id`, `post_version ≥ 1`, `item ≤ 30 code points`(무엇을, 필수), `reason ≤ 200 code points | null`(사유, 선택), `amount_krw > 0`, `category`, `post_type ∈ spent|considering`, `created_at`, `audience{room_ids unique[], audience_version, public_share_enabled}`, `privacy_versions[{scope_key, epoch}]`, `room_snapshots[{room_id, intensity, rule_version}]`, `intake_result | null`, `jury: JurySnapshot | null` |
| (내포) `JurySnapshot` | | `verdict_id`, `verdict_version ≥ 1`, `result ∈ guilty|notGuilty|agree|disagree`, `vote_counts map<string,int≥0>`, `guilty_ratio`, `confirmed_at`, `deadline_at`, `policy{version, allowed_sentences[{code, rank}], fallback_sentence, reason_required}`, `target_intensities unique[]`, `default_intensity`. **`dismissed` 는 여기 없다** — 각하는 선고 작업 자체를 만들지 않는다 |
| `sentencing-v1` | `SentencingDecision` | `sentence`(코드 `probation|oneDay|life`, 허용 목록과 **동적** 대조 — 스키마는 문자열, 코드가 검사), `sentencing_reason ≤ 100 | null`, `evidence_labels[]`, `aggravating[]`, `mitigating[]` |
| `writer-draft-v1` | `WriterDraft` | `texts: TextDraft[]`(`target_intensities` 와 정확히 일치, 중복 금지), `meme_tag`, `meme_hints{emotion, keywords[]} | null` |
| (내포) `TextDraft` | | `intensity ∈ mild|spicy|hell`, `headline ≤ 30`, `statement[{text, kind ∈ fact|claim|opinion, evidence_labels[]}]`(2~4문장, 합산 ≤ 300), `banter_strategy`(전략 8종), `selected_candidate_id: UUID | null`, `attack_angle`(서버 지정 6종) |
| `evaluation-v1` | `EvaluationReport` | `policy_version ∈ guardrail-v1|guardrail-v2`, `sentence_check{pass, violations[]}`, `sentencing_reason_check{pass, violations[]}`, `texts[{intensity, pass, violations[], problem_sentences[]}]`. `Violation{code, path, evidence_labels[], explanation ≤ 300}` |
| (enum) `Violation.code` | | `PERSONAL_ATTACK` `IDENTITY_DEGRADATION` `SELF_HARM_LEXICON` `UNGROUNDED_CLAIM` `VERDICT_CONTRADICTION` `INJECTION_FOLLOWED` `UNSAFE_CONTENT` `INTENSITY_MISMATCH` `PROFANITY_OUT_OF_LIST` `SENTENCE_REASON_MISMATCH` `SCHEMA_INVALID` |
| `finalize-v1` | `FinalizeRequest` | `schema_version`, `job_id`, `generation_id`, `verdict_version`, `expected_text_version`, `dossier_id`, `privacy_versions[]`, `draft_hash`(canonical draft sha256), `sentencing | null`, `draft: WriterDraft`, `evaluation: EvaluationReport`, `evaluation_draft_hash`, `prompt_bundle_version`, `guardrail_policy_version`, `model_ids{sentencing, writer, evaluator}` |
| `intake-v1` | `IntakeRequest` / `IntakeResult` | 요청 `submission_id`, `payload_hash`, `mode ∈ INITIAL|FINAL_CHECK`, `post_type`, `amount_krw`, `category`, `item`, `reason | null`. 결과 `status ∈ PASS|NEEDS_CLARIFICATION|BLOCKED`, `item_review{status ∈ OK|VAGUE|EXAGGERATED, suggested_item ≤ 30 | null}`, `message ≤ 60 | null`(참고용 — 프론트 솔직 팝업은 고정 문구), `category_review{status ∈ OK|MISMATCH, suggested_category | null, confidence 0..1}`, `injection_detected`, `intake_source ∈ AI|FALLBACK`. **`FINAL_CHECK` 는 `NEEDS_CLARIFICATION` 을 낼 수 없다**(스키마 `if/then`) |
| `verdict-view-v1` | `VerdictView` | `post_id`, `jury_status`, `sentence_status ∈ PENDING|FINAL`, `text_status ∈ PENDING|GENERATING|TEMPLATE_READY|AI_READY`, `text_version ≥ 0`, `view | null {intensity, headline, statement(문장 배열), sentence, sentence_label, sentencing_reason | null, source ∈ AI|TEMPLATE, meme{tag, image_id, image_url}}`, `poll_after_ms` |

전략 8종: `CHEAPER_ALTERNATIVE` `FREE_ALTERNATIVE` `DIY_REPLACEMENT` `PREMISE_REJECTION` `EXCUSE_STRIPPING` `NECESSITY_APPROVAL` `REPEAT_OFFENSE` `ROOM_RULE_CALLBACK`. 짤 태그 5종: `GUILTY_HEAVY` `GUILTY_LIGHT` `NOT_GUILTY` `APPROVED` `REJECTED`(기획서 값 그대로 — 평결 enum 과 별개, 대문자 유지). 공격 각도 6종: `CONVERSION`(환산) `REPETITION`(반복) `EXCUSE_DISSECTION`(변명 해부) `FUTURE_PROPHECY`(미래 예언) `RULE_PERSONIFICATION`(규칙 의인화) `ALTERNATIVE_MOCKERY`(대안 조롱).

### 3.3 모델용 strict 스키마 6종 (`contracts/llm_schemas.py`, 계약에서 파생)
| 역할 | 파생 원본 | 차이 |
|---|---|---|
| 심문관 | `IntakeResult` | `intake_source` 제외(서버가 채움). `FINAL_CHECK` 호출은 `status` enum 을 `PASS|BLOCKED` 로 주입 |
| 조서 | (내부) | `facts[]{kind, text ≤ 500, source_refs[]}` + `reason_analysis{has_mitigation, mitigation_kind, injection_suspected}`. **F-라벨은 코드가 붙인다**(작업 4) |
| 드립 후보 | (내부) | `candidates[]{text, strategy, fits[], evidence_labels[]}` — 강도 1개 호출이라 `intensity` 없음 |
| 양형관 | `SentencingDecision` | `sentence` enum 을 **허용 목록으로 주입** |
| 서기 | `TextDraft` 1개 + `meme_tag` + `meme_hints` | `intensity`·`attack_angle` 은 서버 값으로 enum 고정(모델이 고르지 않는다). `selected_candidate_id` 는 후보 목록 enum 또는 null |
| 검수관 | `EvaluationReport` | `policy_version` 제외(서버가 채움), `texts[].intensity` enum 을 초안 강도 집합으로 주입 |

strict 규칙: 모든 키 `required`, `additionalProperties=false`. enum 주입 함수 `with_enums(schema, **values)` 를 한 곳에 둔다.

### 3.4 fixture (`contracts/fixtures/`, proposal2 부록 A)
| 파일 | 내용 |
|---|---|
| `taxi-hell-input.json` | `{"reason": "늦잠자서 출근할 때 택시 탐 9200", "amount_krw": 9200, "intensity": "hell"}` |
| `taxi-hell-requested-output.json` | 사용자 지정 hell 문구 **원문 그대로 보존**(부록 A.2). 평결·형량·과거 이력을 추가하지 않는다 |
| `taxi-hell-expected-evaluation.guardrail-v1.json` | `pass=false`, `PERSONAL_ATTACK` 1건(`texts[0].statement[0].text`) |
| `taxi-hell-expected-evaluation.guardrail-v2.json` | `pass=true`, 위반 0. **단 `PROFANITY_OUT_OF_LIST` 여부는 비속어 허용 목록 확정 전까지 열어 둔다**(fixture 에 `open_questions` 필드) |
| `case-snapshot-taxi.json` · `jury-guilty-75.json` · `jury-rejected.json` · `jury-not-guilty.json` | 합성 백엔드 fixture. 택시 12,000원·`policy.allowed_sentences=[probation#1, oneDay#2]`·`fallback_sentence=oneDay` 등 |
| `dossier-taxi.json` | `scripts/probe_writer_latency.py:71-103` 의 `CASE.dossier`(F0~F6) + `label_map` |
| `banter-taxi.json` | 같은 스크립트의 후보 4개 + UUID |
| `templates-v1.json` | 결과별 사전 검수 템플릿(headline·statement·sentencing_reason 치환문) — **백엔드 watchdog 과 공유**(`10-backend-contract.md` §10) |

### 3.5 domain 4모듈
| 모듈 | 내용 |
|---|---|
| `intensity.py` | `Intensity` enum(`mild`·`spicy`·`hell`, 9/8 확정 프론트 값) ↔ 표시명(순한맛·매운맛·지옥맛) 매핑 **단일 지점**(D-21). 다른 곳에서 한글 문자열을 쓰지 않는다 |
| `lexicon.py` | `DEATH_WORDS`(자살·자해·죽어·죽고 싶·죽여·뒤져·뒤지·목을 매·손목·극단적 선택), `PROFANITY`(순한맛·매운맛 0개 검사용 — 미친·미쳤·돌았·지랄·새끼·처먹·처타·처박·처발·개같·개무시·씨발·씨빨·ㅅㅂ·병신·ㅂㅅ·존나·ㅈㄴ·좆·꺼져·닥쳐·또라이·등신·멍청), `HELL_ALLOWED_PROFANITY`(미친·돌았냐·정신 나갔냐·실화냐·어이없네·개같은 선택·지랄·꼴·처타다·헛소리·레전드·새끼), `HELL_ONCE_PER_VERDICT`(새끼·ㅋㅋ), `WORN_PHRASES`(정신 차리십시오 등), `ID_IN_TEXT = r"\bF\d+"`. 강도별 적용 표를 함수로(`applies(intensity, rule)`) |
| `attack_angles.py` | 6종 + 마무리 방식 문장(스크립트 `:235-242`). `pick(post_id, offset) = crc32(post_id) % 6 + offset`. 모델이 고르지 않는다 |
| `validation.py` (구조) | `validate_writer_draft(draft, target_intensities, label_map)`: 강도 집합 정확히 일치·중복 없음·길이·라벨 ∈ `label_map`·문장 수 2~4·`kind` enum. `validate_evaluation(report, intensities, policy_version)`: 강도 완전성·검사 필드 완전성·`pass` 불리언·정책 버전 일치. **`false`·누락·파싱 실패는 모두 검수 실패**(proposal2 §5.3 ⑥) |

### 3.6 ports 5종 + fake provider
```python
# ports/llm.py
class LLMPort(Protocol):
    async def structured_call(self, *, role: str, messages: list[dict], schema: dict,
                              timeout_s: float, max_output_tokens: int) -> LLMResult: ...
@dataclass(frozen=True)
class LLMResult:
    output: dict | None; stop_reason: str            # "stop" | "max_tokens" | "refusal"
    usage: Usage                                      # prompt/completion/reasoning/cached tokens
    cost: Cost                                        # ticks | None, micro_usd | None, source: "usage"|"table"|"unknown"
    provider_request_id: str | None; model_id: str; vendor: str; latency_ms: int
class LLMError(Exception): kind: str                  # TIMEOUT | RATE_LIMIT | SERVER | TRANSPORT | REFUSAL | SCHEMA | PARSE

# ports/backend.py  — 10-backend-contract.md §4 의 5 API
class BackendPort(Protocol):
    async def snapshot(self, job_id, generation_id) -> CaseSnapshot
    async def resolve_evidence(self, job_id, generation_id, req: ResolveEvidenceRequest) -> ResolveEvidenceResponse
    async def begin_generation(self, verdict_id, *, job_id, generation_id, verdict_version) -> BeginGenerationResult   # 고정 형량·text_version
    async def finalize(self, verdict_id, req: FinalizeRequest) -> FinalizeResult                                        # 동일 요청 재전송 = 같은 결과
    async def generation_failed(self, verdict_id, *, job_id, generation_id, error_code) -> None

# ports/memory.py  — recall 은 참조만 돌려준다 (proposal2 §16.1)
class MemoryPort(Protocol):
    async def recall_user(self, user_id, category, before, limit) -> list[MemoryCandidate]   # {source_type, source_id, source_version, score}
    async def recall_room(self, room_id, category) -> RoomRecall                              # {rules_hit[], style_example_refs[], strictness}
    async def retain_verdict(self, event_id, verdict_payload) -> int
    async def retain_comment(self, event_id, comment_payload) -> int
    async def delete_user(self, user_id) / delete_room(self, room_id) / delete_post(self, post_id) -> int

# ports/jobs.py  — 작업 2
class JobsPort(Protocol):
    async def claim(self, kinds, worker_id) -> Job | None
    async def heartbeat(self, job_id, worker_id, generation_id) -> bool
    async def complete(self, job_id, worker_id, generation_id) -> bool
    async def fail(self, job_id, worker_id, generation_id, error_code, retry_after_s) -> bool
    async def release(self, job_id, worker_id, generation_id) -> bool

# ports/ledger.py  — 작업 6
class LedgerPort(Protocol):
    async def reserve(self, post_id, call: CallSpec) -> str            # llm_calls.id (RESERVED)
    async def settle(self, call_id, result: LLMResult) -> None         # COMPLETE + actual_cost
    async def mark_unknown(self, call_id, error: LLMError) -> None
    async def get_node_result(self, request_hash, versions) -> dict | None
    async def put_node_result(self, call_id, request_hash, versions, output, expires_at) -> None
```
`adapters/fake_llm.py`: 역할별 고정 출력을 fixture 에서 읽고, `FakeScenario` 로 지연·오류(`TIMEOUT`·`RATE_LIMIT`·`REFUSAL`·`max_tokens` 잘림·JSON 파싱 실패·스키마 불일치)·**강도별 실패**(서기 `hell` 만 실패)·검수관 위반 코드를 주입한다. 호출 기록(`calls[]`)을 남겨 테스트가 호출 횟수·순서를 단언한다.

### 3.7 설정 (`core/config.py`, proposal2 §14.1·§19)
| 필드 | 초기값 | 비고 |
|---|---|---|
| `APP_ENV` | `development` | `development` \| `production`(9/11 확정). production 이면 `startup.validate()` 가 정책 명시를 요구한다. `config.is_production()` 한 곳만 읽는다 |
| `DATABASE_URL`, `BACKEND_INTERNAL_URL`, `SERVICE_AUTH_TOKEN` | — | 비밀값은 환경 주입. 로그·git 기록 금지 |
| `OPENAI_API_KEY`, `XAI_API_KEY`, `XAI_BASE_URL` | — / `https://api.x.ai/v1` | |
| `MODEL_JUDGMENT` · `MODEL_WRITER` · `MODEL_EVALUATOR_HELL` | `gpt-5.6-luna` · `grok-4.20-0309-non-reasoning` · `gpt-5.6-luna` | 서기·드립에 추론 모델(`grok-4.6`·`4.5`·`4.3`) 금지 — startup 오류 |
| `PROMPT_BUNDLE_VERSION` · `GUARDRAIL_POLICY_VERSION` | `bundle-v1` · `guardrail-v2`(제안) | 정책은 프롬프트·검수관·finalize·fixture 에 동시에 걸린다 |
| `INTAKE_TIMEOUT_SECONDS` | 4 | |
| `FIRST_RESULT_TARGET_SECONDS` · `REPAIR_PATH_BUDGET_SECONDS` | 10 · 15 | |
| `SENTENCING_NODE_TIMEOUT_SECONDS` · `WRITER_NODE_TIMEOUT_SECONDS` · `EVALUATOR_NODE_TIMEOUT_SECONDS` | 3 · 6 · 4 | 양형관·검수관은 작업 3 실측 후 조정 |
| `WORKER_POLL_MS` · `JOB_LEASE_SECONDS` · `HEARTBEAT_SECONDS` | 250 · 15 · 5 | 작업 2 |
| `FINALIZE_RESERVE_MS` · `INLINE_CONTEXT_MIN_REMAINING_MS` | 500 · 8500 | 작업 5 |
| `IMMEDIATE_REPAIR_MAX` · `TEXT_RETRY_ROUNDS` · `TEXT_RETRY_TIMEOUT_SECONDS` | 1 · 3 · 20 | |
| `RECALL_CANDIDATE_LIMIT` · `EVIDENCE_PACK_LIMIT` · `STYLE_EXAMPLE_LIMIT` | 20 · 12 · 3 | 작업 4 |
| `MODEL_CONCURRENCY_LIMIT` | 8 | D-22(9/8 확정) |
| `ALERT_DISCORD_WEBHOOK_URL` · `COST_ALERT_KRW_PER_DAY` | — · 5000 | 9/8 확정. 알림 규칙은 08 §3.3, 평가 실행(`GEOJI_EVAL=1`)분은 별도 집계 |
| `MAX_TOTAL_PROMPT_TOKENS` · `WRITER_MAX_PROMPT_TOKENS` | 6000 · 8000 | |
| 출력 토큰 상한 | intake 300 · context 700 · banter 1200 · sentencing 400 · writer 700/강도 · evaluator 800 | 잘리면 스키마 실패로 처리하고 사용량 기록 |
| 기능 플래그 | `ROOM_COMMENT_STYLE_ENABLED=false` · `PUBLIC_HISTORY_CALLBACK_ENABLED=false` · `REFLECT_ENABLED=false` · `HINDSIGHT_ENABLED=false` | hell 을 끄는 플래그는 없다 — 정책 버전으로 통제 |
| `WORKER_SLOTS` | `SENTENCE=2, PREPARE=1, BACKGROUND=1` | 작업 2 |

`startup.validate()`(production): 정책 없는 형량 fallback → 오류(**9/11 확정: "없다" = 환경변수에 `GUARDRAIL_POLICY_VERSION` 을 직접 적지 않고 코드 기본값에 기대는 것. 운영 배포 환경변수 목록에 이 값을 반드시 넣는다. 값은 `guardrail-v2`**), 추론 모델 설정 → 오류, 키 누락 → `/health/ready` 503.

## 4. 완료 기준 (DoD)

### 4.1 정량 목표
| 지표 | 목표 | 측정 |
|---|---|---|
| 거부 케이스 | 6종(알 수 없는 필드·hell 누락·강도 중복·없는 라벨·Report 누락·정책 버전 불일치) 전부 거부 | `tests/contracts/test_reject_cases.py` |
| 정본 동등성 | pydantic 모델이 생성한 JSON Schema 와 `contracts/*.schema.json` 의 `required`·enum·길이 제약 동일 | `test_pydantic_equivalence.py` |
| fixture | 12개 로드·검증 통과, hell 원문 바이트 동일 | `test_fixtures.py` |
| fake provider | 시나리오 8종(정상·timeout·429·거부·잘림·파싱·스키마·강도 1개 실패) 재현 | `test_fake_llm.py` |
| startup | production + 정책 누락 → 기동 실패, `grok-4.6` 서기 → 기동 실패 | `test_startup.py` |

### 4.2 검증 테스트 시나리오
- **`tests/contracts/test_reject_cases.py`**
  - [ ] `WriterDraft` 에 `foo` 필드 → 거부 / `texts` 에 `hell` 누락(target 3개) → 거부 / `spicy` 2개 → 거부
  - [ ] `statement[].evidence_labels=["F9"]`, `label_map` 에 F0~F6 → 거부(`validation.validate_writer_draft`)
  - [ ] `EvaluationReport.texts` 에 강도 하나 빠짐 / `sentence_check` 누락 / `pass="yes"` → 거부
  - [ ] `FinalizeRequest.guardrail_policy_version="guardrail-v1"` 인데 설정 `v2` → 거부
  - [ ] `IntakeResult(mode=FINAL_CHECK, status=NEEDS_CLARIFICATION)` → 거부
- **`tests/unit/test_attack_angles.py`**: 같은 `post_id` → 같은 각도, offset +1 → 다음 각도, 6종 순환
- **`tests/unit/test_lexicon.py`**: 강도별 적용 표(`spicy` 에 `PROFANITY` 검사 on, `hell` 은 허용 목록 검사), `ID_IN_TEXT` 매칭
- **`tests/unit/test_intensity.py`**: enum ↔ 표시명 왕복, 알 수 없는 값 거부
- **`tests/unit/test_fake_llm.py`**: 시나리오 8종 + `calls[]` 기록

### 4.3 동작 확인 가이드 (수동)
```bash
uv sync && uv run pytest tests/contracts tests/unit -q
uv run python -c "from geoji_ai.contracts.llm_schemas import writer_schema; import json; print(json.dumps(writer_schema(['spicy'], ['CONVERSION']), ensure_ascii=False)[:400])"
uv run uvicorn geoji_ai.api.app:app --port 8100 & curl -s localhost:8100/health/live
```

### 최종 완료 기준:
- [ ] `contracts/*.schema.json` 7종 + fixture 12개 커밋, 거부 케이스 6종 green(proposal2 §20 작업 1 완료 기준 "스키마 거부 케이스 통과")
- [ ] ports 5종 시그니처 고정, `fake_llm.py` 로 작업 5 가 그래프를 돌릴 수 있음
- [x] D-20 회신 기록(`00-INDEX.md` §8.4, 10 §15) — 9/8 확정
- [ ] README 에 실행 절차·설정 표

## 5. 작업 분할 (Task Breakdown — 카드 연동)

| # | 카드명 | 설명 | 라벨 | 예상 | 선행 |
|---|---|---|---|:--:|---|
| CT-01 | 골격·설정·startup | `pyproject`, `src/geoji_ai`, config 표, `startup.validate`, 로깅, `/health/live` | infra | 0.5d | — |
| CT-02 | JSON Schema 7종 | §3.2 정본 + 공통 규칙 + 예시 | contract | 0.5d | — |
| CT-03 | pydantic 미러·동등성·strict 파생 | 7 모듈 + `llm_schemas.py` + `with_enums` | contract | 0.5d | CT-02 |
| CT-04 | fixture 12개 | 부록 A 4개 + 합성 8개 + `templates-v1.json` | contract | 0.25d | CT-02 |
| CT-05 | domain 4모듈 | intensity·lexicon·attack_angles·validation(구조) + 테스트 | domain | 0.5d | CT-03 |
| CT-06 | ports·fake provider | Protocol 5종, `fake_llm.py` 시나리오 8종 | ports | 0.5d | CT-03 |
| CT-07 | 백엔드 회신 반영 | ~~D-20·D-21·enum 매핑~~(9/8 완료 — 이 문서의 enum 을 프론트 값으로 치환) · CaseSnapshot 필드 회신을 계약에 반영, `schema_version` 유지 | contract | 0.25d | 백엔드 9/9 |

**CT-01** — [ ] `uv init`·의존성 / [ ] `config.py` §3.7 / [ ] `startup.validate` 3검사 / [ ] structlog + `trace_id` / [ ] `/health/live`
**CT-02** — [ ] 7 파일 / [ ] `additionalProperties:false`·길이·enum / [ ] `IntakeResult` `if/then` / [ ] 거부 케이스 6종 테스트
**CT-03** — [ ] `extra="forbid"` 모델 7종 / [ ] 동등성 테스트 / [ ] strict 6종 + enum 주입
**CT-04** — [ ] 부록 A 원문 보존 / [ ] v1·v2 기대값(`open_questions`) / [ ] 합성 8개 / [ ] 템플릿 JSON
**CT-05** — [ ] `intensity` / [ ] `lexicon` 목록·적용 표 / [ ] `attack_angles` / [ ] `validation` 구조 2함수
**CT-06** — [ ] Protocol 5종·데이터클래스 / [ ] fake 시나리오·`calls[]`
**CT-07** — [ ] 회신 반영 / [ ] INDEX §8.4 기록
