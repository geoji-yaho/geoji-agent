# 떼거지 AI 에이전트 — 무엇이고 어떤 역할이 있는가

백엔드·프론트가 AI 파트(`geoji-agent`)의 구조를 물을 때 읽는 한 장. 계약과 붙이는 법은 10(`docs/plans/10-backend-contract.md`)이 정본이고, 이 문서는 **역할 이름과 흐름을 설명**만 한다. 값이 다르면 10 과 코드가 맞다.

- 기준 코드: `geoji-agent` main 9/16 (`src/geoji_ai/graphs/`, `prompts/`)
- 마지막 갱신: 2026-09-16

## 0. 먼저 답 — 검수관과 배심원은 다르다

| | 배심원 | 검수관 |
|---|---|---|
| 누구 | **사람**. 방 친구들 | **AI**. 모델 호출 1~2회 |
| 하는 일 | 지출에 유죄·무죄(또는 살까 말까에 동의·기각) **투표** | 서기가 쓴 판결문이 **정책을 넘지 않았는지 검사**(인격 공격, 근거 없는 주장, 평결과 모순, 비속어 목록 밖 등) |
| 결과 | 평결(`guilty/notGuilty/agree/disagree/dismissed`)과 유죄율 | `EvaluationReport`(`pass` + 위반 코드 목록). 판결문 저장 여부를 가른다 |
| 소유 | **백엔드**(투표·평결 확정·`verdicts` 테이블) | **AI 워커**(그래프 C 안) |
| 평결을 바꿀 수 있나 | 만든다 | **못 바꾼다.** 검수관은 문장만 본다. 평결·형량은 건드리지 않는다 |

AI 는 **평결을 내리지 않는다.** 평결은 배심원 투표의 결과이고 백엔드가 확정한다. AI 가 하는 것은 그 평결을 받아 **형량을 고르고(유죄일 때만) 판결문을 쓰고 검수하는 것**이다. 판사 역할을 굳이 찾으면 양형관 + 서기 + 검수관 셋을 합친 것이지, "판사"라는 단일 역할은 없다.

## 1. 전체 그림 — 사람이 하는 것과 AI 가 하는 것

```
[사용자] 지출 등록 ─▶ [백엔드] ─내부 HTTP─▶ [AI API] 심문관 (그래프 A, 4초 안 동기 응답)
                          │ 저장 + ai.jobs INSERT (PREPARE)
                          ▼
                    [AI 워커] 그래프 B: 조서 + 드립 후보 (미리 준비, 사용자는 모른다)
                          
[배심원 = 사람] 투표 ─▶ [백엔드] 평결 확정 ─▶ ai.jobs INSERT (SENTENCE, 마감 10초)
                          ▼
                    [AI 워커] 그래프 C: 양형관(유죄만) → 서기(강도별) → 검수관 → finalize
                          │ 워커는 업무 테이블을 직접 쓰지 않는다. 백엔드 finalize API 로 넘긴다
                          ▼
                    [백엔드] 재검증 · 저장 · 짤 선택 ─▶ [프론트] 폴링으로 판결문 노출
```

- **AI API**(FastAPI)와 **AI 워커**(Python, `ai.jobs` 큐 소비) 두 프로세스. 백엔드가 동기로 부르는 것은 심문관 하나(`POST /internal/v1/intake`)이고, 나머지는 전부 큐를 거치는 비동기다
- 10초를 넘기면 백엔드 watchdog 이 **템플릿 판결문**으로 먼저 확정하고, AI 는 뒤에 `TEXT_RETRY` 로 문장만 다시 채운다. 그래서 프론트는 `source=TEMPLATE|AI` 를 본다

## 2. 역할 여섯 — 이름 · 입력 · 출력 · 모델

계획서와 용어 룰은 "다섯 역할"이라 하는데, 조서 옆에 **드립 후보**를 별도 호출로 두어 코드는 여섯 호출이다.

| 역할 | 영어(코드 `role`) | 언제 | 무엇을 받아 | 무엇을 내나 | 모델 · 상한 |
|---|---|---|---|---|---|
| **심문관** | Intake (`intake`) | 지출 등록 직전, 백엔드가 동기 호출 | `item`(무엇을), `reason`(사유), 금액, 카테고리, `mode` INITIAL/FINAL_CHECK | `IntakeResult`: `PASS` / `NEEDS_CLARIFICATION`(솔직 팝업) / `BLOCKED`, 카테고리 오류 의심, 인젝션 감지 | OpenAI luna · 4초. 실패하면 `intake_source=FALLBACK` 으로 `PASS` |
| **조서** | Context (`context`) | 게시물 저장 뒤 `PREPARE` job(그래프 B) | 이번 지출 + 백엔드 `resolve-evidence` 가 준 30일 이력·방 규칙·최근 판결 | `Dossier`: 사실 목록 `F0~F6` (이번 건, 반복 패턴, 과거 사유, 과거 판결, 규칙 위반, 상태, 사유 분석). **라벨은 코드가 붙인다** | OpenAI luna |
| **드립 후보** | Banter (`banter`) | 조서 직후, 강도마다 1회 | 조서 + 방 강도 | 비꼼 후보 문장 여러 개(전략 8종 중 하나씩, 근거 라벨 포함). 서기가 골라 쓴다 | xAI Grok |
| **양형관** | Sentencing (`sentencing`) | `SENTENCE` job(그래프 C), **`spent` ∧ `guilty` 일 때만** | 평결·유죄율·정책 허용 형량 목록 + 조서 | `SentencingDecision`: 형량(`probation/oneDay/life`) + 양형 이유 100자 + 가중·감경 사유. **허용 목록 밖이면 코드가 절삭**, 실패하면 정책 `fallback_sentence` | OpenAI luna · 3초 |
| **서기** | Writer (`writer`) | 양형 뒤(무죄·동의·기각이면 바로), 방 강도마다 1회 병렬 | 평결·형량·조서·드립 후보·공격 각도(서버 지정 6종 순환)·강도별 문체 예시 | `TextDraft`: `headline` 30자 + `statement` 2~4문장(합 300자, 문장마다 근거 라벨) + 짤 태그·힌트 | xAI Grok · 6초. 실패 강도는 `TEMPLATE` 치환 |
| **검수관** | Evaluator (`evaluator`) | 서기 뒤, 전 강도 1회(+ `hell` 은 별도 1회) | 서기 초안 전체 + 조서 + 평결 + 정책 버전 | `EvaluationReport`: 강도별 `pass` + 위반 코드(`PERSONAL_ATTACK` `UNGROUNDED_CLAIM` `VERDICT_CONTRADICTION` `PROFANITY_OUT_OF_LIST` 등 11종) | OpenAI luna · 4초. `hell` 은 `MODEL_EVALUATOR_HELL` |

- **강도**(`mild/spicy/hell`, 순한맛·매운맛·지옥맛)는 방 설정이다. 게시물이 여러 방에 있으면 그 방들의 강도 집합만큼 서기가 병렬로 뛴다. 모델이 강도를 고르지 않는다
- 검수관이 `pass=false` 를 내면 그 강도만 서기가 **한 번** 다시 쓴다(공격 각도 +1, 위반 사항 전달). 또 실패하면 그 강도는 템플릿 문장으로 간다. 양형 이유가 걸리면 재생성 없이 템플릿 치환(D-19)
- 정책은 `guardrail-v2`. 지옥맛에서 인격 조롱은 허용하되 **정체성 비하·자해·죽음 어휘·목록 밖 비속어**는 어느 강도에서도 막는다. `guardrail-v1` 은 더 엄격한 옛 정책이고 fixture 로만 남아 있다

### 비용·시간(00 §7 계획값)

| 경로 | 호출 | 목표 |
|---|---|---|
| 유죄 | 양형관 + 서기(강도별 병렬) + 검수관 | 7~8초 |
| 무죄·동의·기각 | 서기 + 검수관 | 5~6초 |
| 판결 1건 비용 | 약 21원(서기가 13원) | 상한 40원 |

## 3. 흐름 셋 — 그래프 A · B · C

코드는 `src/geoji_ai/graphs/`. B·C 는 LangGraph, A 는 함수 셋이다.

| 그래프 | job kind | 노드 순서 | 모델 호출 | 끝나면 |
|---|---|---|---|---|
| **A 심문** | 없음(동기 API) | `validate_request → call_intake → validate_intake_output`, 실패 시 `fallback_result` | 심문관 1 | `IntakeResult` 를 백엔드에 응답 |
| **B 사전 준비** | `PREPARE` | `load_case → load_reusable_prep → recall_candidates → resolve_sources → build_db_evidence → analyze_reason → persist_dossier → generate_banter → validate_banter → persist_banter` | 조서 1 + 드립 후보(강도 수) | `ai.dossiers`·`ai.trial_prep` 에 준비 자료. 드립 실패는 조서를 되돌리지 않는다 |
| **C 선고** | `SENTENCE`, `TEXT_RETRY` | `begin_generation → load_valid_prep → (inline_context \| minimal_dossier) → sentencing → writer(fan-out) → join → deterministic_validate → evaluator ⇄ writer_repair → finalize \| generation_failed` | 양형관 ≤1 + 서기(강도 수) + 검수관 1~2 | 백엔드 `finalize` API. 백엔드가 재검증하고 저장 |
| **retain** | `RETAIN` | 그래프 아님. 확정 판결·승인 댓글을 메모리에 참조로 적재 | 없음 | `ai.memory_facts`. 다음 조서의 "과거 이력"이 된다 |

- 그래프 B 가 끝나기 전에 평결이 확정되면 백엔드는 SENTENCE 를 **PREPARE 종료(최대 30초)까지 기다렸다가** 넣는다(10 §3 게이트, D-24). 준비 자료가 없으면 C 가 남은 시간에 따라 조서를 인라인으로 만들거나(`inline_context`) 이번 건 사실 `F0` 만으로 간다(`minimal_dossier`)
- `TEXT_RETRY` 는 그래프 C 를 `REGENERATE` 모드로 다시 도는 것이다. 형량은 이미 확정돼 있어 **문장만** 다시 쓴다. 20초 예산, 보정 없음
- `dismissed`(정족수 미달 각하)는 **job 자체를 만들지 않는다.** `disagree`(살까 말까 부결)는 만들되 양형관만 건너뛴다

## 4. 백엔드가 만나는 접점

| 접점 | 방향 | AI 쪽 역할 | 10 절 |
|---|---|---|---|
| `POST /internal/v1/intake` | 백엔드 → AI API | 심문관 | §4, §16.2 |
| `ai.jobs` INSERT 5지점 | 백엔드 → 큐 | 워커가 250ms 안에 집는다 | §3 |
| `snapshot` · `resolve-evidence` | 워커 → 백엔드 | 조서·양형관·서기가 읽을 사건·이력을 받는다 | §4.1, §4.2 |
| `begin-generation` | 워커 → 백엔드 | 그래프 C 시작. 고정 형량·`text_version`·마감을 받는다 | §4.3 |
| `finalize` | 워커 → 백엔드 | 양형·초안·검수 보고서·`draft_hash` 를 한 번에. 백엔드가 12단계 재검증 뒤 저장 | §5 |
| `generation-failed` | 워커 → 백엔드 | 오류 코드와 함께 포기. 백엔드가 round 재시도 여부를 정한다 | §4.6, §7 |
| watchdog · 템플릿 | 백엔드 단독 | AI 가 10초를 넘기면 `templates-v1.json` 으로 먼저 확정 | §6, §10 |
| `GET /posts/{id}/verdict` 폴링 | 프론트 → 백엔드 | AI 는 관여 없음. `text_status`·`source` 로 AI/TEMPLATE 구분 | §9 |

## 5. 헷갈리기 쉬운 이름

| 이렇게 들리지만 | 실제로는 |
|---|---|
| 검수관 = 배심원? | 아니다. §0. 배심원은 사람, 검수관은 AI 문장 검사 |
| 양형관 = 판사? | 양형관은 **형량만** 고른다. 유죄일 때만 뛴다. 무죄·동의·기각에는 없다 |
| 서기 = 판결문 쓰는 AI? | 맞다. `headline` + `statement` 가 판결문 본문이다. 강도마다 따로 쓴다 |
| 조서 = 근거? | 조서는 근거 사실의 **목록**(`F0~F6`). 근거 원본은 백엔드 `resolve-evidence` 가 주고, 라벨은 코드가 붙인다 |
| 드립 후보 = 서기? | 드립 후보는 서기 **전에** 만드는 비꼼 재료다. 서기가 그중 하나를 골라 문장에 녹인다 |
| 평결(verdict) / 형량(sentence) / 양형 이유(sentencing_reason) | 평결은 배심원 투표 결과(5종). 형량은 유죄 때 양형관이 고른 벌(3종). 양형 이유는 형량을 고른 한 문장 |
| `source` AI / TEMPLATE / RULE | AI 가 쓴 것 / 템플릿 파일 문장 / 정책 규칙이 고른 형량(`fallback_sentence`) |
| `PENDING → GENERATING → TEMPLATE_READY → AI_READY` | 판결문 상태. `TEMPLATE_READY` 는 watchdog 이 먼저 확정한 것, `AI_READY` 가 최종 |
| `intake_source` AI / FALLBACK | 심문관이 답했는지, 시간 초과·오류로 코드가 `PASS` 를 대신 냈는지 |
| 짤(`meme_tag`) | 서기가 태그 5종(`GUILTY_HEAVY` 등)과 힌트만 낸다. 이미지 선택·점수는 **백엔드 finalize**(10 §11) |

## 6. 코드 위치 (AI 파트용)

| 무엇 | 경로 |
|---|---|
| 그래프 A·B·C, 템플릿 치환 | `src/geoji_ai/graphs/{intake,preparation,sentencing,templates}.py` |
| 프롬프트(역할별 · 강도별) | `prompts/intake-v1.md` `context-v1.md` `banter-v1.md` `sentencing-v1.md` `writer/{common,mild,spicy,hell}-v5.4.md` `evaluator/guardrail-v2.md` |
| 계약 정본(JSON Schema) · pydantic 미러 | `contracts/*-v1.schema.json` · `src/geoji_ai/contracts/` |
| 역할별 모델·상한 설정 | `src/geoji_ai/core/config.py` (`MODEL_JUDGMENT` `MODEL_WRITER` `MODEL_EVALUATOR_HELL`, `*_TIMEOUT_SECONDS`) |
| job 종류 → 처리기 | `src/geoji_ai/workers/dispatch.py` |
| 검증 규칙(구조·텍스트) · 어휘 목록 · 공격 각도 | `src/geoji_ai/domain/{validation,lexicon,attack_angles}.py` |
| 계획서(설계 정본) | `docs/plans/05-graphs-b-c.md`(B·C) · `07-intake-frontend.md`(A) · `06-real-model-budget-prompts-eval.md`(프롬프트·정책) |
