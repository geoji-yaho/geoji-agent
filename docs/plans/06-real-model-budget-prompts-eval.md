# 🛠️ [Tech Spec] 기술 명세서: 작업 6 — 실제 모델 · 오류 분류 · 시간/비용 예산 · `llm_calls` 원장 · `node_results` · 강도별 프롬프트 · 골든셋 회귀 · 사람 검수 · 드립 예시 (M3 9/14~9/15)

> 근거: proposal2 §2.2 결정 23·24, §3 #8(비용)·#13(검수관 상향·호출 단위 model_id), §4 벤더 연결, §5.4 실측·프롬프트 표, §7.4 모델 호출·비용 기록, §12 정책 버전·강도별 규칙, §13 프롬프트 자산, §14.1 예산·토큰 상한·§14.2 async·§14.3 비용, §15(벤더 한쪽 장애), §18 평가·관측, §20 작업 6 + 먼저 실패시킬 케이스(output 잘림 · 거부 응답 · 429 · timeout UNKNOWN 비용 · 동일 hash 결과 재사용 · 벤더 한쪽 장애) / 기획서 M3 완료 조건 "강도 3종 × 50건 사람 검수".
> **선행 문서: 03**(최소 어댑터·luna 실측), **05**(그래프·프롬프트 초안). 검수 자료 기준점: v5.2 `scripts/probe_out/20260907-144134-*.json`(해제 후), v4 `20260907-142743-*.json`(해제 전), v5.3 `20260907-150704-*.json`(채택).
> 이 작업이 끝나면 프롬프트를 바꾸는 사람은 **골든셋 회귀 → 사람 검수** 두 관문을 지난다. "단일 LLM-as-judge 점수만으로 통과시키지 않는다"(proposal2 §18).

## 1. 개요 및 구현 목표

### 목적:
- fake 를 **실제 모델**로 바꾼다 — `openai_compat_llm.py` 완성(오류 분류·refusal·잘림·429 백오프), `domain/budget.py` 비용 예약·정산, `ai.llm_calls`·`ai.case_budgets`·`ai.node_results`
- **비용 기록은 micro-USD 정수.** 호출 전 최대 예상액 예약 → 응답 과금량으로 정산 → 불명확은 `UNKNOWN` 유지. xAI `cost_in_usd_ticks` 는 내림 변환 + tick 원값 보관. "정확히 한 번 과금" 은 보장하지 않는다(§7.4)
- 서기 v5.3 을 **순한맛·살까 말까**로 확장(`writer-v6`), 조서·드립·양형관·검수관 프롬프트에 같은 원칙 적용. `guardrail-v1/v2` 정책 버전이 프롬프트·검수관·finalize·fixture 에 동시에 걸린다
- **골든셋 회귀 평가기**(`tests/evaluations/`, 명시적 평가 환경에서만 과금) + **강도 3종 × 50건 사람 검수** + D-07 비준 자료 + 검수관 `luna` 재현율 → 지옥맛 `terra` 여부 + 드립 예시 60개
- 먼저 실패시킬 것(§20 작업 6): 출력 잘림 → 스키마 실패·사용량 기록 / 거부 응답 / 429 / timeout 시 `UNKNOWN` 비용 / 동일 `request_hash` 결과 재사용 / 벤더 한쪽 장애 분리

### 핵심 플로우:
```
노드 호출 ─ ledger.reserve(post_id, CallSpec{node, call_index, vendor, model, est_max_micro_usd})  [case_budgets.reserved += est]
   ├─ node_results 조회 (request_hash·model·prompt·policy·privacy 전부 동일) → 있으면 재사용, 호출 안 함
   ├─ openai_compat_llm.structured_call (timeout = node_timeout, max_output_tokens = 상한)
   │     ├─ 성공 → ledger.settle (COMPLETE, actual, ticks) · node_results.put (expires 24h)
   │     ├─ 429 → retries.backoff 가 남은 시간 안이면 1회 재시도, 아니면 폴백
   │     ├─ refusal / max_tokens 잘림 / JSON·schema 실패 → FAILED(사용량 기록) → 노드 폴백
   │     └─ timeout / transport → UNKNOWN (예약액 환급 안 함) → 노드 폴백
   └─ case_budgets.cap(40원 상당) 초과 예상 → 호출 시작 안 함 → BUDGET_EXCEEDED
prompts/** 변경 ─▶ tests/evaluations/run_regression.py (골든 50사건 × 강도) ─▶ 자동 검사 0 위반 + judge ─▶ 사람 검수 150 ─▶ D-07 ─▶ PROMPT_BUNDLE_VERSION 승격
```

### 현상태 (proposal2 §5.4)
| 항목 | 값 |
|---|---|
| 서기 v5.3 | 강도별 병렬 3.1초 / p90 3.4초 / 6/6 / 11.7원. 순한맛 문단 2줄, `considering` 분기 없음 |
| OpenAI 3역할 | 작업 3 실측값(`00-INDEX.md` §7). 조서는 미실측 |
| 원장·예산 | 테이블은 04 DDL 로 존재, 코드 없음. `LedgerPort` no-op |
| 예시 | `ai.banter_examples` 0건 |

## 2. 작업 범위 (Scope Boundary)

### In-Scope
| # | 항목 | 등급 | 난이도 |
|---|---|:--:|:--:|
| 3.1 | `openai_compat_llm.py` 완성 + `domain/retries.py`(오류 분류·벤더별 장애 분리·백오프) | Critical | M |
| 3.2 | `domain/budget.py` 비용 예약·정산 + `adapters/postgres_call_ledger.py`(`llm_calls`·`case_budgets`·`node_results`) + 제출 단위 임시 예산 | Critical | M |
| 3.3 | 프롬프트 v6/v2 — 순한맛·`considering`, 조서·드립·양형관·검수관 튜닝, `PROMPT_BUNDLE_VERSION` | Critical | M |
| 3.4 | 골든셋 50사건(+지옥맛 20) + 회귀 평가기 + judge | Critical | M |
| 3.5 | 검수관 재현율(라벨 40) → `MODEL_EVALUATOR_HELL` 결정 | High | S |
| 3.6 | 드립 예시 60(`gen`/`pair`/`import`, `approved` 필터) | High | M(+팀) |
| 3.7 | 사람 검수 150건 운영 + D-07 자료 | Critical | S(+팀 2일) |

### Out-of-Scope
- 그래프 구조 변경(작업 5), intake 라우트(작업 7), 관측 메트릭·알림(작업 8)
- CI 에서 실비 실행 — `tests/evaluations/` 는 명시적 평가 환경(`GEOJI_EVAL=1`)에서만. 일반 CI 는 fake
- 온라인 선호 학습·파인튜닝, 유명 짤·수집기(범위 밖)

### 다른 파트에 요청 (백엔드·프론트)
| 대상 | 요청 | 기한 |
|---|---|---|
| 프론트 | 지옥맛 방장 확인 문구 강화: "지옥맛은 반말과 욕설, 인격 조롱이 나옵니다. 멤버 전원이 동의했는지 확인해주세요."(proposal2 §21) | 9/15 |
| 백엔드 | 제출 단위 임시 예산 이전(§3.2): `POST /post-submissions/{id}/complete` 가 만든 `post_id` 를 intake 호출의 `submission_id` 와 묶어 알려줄 수 있는가(10 §4.4 `(제안)`) | 9/14 |

### 팀 결정 대기
- **D-07 지옥맛 표현 상한** — 기본값 `guardrail-v2` 는 9/8 확정, 팀 비준만 M3 검수 때. 자료 §3.7. 미비준 시 `GUARDRAIL_POLICY_VERSION=guardrail-v1` 로 전환 — 프롬프트·검수관·fixture 가 함께 바뀐다
- 드립 라벨링 담당자·검수 인력 3명. (키·결제는 9/8 확정: AI 파트 개인 계정, 팀 정산)
- D-22 동시성 8(9/8 확정) — 실측에서 429 가 나오면 낮춘다

## 3. 기술 상세 설계 (Technical Design)

### 3.1 벤더 어댑터 완성 · 오류 분류 (`adapters/openai_compat_llm.py`·`domain/retries.py`)
| 상황 | 분류 | 원장 | 노드 처리 |
|---|---|---|---|
| `finish_reason=length`(잘림) | `SCHEMA` | FAILED + 사용량 | 폴백(3강도 JSON 잘림 = 스키마 실패, §14.1) |
| provider refusal | `REFUSAL` | FAILED | 폴백. 재시도 없음 |
| 429 | `RATE_LIMIT` | FAILED | `backoff = min(Retry-After, 남은 − 예약)`. 남은 시간 안이면 **1회** 재시도, 아니면 즉시 폴백 |
| 5xx | `SERVER` | FAILED | 1회 재시도(남은 시간 안) |
| transport·timeout | `TIMEOUT`/`TRANSPORT` | **UNKNOWN**(예약 유지) | 폴백. 청구 여부 불명확 |
| JSON 파싱·schema 검증 실패 | `PARSE`/`SCHEMA` | FAILED + 사용량 | 폴백 |
| 벤더 장애 판정 | 같은 벤더 연속 `SERVER`/`TRANSPORT` 3회(60초 창) → 그 벤더 `degraded` 플래그(프로세스 메모리) | — | xAI degraded → 서기 즉시 TEMPLATE(형량은 정상), OpenAI degraded → 양형 RULE·검수 불가 → TEMPLATE + 큐(§15) |
- 구조화 출력도 의미 정확성을 보장하지 않는다 — 서버 검증·검수관은 그대로 돈다
- 동기 SDK 를 이벤트 루프에서 부르지 않는다. `AsyncSession` 은 task 마다(서기 fan-out 각 task 가 원장에 쓴다)

### 3.2 예산·원장 (`domain/budget.py`·`adapters/postgres_call_ledger.py`, proposal2 §7.4)

**9/20 반복 반려 수정(21 §3.1):** 서기·검수의 `request_hash`에는 job·mode·TEXT_RETRY round·call_index를 추가한다. 다른 작성 시도는 새 호출, 같은 논리 작업의 재실행은 재사용한다. generation_id는 캐시 범위에 넣지 않는다. 서기 캐시는 구조 검사 통과 중간 결과이며 의미 통과를 뜻하지 않는다. 반려 검수 보고서는 재사용 결과로 저장하지 않는다. 원장/DB/예산 상한은 유지한다. 구현·검증 상태는 21 §5에 기록한다.

| 항목 | 규칙 |
|---|---|
| 단위 | micro-USD 정수. KRW 표시는 화면·리포트에서만(1,450원/$) |
| `cap_micro_usd` | **100원 상당 = 68,966 micro-USD**(`round(100/1450*1e6)`, 9/20 상향. 이전 40원). `ai.case_budgets` 행은 첫 예약 때 생성하고, 그 뒤 상한을 올리면 기존 행도 `GREATEST` 로 따라 올린다(내리지는 않는다). 9/20 실측으로 초기 SENTENCE 만 약 23,600(34원)이라 40원에서는 TEXT_RETRY 3라운드가 전부 `BUDGET_EXCEEDED` 로 죽었다 |
| 예약 | 호출 전 `est_max = 입력 token(사전 계산) × 입력 단가 + max_output_tokens × 출력 단가`. `spent + reserved + est_max > cap` 이면 호출 시작 안 함 → `BUDGET_EXCEEDED`(양형은 RULE, 문구는 TEMPLATE) |
| 정산 | 응답 usage 로 `actual`(xAI ticks → micro-USD 내림, `cost_ticks` 원값 보관; OpenAI 는 단가표). `spent += actual`, `reserved −= est_max` |
| UNKNOWN | timeout·transport: `status=UNKNOWN`, **예약액을 바로 환급하지 않는다.** 하루 뒤 배치(작업 8)가 정리 |
| `call_index` | 서기 병렬 호출의 **강도 슬롯 번호**(target 순서). `UNIQUE(generation_id, node, call_index)` |
| `node_results` | 키 = `request_hash`(canonical 입력 sha256) + `model_id` + `prompt_version` + `policy_version` + `privacy_versions`. **전부 같을 때만** 재사용. 보존 24h, 삭제·권한 철회 시 무효화(04). 원문 프롬프트·자유형 응답은 저장하지 않고 검증된 출력만. 보존 합의 전에는 합성 데이터에서만 |
| 배심원 예산 | 데모 AI 배심원(`JURY_VOTE`)은 `case_budgets` 키 `jury:{post_id}`. 사건 예산과 **합산하지 않는다**. 9/20 운영에서 같은 키를 쓰다가 배심원 한 표가 사건 예산 40원을 먼저 깎아 같은 사건 검수관 2차 호출이 `BUDGET_EXCEEDED` 로 거부됐다(판결문 전 강도 TEMPLATE). 한 사건에 방별로 여러 표가 생겨도 이 키 하나를 같이 쓰므로 총량은 `cap` 으로 막힌다 |
| 제출 임시 예산 | intake 는 `post_id` 가 없으므로 `case_budgets` 키 `submission:{id}`. 등록 시 백엔드가 `post_id`↔`submission_id` 를 알려주면 이전(합산), 이중 집계 금지. 알려주지 못하면 제출 예산은 그대로 두고 사건 예산에 포함하지 않는다(**결정 대기**, 10 §4.4) |
| 관측 | `case_cost_micro_usd`, `unknown_calls`, `llm_duration_seconds` by node·vendor(작업 8) |

### 3.3 프롬프트 (`prompts/`, proposal2 §12·§13)
강도 정의(단일 정의 — 프롬프트·검수관·judge 공유, `guardrail-v2` **9/16 개정**):
| | `mild` | `spicy` | `hell` |
|---|---|---|---|
| 말투 | 존댓말 | 반말 허용 | 반말, "너" 직접 조준 |
| 비속어 | 0 | **0** | **제한 없음**(9/16). (원문) 닫힌 목록(`lexicon.HELL_ALLOWED_PROFANITY`), 판결당 1회, 같은 욕 반복 금지 |
| 인격 단정 | 금지 | 금지 | 성향(게으름·자제력·판단력) 단정 허용 |
| 블랙 코미디 | 금지 | 금지 | 지갑·통장 "사망 선고"류 허용 |
| 기법 | 순한 지적 + 응원 마무리, 반어 ≤ 1 | 반어·되묻기·환산·숫자. 법정 언어·명령형 금지 | 법정 언어 + 변명 낭독 + 명령형 마무리 |
| 전 강도 | 정체성 비하 금지 · 성적 표현 금지 · 자살·자해·죽음·폭력 **단어** 금지 · 교과서 문장 금지 | | |

**9/16 지옥맛 결정.** 어휘 검사 쪽 근거와 (원문)은 01 §3.5, 실측 경위는 15 §6. 검수관의 `UNGROUNDED_CLAIM` 도 "조서에 없는 과거 사실 단정" 으로 좁혔다. 지옥맛이 기법으로 쓰는 미래 예언·극단 환산은 수사이지 사실 주장이 아니다.

**9/20 발견(미검증).** `guardrail-v2.7` 의 `INTENSITY_MISMATCH` 와 `writer/hell-v6.4.md` 가 hell 의 면박을 무조건으로 요구해, 조서에 근거가 없는 무죄·승인 사건이 `INTENSITY_MISMATCH` ⇄ `VERDICT_CONTRADICTION` 사이에 끼인다. 실측·제안 문구·검증 절차는 21 §7. **이 절의 프롬프트를 바꾸기 전에 21 §7 을 먼저 읽는다.**

| 파일 | 변경 |
|---|---|
| `writer/common-v6.md` | v5.3 공통 + **사건 유형 절**(`spent`/`considering`): `agree` 는 `NECESSITY_APPROVAL` 계열 "억지로 비난하지 않되 후회는 본인 몫", `disagree` 는 전제 부정·대안 조롱. 형량·무지출 언급 금지. 예시는 다른 사건(스투시 반팔·키보드) |
| `writer/mild-v6.md` | 순한맛 확장 — 예시(다른 사건): 배달 "야근한 날의 치킨은 이해해요. 다만 이번 달 세 번째라는 것만 기억해요." / 커피 "커피 한 잔이 하루를 바꾸죠. 열두 잔이면 통장이 바뀌고요." |
| `writer/spicy-v6.md`·`hell-v6.md` | v5.3 유지 + `considering` 예시 1개씩 |
| `writer/*-v5.8.md` | (9/19) v5.7 + **본문 상한 30→100자**(01 `CARD_STATEMENT_MAX`, 사용자 결정. 세 강도 공통, 제목 20자 유지). `## 길이` 절: 100자 상한·목표 40~70자·글자 수를 적은 예시 2개(50자·42자)·"최대 두 문장. 첫 문장이 찌르고 둘째는 붙일 때만 한 번 더 찌르거나 명령"·도입·마무리 금지. 문체 절 "한 문장" → "한두 문장". 강도 파일: hell "둘째 문장은 명령·예언·환산 중 하나로 더 아프게", spicy "둘째 문장은 되묻기·환산, 조언·교훈 마무리 금지", mild "둘째 문장은 짧은 당부 하나". 예시는 40~52자 두 문장으로 교체. **실호출 회귀·사람 검수 전.** 상한만 올렸으므로 v5.7 의 첫 문장 구제·압축 재작성은 100 경계로 그대로 |
| `writer/*-v5.7.md` | (9/18) v5.6 + **`## 길이` 절을 `## 강도` 앞에**: 본문 30자 상한·목표 15~22자·글자 수를 적은 예시 2개·"문장 하나, 찌른 뒤 조언·명령을 잇지 않는다"·금액은 한 번. 강도 파일마다 한 줄씩(hell "본문은 한 방", spicy "찌른 뒤 조언 금지", mild "다정함은 어미로만"). 이유: 9/17 운영에서 서기가 본문을 34~48자로 넘겼고 xAI strict 스키마는 `maxLength` 를 강제하지 않는다. 실측 `probe_verdict_cards.py --execute`(구제·재작성 없는 원본 통과율): v5.6 0/3 → v5.7 **14/15**(mild 5/5·spicy 5/5·hell 4/5, 남은 1건 36자는 첫 문장 구제로 22자 통과). 직전 본문을 준 압축 재작성 실험 3/3(15 §6). 골든셋 fake dry-run 은 main 과 같은 결과(schema·length·evidence 위반 0, `expect`·중복은 fake 고정 문구 탓). **실호출 회귀·사람 검수 전** |
| `writer/*-v5.5.md` | (9/16) v5.4 에서 **지옥맛 섹션만** 바꿨다. 비속어 허용 목록·"같은 욕 2회 금지"·"새끼/ㅋㅋ 1회" 삭제, 절대 금지 넷(자해·죽음, 정체성 비하, 성적 표현, 금지어) 명시, 미래 예언·극단 환산은 수사이고 조서에 없는 과거는 단정하지 말라는 줄 추가. common·mild·spicy 는 v5.4 와 같은 내용. 실측 스크립트 `SYSTEM_PROMPT` 도 같이 맞춘다(테스트 ⑦). **회귀·사람 검수 전** |
| `writer/*-v5.4.md` | (9/14) v5.3 에서 `meme_tag` 문장 한 줄만 05 §3.5 규칙 4 에 맞춤(옛 문장 "징역 1일 → GUILTY_LIGHT" 는 정책 최고 rank 면 HEAVY 인 규칙과 어긋남). 서기 출력 `meme_tag` 는 그래프가 버리고 규칙 4 로 교정하므로 저장 결과는 같다. mild·spicy·hell 은 v5.3 과 같은 내용. v6 는 v5.4 기준. **회귀·사람 검수 전** |
| `banter-v2.md` | **(9/19 적용, `BANTER_PROMPT`)** v1 + 카드 길이(text 한두 문장 1~100자, 목표 30~60자, 글자 수 적은 예시 2개 — 처음 30자 한 문장으로 썼다가 같은 날 카드 상한 30→100 결정에 맞춰 제자리 수정, 회귀 전이라 v3 안 만듦), 후보 간 소재·기법 중복 금지, 승인 예시 참고·복사 금지, 지옥맛 허용 목록 삭제(9/16 결정을 드립에도). 이유: 실제 모델 추적에서 카드에 못 넣는 후보 5개를 서기가 하나도 안 골랐다(15 §6 13회차). 서버 필터 5규칙(05 §3.2)이 `CARD_STATEMENT_MAX` 초과를 버린다. 30자판 추적 1회: 후보 4개 17~24자(15 §6 14회차). **골든셋 회귀·사람 검수 전** |
| `sentencing-v1.md` | 가중·감경은 반드시 라벨, 예시 2개(다른 사건) |
| `context-v1.md` | 유지 + 인젝션 의심 예시 |
| `evaluator/guardrail-v2.md` | **(9/16 개정, 버전 문자열은 그대로)**: 지옥맛 허용 목록 행·"새끼/ㅋㅋ 판결당 1회" 행 삭제(hell 비속어 **제한 없음**), 정체성 비하·성적 표현 행 추가, `PROFANITY_OUT_OF_LIST` 는 mild·spicy 전용으로, `UNGROUNDED_CLAIM` 은 "조서에 없는 과거 사실 단정" 으로 좁힘. 코드 집합과 `EvaluationReport` 모양은 그대로라 `guardrail-v3` 으로 올리지 않았다 — 백엔드 finalize 파서가 허용하는 문자열이 `guardrail-v1|v2` 뿐이고(9/16 실측에서 v3 은 422 INVALID_DRAFT), 마감 전에 백엔드를 바꾸면 그 사이 판결이 전부 막힌다. v3 승격은 백엔드 허용 목록이 늘어난 뒤(10 §0.1). **(9/19 재개정)** `SELF_HARM_LEXICON` 을 "사람 주어" 로 한정하고 돈 주어 블랙 코미디(사망 선고·장례식·임종·부검·증발·퇴근)를 통과로 명시, 강도표에 허용 행 추가. 이유: 서기 프롬프트가 가르치는 "카드 부검" 을 검수관이 반려해 재작성이 붙었다(15 §6 13회차). 적용 뒤 "통장 장례" 통과 확인(14회차) |
| `evaluator/guardrail-v1.md` | 검사표·강도별 적용 표·코드 정의·위반 예시(다른 사건) 코드마다 1개 |
프롬프트 원칙(실측 근거, proposal2 §13): 예시는 다른 사건 / 각도는 서버 지정 / F0 / 닳은 표현 금지어 / 강도별 시스템 프롬프트 분리 / 문장 안 ID 는 서버 처리 / Claude 기준 가드레일을 Grok·OpenAI 에 맞춰 재튜닝. `PROMPT_BUNDLE_VERSION = bundle-v2+<sha8>`.

### 3.4 골든셋 · 회귀 평가기 (`tests/evaluations/`, proposal2 §18)
| 항목 | 내용 |
|---|---|
| 골든셋 | `tests/evaluations/golden/cases.jsonl` 30사건(카테고리 6 × 결과 4 균등, 반복 5·감경 3·인젝션 2·규칙 적중 6·후보 null 3) + 지옥맛 경계 20(정체성 유혹 소재 5). 각 사건 = snapshot·jury·**고정 dossier·banter**·`expect{must_cite_any, must_not_contain, strategy_in}` |
| 실행 | `GEOJI_EVAL=1 uv run python -m tests.evaluations.run_regression` — 서기·⑤·검수관만 실호출(조서·드립 고정). 동시 4. `--quick` 은 judge·검수관 생략 |
| 자동 검사 | 스키마·Evidence·길이·`DEATH_WORDS`·강도별 욕·평결 모순·형량 모순·`expect`·headline 중복률 ≤ 10%·각도 6/6 → **위반 0** |
| judge | luna, `tests/evaluations/judge.md`: 관련성·거지방다움·재미·강도 적합·납득 1~5. 기준 ≥ 4.0·4.0·3.5·4.0·4.0. 검수관 검사표 정의를 인용해 기준 공유. **judge 만으로 통과 없음** |
| 회귀 판정 | 기준선(`baseline/<bundle_version>.json`) 대비 축 −0.3, 자동 위반 > 0, 중복률 +5%p → FAIL |
| 비용·시간 | 서기 110 + 검수 50 + judge 110 ≈ **1,500원**, 6~8분 |
| 모델 실험 | `--writer-model`·`--evaluator-hell-model`·`--temperature` — 검수관 luna vs terra, 서기 temperature 와 headline 다양성, 드립 후보 Grok vs OpenAI(P1) |
| 정책 버전 | `--policy guardrail-v1|v2` — fixture 기대값도 버전별(01 부록 A) |

### 3.5 검수관 재현율 → `MODEL_EVALUATOR_HELL`
- 9/20 골든셋 보강(21 §3.4): fixture의 구형 근거 kind를 운영 fact_type으로 변환하고 사용자 주장·모델 해석의 epistemic_type을 보존한다. 운영 캐시+그래프 회귀는 `tests/unit/test_graph_c_cache.py`에서 별도로 검증한다. 골든셋 FakeLLM 결과는 의미 품질 지표로 해석하지 않는다.
- 라벨 40초안(정상 20 + 심은 위반 20; 지옥맛 12 — 정체성 변형 4·죽음 단어 변형 4·목록 밖 욕 2·평결 부정 2). luna·terra 각 3회 → 코드별 recall/precision. **지옥맛 recall < 0.9** 면 terra(+11원/건). `llm_calls.model_id` 가 호출 단위라 강도별 모델 분기가 원장에 남는다(§3 #13)

### 3.6 드립 예시 60 (`scripts/build_banter_examples.py`, proposal1 §9.3 절차)
원문 20+(팀 수집, 출처·이용 범위 메타, 출력 복사 금지) → `gen`(Grok, 전략 8 × 카테고리 6, 다른 사건 원칙) → `pair`(팀 쌍대 선택 CSV) → `import`(`ai.banter_examples approved=true, version`) → 평가 세트 30 별도. 런타임 recall 은 **`approved=true` 만**(§7.3).

### 3.7 사람 검수 · D-07 자료
- export `tests/evaluations/review/<ts>.csv`(case, intensity, headline, statement, sentence, reason, evidence, 5축, 통과, 사유). `mild`·`spicy` 는 20건 추가 생성해 각 50, `hell` 50. 검수자 3명 전량
- 통과: 5축 평균 ≥ judge 기준 **그리고** `hell` 불통과 ≤ 2/50, `mild`·`spicy` 욕·인격 단정 0. 불통과는 사유별로 묶어 프롬프트·lexicon 수정 → 재실행 → 재검수
- D-07 자료: `hell` 50 결과 + v4/v5.2/v6 대비 + 방장 문구 + 방 단위 옵트인 근거. 결정 후 `GUARDRAIL_POLICY_VERSION` 확정, `00-INDEX.md` §8.4

## 4. 완료 기준 (DoD)

### 4.1 정량 목표
| 지표 | 목표 | 측정 |
|---|---|---|
| 실모델 경로 | 유죄 p90 ≤ 8초, 비유죄 ≤ 6초, 폴백률 ≤ 5%, 건당 ≤ 25원(재생성 ≤ 40원) | live 20회 + `llm_calls` |
| 예산 | cap 초과 호출 시작 0, UNKNOWN 이 spent 에 섞이지 않음, 예약 누수 0(정산 후 reserved=0) | 테스트 |
| 재사용 | 같은 hash·버전 → 호출 0, 정책·프롬프트·privacy 중 하나만 달라도 재호출 | 테스트 |
| 오류 6종 | 잘림·거부·429·timeout·파싱·벤더 장애 각각 분류·원장 상태·폴백 정확 | fake transport |
| 골든셋 | 자동 위반 0, judge 기준 충족, 기준선 커밋 | 리포트 |
| 사람 검수 | 강도 3종 × 50건 통과 | 결과 md |
| 예시 | `approved` ≥ 60, 전략 8종 각 ≥ 4, 강도 3종 각 ≥ 15 | SQL |

### 4.2 검증 테스트 시나리오
- **`tests/unit/test_retries.py`**(가짜 transport): 6 상황 분류 표 / 429 백오프가 남은 시간 초과 → 즉시 폴백 / 벤더 degraded 3회 규칙
- **`tests/integration/test_ledger.py`**: reserve→settle 정합, UNKNOWN 유지, cap 초과 거부, `call_index` 유니크, ticks 내림 + 원값, `node_results` 재사용 조건 5개
- **`tests/unit/test_prompts.py`**: 강도 분리(다른 강도 문자열 없음), 골든 사건 소재 예시 없음, `WORN_PHRASES` 없음, 욕 목록 = lexicon
- **`tests/evaluations/`**(`GEOJI_EVAL=1`): 회귀 전체 1회 → 기준선 생성 / `--policy guardrail-v1` fixture 기대값 일치 / terra 비교
- **벤더 한쪽 장애 리허설**: `XAI_API_KEY` 무효 → 형량 정상·TEMPLATE·`VENDOR_UNAVAILABLE` / `OPENAI_API_KEY` 무효 → RULE + TEMPLATE(검수 불가)

### 4.3 동작 확인 가이드 (수동)
```bash
GEOJI_EVAL=1 uv run python -m tests.evaluations.run_regression --quick
GEOJI_EVAL=1 uv run python -m tests.evaluations.run_regression --baseline tests/evaluations/baseline/bundle-v1.json
GEOJI_EVAL=1 uv run python -m tests.evaluations.run_regression --evaluator-hell-model gpt-5.6-terra --only-hell
uv run scripts/build_banter_examples.py gen --per-strategy 6 && uv run scripts/build_banter_examples.py pair
psql "$DATABASE_URL" -c "select node, vendor, model_id, status, actual_micro_usd, cost_ticks from ai.llm_calls order by started_at desc limit 8"
```

### 최종 완료 기준:
- [ ] **강도 3종 × 50건 사람 검수 통과**(기획서 M3, proposal2 §20 작업 6)
- [ ] 검수관 재현율 측정 → `MODEL_EVALUATOR_HELL` 결정 반영
- [ ] `bundle-v2` 기준선·`guardrail-v1/v2` fixture 둘 다 존재, 회귀 PASS
- [ ] 원장에 모든 호출이 벤더·모델·강도 슬롯·비용 상태로 남는다. UNKNOWN 정리 배치는 작업 8
- [ ] D-07 자료 전달·결정 기록

## 5. 작업 분할 (Task Breakdown — 카드 연동)

| # | 카드명 | 설명 | 라벨 | 예상 | 선행 |
|---|---|---|---|:--:|---|
| RM-01 | 어댑터 완성·오류 분류 | 6 상황, 백오프, degraded, 비동기 세션 규칙 | adapter | 0.5d | 03 VF-04 |
| RM-02 | 예산·원장 | reserve/settle/UNKNOWN, cap, `call_index`, ticks, 제출 임시 예산 | domain·adapter | 0.75d | 04 ME-01 |
| RM-03 | `node_results` 재사용 | 키 5요소, 24h, 무효화 연동, 그래프 훅 | adapter | 0.25d | RM-02 |
| RM-04 | 프롬프트 v6/v2 | 순한맛·considering·5종 튜닝·번들 버전 | prompt | 0.75d | 05 GR-07 |
| RM-05 | 골든셋·회귀 평가기 | 50사건·자동 검사·judge·기준선·`--quick`·정책 옵션 | eval | 0.75d | RM-01 |
| RM-06 | 검수관 재현율 | 라벨 40·luna/terra·결정 | eval | 0.25d | RM-05 |
| RM-07 | 드립 예시 60 | gen/pair/import, 팀 채점 | data | 0.5d(+팀) | 04 |
| RM-08 | 사람 검수·D-07 | export 150·채점·수정·재검수·자료 | review | 0.5d(+팀 2일) | RM-04, RM-05 |
| RM-09 | 실모델 live·벤더 장애 | 20회 실측·폴백률·비용·장애 2종 | qa | 0.25d | RM-02 |

**RM-01** — [ ] 6 분류 / [ ] 백오프 규칙 / [ ] degraded / [ ] 테스트
**RM-02** — [ ] micro-USD·cap / [ ] reserve/settle/UNKNOWN / [ ] `call_index` / [ ] ticks / [ ] submission 예산
**RM-03** — [ ] 키·조회·저장 / [ ] 그래프 훅 / [ ] 무효화
**RM-04** — [ ] common-v6 사건 유형 / [ ] mild-v6 / [ ] 5종 튜닝 / [ ] 번들 해시
**RM-05** — [ ] 50사건 / [ ] 검사 10항 / [ ] judge / [ ] 기준선·판정 / [ ] 리포트
**RM-06** — [ ] 라벨 40 / [ ] 3회 측정 / [ ] 설정 반영
**RM-07** — [ ] 원문 수집 요청 / [ ] gen / [ ] pair / [ ] import ≥ 60
**RM-08** — [ ] export / [ ] 채점 / [ ] 수정·재검수 / [ ] D-07 자료
**RM-09** — [ ] live 20 / [ ] 장애 2종 / [ ] INDEX §7 기록
