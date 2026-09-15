# AI 파트 전달 보고서 — 백엔드(geoji-server)

AI 파트(`geoji-agent`)에 전할 내용을 한곳에 모은다. 백엔드가 10(`docs/plans/10-backend-contract.md`)을 구현하면서 생긴 **질문·10 과 달라진 점·§14 답·§0.1 반영 제안·AI 파트가 할 일**이다. 10 사본은 백엔드에서 고치지 않으므로, 여기 내용을 AI 저장소 10 에 옮기는 것은 사람이 한다.

- 기준 10: geoji-agent `b77c27b` 사본
- 마지막 갱신: 2026-09-15 — W1~W6 머지분(main `047b828`). chore-remove-trial 은 프론트 전환 확인 전이라 보류
- 원문: 각 PR 본문의 "AI 파트 회신" 절(PR #2~#14, W1·W2 는 git 머지라 워커 보고서)
- 비밀값의 실제 값은 적지 않는다

## 목차

1. [AI 파트가 할 일](#1-ai-파트가-할-일)
2. [AI 파트에 묻는 질문](#2-ai-파트에-묻는-질문)
3. [10 과 달라진 점](#3-10-과-달라진-점)
4. [10 에 없어 백엔드가 정한 값](#4-10-에-없어-백엔드가-정한-값)
5. [10 에 없는 테이블·컬럼](#5-10-에-없는-테이블컬럼)
6. [§14 회신 대기 답](#6-14-회신-대기-답)
7. [§0.1 할 일 반영 제안](#7-01-할-일-반영-제안)
8. [가짜 백엔드와 다른 동작](#8-가짜-백엔드와-다른-동작)
9. [진행 현황과 출처](#9-진행-현황과-출처)

---

## 1. AI 파트가 할 일

| # | 할 일 | 10 절 | 출처 |
| - | ----- | ----- | ---- |
| A1 | 워커·AI API 의 `SERVICE_AUTH_TOKEN` 과 백엔드 EB 환경변수 `SERVICE_AUTH_TOKEN` 에 **같은 값**을 넣는다(양방향 같은 토큰). 값 전달 경로는 사람 | §4.7·§16.1 | W1 infra |
| A2 | 백엔드가 AI API 를 부를 주소 `AI_API_BASE_URL` 을 백엔드에 알려 준다 | §16.1 | W1 infra |
| A3 | 워커 설정 `BACKEND_INTERNAL_URL` = 백엔드 EB 주소 + `/internal/v1` | §16.1 | 플랜 |
| A4 | 템플릿을 바꾸면 `templates-v2` 로 올리고 백엔드에 알린다(백엔드는 `templates-v1.json` 을 geoji-agent `995c7fb` 바이트 그대로 고정 복사) | §10·§16 | W2 pure |
| A5 | `workers/dispatch.py` 모듈 docstring 정리 — "`intensities[]`·RETAIN `verdict_version` 은 백엔드 미채택이라 넣지 않는다"가 낡았다. 백엔드는 둘 다 넣는다 | §3 | W2 jobs |
| A6 | 워커가 **PREPARE·SENTENCE·TEXT_RETRY snapshot 404 와 resolve-evidence 404** 도 skip 으로 받는지 확인(백엔드는 원본 게시물이 삭제되면 kind 와 무관하게 404) | §4.1·§4.2 | W3 internal-read |
| A7 | 공개 API 가 `/api` 접두어·camelCase 라는 사실을 데모·문서에 반영(§3 참고) | §9 | 사용자 결정 |
| A8 | **백엔드 시드 기동 전에 워커·AI API 를 띄운다.** 시드는 실제 intake 12회·PREPARE 12·SENTENCE 10 을 만든다. 워커가 늦으면 watchdog 10초 폴백(TEMPLATE) 판결이 된다 | §12·§6 | W6 seed |
| A9 | 백엔드 시드 로그의 `10 §16.3 AI 시드:` 줄을 그대로 실행한다: `uv run scripts/seed_agent_db.py --demo-user U --demo-room R --post-ids P1,P2 --verdict-ids V1,V2 --now ISO`(`seed_memory_demo_c.py` 도 같은 값) | §12·§16.3 | W6 seed |
| A10 | `DEMO_SEEDS` 스타벅스 첫째 유죄율을 백엔드 실제 평결 **2/3(0.67)** 에 맞출지, 0.8 을 두고 어긋남을 감수할지 정한다(0.67 은 밴드 `[probation]`, 0.8 은 `[probation, oneDay]`). `DEMO_SEEDS` 의 −10일·−4일은 AI 메모리 시각이고 백엔드 posts·verdicts 시각은 시드 실행 시각이다 | §12 | W6 seed |

## 2. AI 파트에 묻는 질문

| # | 질문 | 백엔드 현재 동작 | 10 절 | 출처 |
| - | ---- | ---------------- | ----- | ---- |
| Q1 | SENTENCE·TEXT_RETRY `deadline_at` 을 `now()` 대신 `clock_timestamp()` 로 할까? Postgres `now()` 는 **트랜잭션 시작 시각**이라 업무 트랜잭션이 길면 10초 마감이 그만큼 짧아진다 | 10 §3 SQL 그대로 `now()`. 백엔드는 트랜잭션을 짧게 유지(트랜잭션 안 HTTP 금지) | §3 D-24 | W2 jobs |
| Q2 | 게시물 삭제 무효화를 `('POST', post_id)` 한 행으로 기록하면 `invalidate_scope.sql` 이 같은 게시물의 **VERDICT(source_id = verdict_id)·COMMENT memory_facts** 를 지우지 못한다. AI `delete_post` 는 payload post_id 로 전부 지운다. 백엔드가 VERDICT·COMMENT 기록도 넣어야 하나? | POST 1행만 기록 | §8·§16.3 | W3 privacy |
| Q3 | 삭제된 게시물 P 를 다른 게시물 Q 의 조서가 PRIOR 근거로 인용하면, Q 의 `privacy_versions` 에 `post:P` 가 없어 조회 시 epoch 비교로 즉시 차단되지 않고 무효화 스케줄러(약 5초) 뒤 템플릿으로 바뀐다. 허용 범위인가? | 스케줄러 5초 주기로 전환 | §8 | W3 privacy |
| Q4 | `RecentVerdict.sentence` 를 nullable 로 바꿀 수 있나? 지금 워커 모델이 non-null str 이라 비유죄·동의·기각 평결을 `recent_verdicts` 에 넣으면 응답 전체가 거부된다 | 유죄(sentence 있음)만 넣는다 | §4.2 | W3 internal-read |
| Q5 | `RULE/{rule_id}/{version}` 참조에 room_id 가 없다. `rule_id` 가 방별 rules 배열 인덱스라 공유 방이 여럿이면 방끼리 구분하지 못한다. 참조 모양을 바꿀까? | RULE 후보는 sources 에서 버린다 | §4.2 | W3 internal-read |
| Q6 | 반복 집계 "확정 소비"를 AI `domain/aggregation.py` 와 같게 **판결 확정과 무관한 spent** 로 해석했다. 맞나? | 작성자·현재 사건 제외·같은 카테고리·spent·삭제 제외·[created_at−30일, created_at) | §4.2 | W3 internal-read |
| Q7 | finalize 에서 10 에 문장이 없어 백엔드가 더한 검사 6개(아래 [4.3](#43-finalize-검사)) — 워커 draft 가 이 조건을 늘 만족하나? | 어기면 409 `EVIDENCE_INVALIDATED` 또는 422 `INVALID_DRAFT` | §5 | W3 finalize |
| Q8 | `text_evidence_refs.field_path` = `statement[j]`(AI `EvidenceRef` 와 같게). 양형 근거 라벨은 intensity 가 없어 저장하지 않고 검증만 한다. 괜찮나? | 위대로 | §2·§5 | W3 finalize |
| Q9 | finalize 12단계 job `SUCCEEDED` 때 백엔드는 `lease_until` 만 비우고 `owner_id`·`generation_id` 는 남긴다. 워커의 뒤이은 complete 는 0행이 된다. 괜찮나? | 위대로 | §5 | W3 finalize |
| Q10 | §11 짤 후보 0 일 때 "결과별 기본 이미지" id 가 10 에 없다. 기본 이미지를 정해 줄 수 있나? | `meme_image_id = null`. 최근 노출 5장 = 같은 작성자의 다른 판결에 고정된 짤(노출 로그 없음), `banter_strategy` 는 `default_intensity` 문구 기준 | §11 | W3 finalize |
| Q11 | `verdict_final.banter_strategy` 를 저장할 자리가 10 §2 에 없다. 필요하면 컬럼을 정해 달라 | RETAIN snapshot 에 `null` | §2·§4.1 | W3 internal-read |
| Q12 | `contracts/verdict-view-v1` 에 null 허용을 더할까? 투표 중 `jury_status`·`view`, 비유죄 `view.sentence`·`sentence_label`, 짤 없음 `view.meme` 이 null 이다(아래 [§3](#3-10-과-달라진-점)). 공개 응답은 camelCase 라 키 이름도 스키마와 다르다 | 스키마 필수 키를 camelCase 로 바꿔 테스트로 대조, 위 null 은 스키마 밖 | §9 | W4 public-api |
| Q13 | §3 "승인된 댓글(안전 검토 통과)"을 **JUDGED 게시물(verdict FINAL·`jury_result ≠ dismissed`·게시물 미삭제) 댓글 전부**로 넣고 안전 필터는 워커 `comment_safety` 에 맡기는 것에 합의하나? | 위대로. 확정 전 댓글은 확정 뒤 5초 스캔으로 넣는다. version 은 수정 기능이 없어 늘 1 | §3 | W5 post-comments |
| Q14 | RETAIN comment job 이 진행 중에 댓글이 삭제되면 백엔드는 그 job 을 끄지 않고(D-26 대상 아님) snapshot 404 로 워커가 skip 하게 둔다. 괜찮나? | 위대로 | §4.1·§8 | W5 post-comments |
| Q15 | 각하(`dismissed`)용 문구를 templates-v1 에 둘까? 지금 키가 없어 판결 조회 `view = null` | 문구 없음 | §9·§10 | W4 public-api |

## 3. 10 과 달라진 점

사용자 결정으로 10 문장과 다르게 구현했거나 구현할 것.

| 항목 | 10 | 백엔드 | 10 절 | 결정 |
| ---- | -- | ------ | ----- | ---- |
| 공개 API 경로 | `/post-submissions`, `/posts/{id}/verdict` 등 접두어 없음 | **전부 `/api` 접두어**: `POST /api/post-submissions`, `POST /api/post-submissions/{id}/complete`, `GET /api/posts/{id}/verdict?room_id=`, `GET /api/posts/{id}/share-card`, `POST /api/posts/{id}/votes`, `DELETE /api/posts/{id}`, `GET /api/posts/{id}/trace` | §9 | 사용자 9/14 |
| 공개 JSON 표기 | snake_case(`submission_id`, `intake_result` 등). `verdict-view-v1` 도 snake | **공개 `/api/**` 는 전부 camelCase**(`submissionId`, `postType`, `amountKrw`, `roomIds`, `intakeResult` 안 키까지, trace 응답 키도). verdict 조회는 `verdict-view-v1` 키를 camelCase 로 바꿔 낸다(W4). **`/internal/v1` 과 백엔드→AI API 호출(intake·trace)은 snake 그대로** | §9 | 사용자 9/15 |
| intake 4xx | 4xx 는 거부 | **401·403(서비스 토큰 불일치)은 FALLBACK 으로 등록 허용**(`status=PASS`, `intake_source=FALLBACK`) + ERROR 로그(상태 코드와 응답 `code` 만). 422 등 그 밖 4xx 는 전처럼 거부 → 사용자 400. **구현됨**(W4 `fix-intake-auth`, 커밋 `6f19dc4`). 10 §4 4xx 문장에 "401·403 은 백엔드가 FALLBACK 처리" 반영 제안 | §4·§4.7 | 사용자 9/15 |
| 제출 만료·요청 한도 | `EXPIRED` 전이·만료 409·429 | **이번 범위에서 뺀다.** `submissions.expires_at` 은 NULL 허용(004b) | §2·§9 | 사용자 9/14 |
| snapshot 삭제 404 범위 | RETAIN 원본 삭제만 404 | **PREPARE·SENTENCE·TEXT_RETRY·RETAIN 과 resolve-evidence 모두** 원본 게시물 삭제 시 404 `{"code":"NOT_FOUND"}`. job 검증 409 가 먼저 | §4.1·§4.2 | 사용자 9/15(제안 채택) |
| 판결 조회 — 투표 중 | `verdict-view-v1` 에 대기 상태 없음(`jury_status` enum 은 판결 5종) | `200` + `juryStatus = null`, `sentenceStatus = PENDING`, `textStatus = PENDING`, `textVersion = 0`, `view = null`, `pollAfterMs = 5000` (W4) | §9 | 사용자 9/15 |
| 판결 조회 — 비유죄·짤 없음 | `view.sentence`·`view.meme` 필수 non-null | 무죄·동의·기각은 `sentence`·`sentenceLabel = null`, `meme_image_id` 가 없으면(폴백 저장 등) `meme = null` (W4) | §9·§11 | 사용자 9/15 |
| 판결 조회 — 각하 | `verdict_texts` 없음, templates-v1 에 dismissed 키 없음 | `juryStatus = dismissed`, `sentenceStatus = PENDING`, `textStatus = PENDING`, `view = null`, `pollAfterMs = 0`(종결) (W4) | §9 | 사용자 9/15 |
| `poll_after_ms` 값 | 프론트 규칙만(1초 → 15초 뒤 5초, AI_READY 즉시 중단, 템플릿 후 30초) | 투표 중 5000 · PENDING·GENERATING 1000 · TEMPLATE_READY 30000 · AI_READY·각하 0. 15초 뒤 5초 전환은 프론트가 경과 시간으로 한다 (W4) | §9 | 사용자 9/15 |
| 공유 카드 문구 | "공개 허용 문구만, 근거 원문·개인 이력 금지" | applied 강도 statement 문장마다 `text_evidence_refs` 를 보고 **인용 없음 또는 인용 evidence 가 전부 PUBLIC 인 AI 문장만** 넣고 나머지 자리는 templates-v1 문장. headline 은 statement 가 전부 AI 로 남을 때만 AI, 아니면 템플릿. 템플릿 행·epoch 불일치면 전부 템플릿. 접근은 작성자 또는 철회 안 된 공유 방 멤버, 확정 전·삭제 404. 금액·항목·사유·투표 사유·근거 원문·배심원 이름 없음 (W4) | §9 | 사용자 9/15 |
| §4.4 매핑 통지 | `(제안)` | **채택 안 함.** `/linked` 호출 없음, PREPARE payload 에 `submission_id` 없음 → 제출 임시 예산(`submission:{id}`)은 사건 예산으로 옮겨지지 않는다 | §4.4·§14 | 사용자 9/14 |

## 4. 10 에 없어 백엔드가 정한 값

### 4.1 인증·오류

| 항목 | 값 | 10 절 | 출처 |
| ---- | -- | ----- | ---- |
| 내부 API 인증 | `Authorization: Bearer <SERVICE_AUTH_TOKEN>` sha256 뒤 timing-safe 비교. **백엔드 설정값이 비면 내부 API 전부 401**(열어 두지 않음). 401 본문 `{"code":"UNAUTHORIZED"}` + `WWW-Authenticate: Bearer`. `/actuator/health` 만 무인증 | §4.7·§0.1 | W1 infra |
| 거부 본문 | `{"code"}` 하나. 본문 파싱·`@Valid` 실패 422 `INVALID_REQUEST`(입력값·검증 원문 없음), 401 `UNAUTHORIZED`, 404 `NOT_FOUND`, 409 `STALE_GENERATION`·`DEADLINE_EXCEEDED`·`EVIDENCE_INVALIDATED`·`IDEMPOTENCY_CONFLICT`, 422 `INVALID_DRAFT` | §4.7·§14 | W1·W3 |
| 5xx | 백엔드 처리 중 예외는 5xx 로 나간다(401 로 가려지지 않음) — 워커 재전송 규칙과 맞음 | §4.7 | W1 infra |
| `X-Generation-Id` | 없음·UUID 아님도 409 `STALE_GENERATION`. resolve-evidence 검사 순서: 인증 → 본문 422 → job 409 | §4.1·§4.2 | W3 internal-read |
| 백엔드 → AI 타임아웃 | connect 0.5초 공통. intake read **5초**, trace read **2초**(10 §4.7 에 trace read 값이 없어 추가 제안) | §4·§4.7·§16.2 | W3 submissions·trace |
| trace 응답 매핑 | 200 → 200(키 camelCase), 404 `TRACE_NOT_FOUND` → 404, 5xx(503 `DB_UNAVAILABLE` 포함)·연결 실패·그 밖 4xx(401, 없는 경로 404 `{"detail"}`) → **502**, read 타임아웃 → **504**. `code` 는 최상위에서 읽는다 | §4.5·§4.7 | 사용자 9/15 |
| trace 프록시 | 공개 `GET /api/posts/{id}/trace`, **게시물 작성자만**(없음·삭제·남의 게시물은 같은 404). 프론트는 AI API 를 직접 부르지 않는다 | §4.5·§14 | 사용자 9/14 |

### 4.2 배심·정책·집계

| 항목 | 값 | 10 절 | 출처 |
| ---- | -- | ----- | ---- |
| 정족수·동률 | 정족수 `min(2, 투표 가능 인원)`, 미달 `dismissed`. 동률 spent → `notGuilty`, considering → `disagree` | §3 | geoji-web SPEC |
| 투표 가능 인원 | 공유 방(`post_rooms`, 철회 제외) 멤버 합집합 − 작성자. 1인 1표, 수정 불가 | §3 | W3 jury |
| 확정 시점 | 전원 투표 즉시, 또는 `posts.vote_deadline_at` 마감(공유 방 `vote_deadline_minutes` 최소값). 확정 전에 이미 삭제된 게시물은 확정하지 않는다 | §3 | W3 jury |
| 강도 | `target_intensities` = 공유 방 강도 집합(mild→spicy→hell), `default_intensity`·`applied_intensity` = 표 최다 방(동률이면 방 생성일 이른 쪽) | §3·§4.1 | 사용자 9/14 |
| `policy_snapshot` | `version = "sentencing-band-v1"`(밴드를 바꾸면 v2), `reason_required = true`. 유죄: 유죄율 50~69 `[probation#1]` / 70~89 `[+oneDay#2]` / 90+ `[+life#3]`, fallback = 상한. **무죄·동의·기각: 최저 밴드** `{allowed_sentences:[{code:"probation",rank:1}], fallback_sentence:"probation"}`(case-snapshot `jury.policy` 필수 객체·minItems 1 을 맞춤, 양형관은 건너뜀). 각하: jsonb `null`(job 없음). 픽스처 `jury-*.json` 의 `"policy-v1"` 과 다르다 | §3·§4.1·§5 | 사용자 9/14·9/15 |
| `jury.guilty_ratio` | 0..1 소수. spent = guilty ÷ (guilty+notGuilty). **considering = disagree ÷ (agree+disagree)(기각 비율)**, 표 0 → 0. 픽스처 `jury-rejected.json`(1:3 → 0.75)과 같다. 이름은 guilty 인데 considering 에서는 기각 비율 | §4.1 | 사용자 9/15 |
| `burn_rate` | 0~1. 작성자 spent(삭제 제외) `amount_krw` 합, 사건 `created_at` 이 속한 **KST 달력월 1일 00:00 ~ created_at**(현재 사건 포함) ÷ `profiles.monthly_budget`, [0,1] 로 자름, budget ≤ 0 이면 0 | §4.2·§14 | 사용자 9/14·9/15 |
| `aggregates.tier` | burn_rate `<0.25 king` · `<0.5 flower` · `<0.8 hardcore` · 그 이상 `penniless`(프론트 Tier 4종) | §4.2 | 사용자 9/15 |
| `aggregates.no_spend_days` | **늘 0.** 서버에 무지출 기록 재료가 없다(posts 에 NO_SPEND 없음) | §4.2 | 사용자 9/15 |
| `aggregates.rule_version` | 공유 방 `rooms.rule_version` 최댓값, 방 없으면 1 | §4.2 | 사용자 9/15 |
| `rule_version`·`rule_id` | `rooms.rule_version int DEFAULT 1`, 규칙 편집마다 +1(편집 API 아직 없음). `rule_id` = 방 rules 배열 인덱스(0부터, 문자열). `room_snapshots[].rule_version`·`room_rules[].version` = `rooms.rule_version` | §2·§4.1·§4.2 | 사용자 9/14(A안) |
| `sources` | POST·VERDICT 만 돌려준다(RULE·COMMENT 후보는 버림). payload POST `{category, amount_krw, reason, spent_at}`, VERDICT `{result, guilty_ratio, sentence}`(비유죄면 sentence null). scope 는 원본 게시물 기준 PUBLIC(공개 공유)/ROOMS, PRIVATE 는 만들지 않는다. AI `usable` 과 같게 미리 거른다. stale(버전 불일치·FINAL 아님)·다른 작성자·삭제·현재 사건 제외. 순서는 candidates.score 내림차순 | §4.2 | 사용자 9/15 |
| `recent_verdicts` | 같은 작성자·현재 사건 제외·`confirmed_at` ∈ [created_at−30일, created_at)·FINAL·**유죄만**·scope 통과·최근 10. `verdict_id` 는 넣을 수 있으나 지금 응답에는 넣지 않는다(워커 모델이 받게 되면 알려 달라) | §4.2·§14 | 사용자 9/14·9/15 |
| include 누락 | `rules`·`recent_verdicts`·`style_comments` 는 `[]`, `aggregates` 는 늘 채운다. 다섯 키는 늘 있다. `style_comments` 는 늘 `[]` | §4.2 | 사용자 9/15 |
| `audience.public_share_enabled` | `posts.public_share_enabled` 에서 읽는다. 켜는 API 가 없어 **당분간 늘 false** | §4.1 | 사용자 9/15 |
| RETAIN `verdict_final.applied_intensity` | 컬럼이 NULL 이면 `default_intensity` | §4.1 | 사용자 9/15 |

### 4.3 finalize 검사

10 에 문장이 없어 백엔드가 더한 검사(Q7).

1. 요청 `privacy_versions` 에 post·작성자·현재 공유 방 key 가 모두 있고 요청 key 전부가 현재 epoch 와 같아야 한다. 현재 공유 방에 없는 room key 가 있으면 409 `EVIDENCE_INVALIDATED`(요청에 `audience_version` 이 없어 audience 재확인을 이것으로 대신한다)
2. `evaluation.policy_version` 도 설정값(`GUARDRAIL_POLICY_VERSION`)과 같아야 한다
3. `meme_tag` ↔ 배심 결과 일치(guilty → `GUILTY_*`, notGuilty → `NOT_GUILTY`, agree → `APPROVED`, disagree → `REJECTED`), 불일치 422
4. `reason_required` 인데 `reason_source = AI` 이고 이유가 null 이면 422(TEMPLATE·null 은 허용)
5. 검수 `texts[]` 에 요청 강도가 빠지면 422
6. `dossier_id` 의 `post_id` 가 판결 게시물과 다르면 422, dossier·evidence 무효화면 409 `EVIDENCE_INVALIDATED`

그 밖 finalize 검증: `reason_source`·`texts[].source` 필수(AI/TEMPLATE, 누락 422), 길이는 코드포인트(라벨 ≤16·`^F\d+$`, statement 1~300자·2~4개, headline ≤30, reason ≤100, keywords ≤10·≤30자) + 강도별 statement 합산 300, `meme_hints.emotion` 6종 밖 422, `draft_hash`·`evaluation_draft_hash` 둘 다 백엔드 재계산과 비교(계산 불가 — 짝 없는 서로게이트·NaN·Infinity — 도 422).

### 4.4 폴백·무효화 문구

| 항목 | 값 | 10 절 | 출처 |
| ---- | -- | ----- | ---- |
| 폴백 statement 모양 | 템플릿 줄마다 `{text, kind:"opinion", evidence_labels:[]}` 1개(워커 `graphs/templates.py` 와 달리 문장 끝에서 쪼개지 않음). `{n}`/`{m}` = votes 행 수 / guilty 수 | §4.6·§10 | W3 begin-failed·privacy |
| 비유죄 폴백 | `sentence_status = FINAL`, 형량·이유 null. TEXT_RETRY begin 의 `fixed_sentencing` 은 null | §4.6 | W3 begin-failed |
| 무효화 템플릿 전환 | 인용한 강도 행만 `verdicts.text_version + 1` 로 갱신, `text_status = TEMPLATE_READY`, `sentencing_reason`·`reason_source`·형량 유지, 전환 행 `dossier_id`·`privacy_epoch_snapshot` NULL, TEXT_RETRY 새 예약 없음. `text_evidence_refs` 는 DELETE 권한이 없어 옛 version 행이 남는다(현재 text_version 의 refs 만 본다) | §8·§10 | W3 privacy |
| 조회 판정 | `privacy_epoch_snapshot` NULL(템플릿 저장 행)은 원문 허용, 게시물 삭제는 차단 우선, snapshot 의 scope epoch 가 하나라도 다르면 템플릿 | §8 | W3 privacy |
| 무효화 기록 | 게시물 삭제·공유 철회 모두 `('POST', post_id)`·scope `post:{id}`(AI `test_deletion.py` 조합) — 철회도 그 게시물 파생 전부를 무효화. 재삭제는 epoch·기록 추가 없이 성공. 탈퇴(`user:{id}`)는 미구현 | §8 | W3 privacy |
| 무효화 스케줄러 | fixedDelay 5초, 배치 50, `SKIP LOCKED`, 5회 실패 FAILED + WARN | §8 | 사용자 9/14 |
| 댓글 삭제 scope | 10 §2 scope key 가 `user`·`room`·`post` 셋뿐이라 **댓글 삭제는 그 댓글이 달린 게시물의 `post:{post_id}`** epoch 를 올리고, 무효화 source 는 `('COMMENT', comment_id)`. 방 전체(`room:`)는 올리지 않는다(방의 다른 게시물까지 무효화되므로). W5 에서 구현 | §2·§8 | 코디네이터(10 §2 에서 도출) |
| 판결 확정 전 댓글 RETAIN | 투표 중에 달린 게시물 댓글도 판결 확정(FINAL, dismissed 아님) 뒤 `comment.approved` RETAIN 을 넣는다. 확정 뒤 작성은 즉시, 확정 전 작성은 5초 주기 스캔이 넣는다(`post_comments.retained_at`). dedupe `retain:comment:{id}:{version}` 는 그대로라 워커 쪽 변화 없음. 확정과 RETAIN 사이 최대 약 5초 지연. W5 에서 구현 | §3 | 사용자 9/15 |
| 짤 동점 | `crc32(UTF-8(post_id + image_id))` 작은 쪽(zlib 과 같음) | §11 | W2 pure |
| `draft_hash` | 백엔드 `verdict/DraftHash` 가 taxi 픽스처(sentencing 있음·null)와 경계값(제어문자·실수 표기·보조 평면 키 정렬)에서 `draft_hash.py`(`d3c7faa`)와 같은 hex. 짝 없는 서로게이트·NaN 은 hash 를 내지 않는다(422) | §5 | W2 pure |

### 4.5 job·round

| 항목 | 값 | 10 절 | 출처 |
| ---- | -- | ----- | ---- |
| ID 타입 | payload·dedupe 의 id 는 문자열(10 §1), 업무 id 는 uuid → `toString()` | §1·§3 | W2 jobs |
| RETAIN | payload 네 키(`event`·`verdict_id`·`comment_id`·`version`, 안 쓰는 id null). `sentence.finalized` 의 `version` = `verdict_version`(dedupe `retain:verdict:{verdict_id}:{version}`) | §3 | W2 jobs |
| TEXT_RETRY `intensities` | null 이면 키 생략, 빈 배열은 백엔드가 INSERT 전에 거부 | §3 | W2 jobs |
| `lease_expired_total` | 운영 reaper(5초)가 회수 건수를 `lease_expired_total kind={kind} status={FAILED\|CANCELLED\|QUEUED} count={n}` INFO 로그로. 0 건이면 로그 없음. 별도 지표 시스템 없음 | §7·§14 | 사용자 9/14 |
| D-24 게이트 | 같은 post PREPARE `QUEUED`·`RUNNING` 이면 보류, PREPARE 종료·없음 또는 `confirmed_at + 30s`(DB now())에 INSERT, job·verdict `deadline_at` = INSERT + 10s, 보류 중 `deadline_at` NULL 이라 watchdog 대상 아님. 스케줄러 250ms | §3·§6 | W3 jury |
| D-24 보류 중 삭제 | 넣는다(가짜 백엔드와 같음). D-26 이 PREPARE 를 CANCELLED → 게이트가 다음 주기에 SENTENCE INSERT → 그 SENTENCE 도 무효화가 끈다 | §3·§8·§14 | W3 jury |
| D-26 | 게시물 삭제·공유 철회 트랜잭션에서 epoch +1 먼저, 같은 트랜잭션에서 QUEUED·RUNNING PREPARE·SENTENCE(`payload->>'post_id'`)·TEXT_RETRY(`payload->>'verdict_id'`) CANCELLED, `owner_id`·`generation_id`·`lease_until` NULL, RETAIN 은 끄지 않음 | §8 | W2 jobs·W3 privacy |
| §6 4단계 | 이전 job `CANCELLED`(`owner_id`·`generation_id`·`lease_until` NULL, 이미 끝난 job 은 그대로). 워커 `complete` 경로(08 §3.1)는 쓰지 않는다 → 늦게 끝난 워커의 complete 는 0행, finalize 는 409 `STALE_GENERATION`. `active_job_id` 뿐 아니라 begin 전이라 active 에 없는 같은 verdict 의 `QUEUED`·`RUNNING` SENTENCE 도 함께 CANCELLED | §6·§14 | 사용자 9/14·W4 schedulers |
| deadline watchdog | 250ms, `deadline_at < now()` ∧ `sentence_status = PENDING` verdict 마다 한 트랜잭션: 잠금 → 이미 AI_READY·FINAL+TEMPLATE_READY 면 skip → 폴백(FINAL/RULE·TEMPLATE·RETAIN·round 1) → 이전 job CANCELLED. `deadline_at` NULL(D-24 보류)은 대상 아님. 다중 인스턴스는 `SKIP LOCKED`. 엄밀한 10,000ms 보장은 아님 | §6 | W4 schedulers |
| TEXT_RETRY round 스케줄러 | 스캔 5분. `pending_retry_at ≤ now()` ∧ `retry_round` 1~3 ∧ TEMPLATE_READY ∧ 진행 중 TEXT_RETRY 없음 → TEXT_RETRY INSERT(`intensities` = TEMPLATE 인 강도) + `pending_retry_at` NULL. round 5·10·20분, ≤ 3. **reaper 가 FAILED(`LEASE_EXPIRED`)로 바꾼 TEXT_RETRY 도 다음 round 를 예약**(generation-failed 경로에 더해). 마지막 실패는 WARN 로그(운영 알림 채널 없음) | §4.6·§7 | W4 schedulers |
| round 3 WARN 누락 | begin-generation 전에 reaper 가 회수한 **round 3** TEXT_RETRY 는 WARN 이 남지 않는다("이미 처리함"과 구분할 컬럼 없음, reaper INFO 회수 로그에는 남음) | §7 | W4 schedulers(알려진 한계) |
| generation-failed | 코드 8종 그대로. 1행 코드 즉시 폴백, 2행 코드 폴백 + round 1(`pending_retry_at` = DB now()+5분). 표 밖 코드 422 `INVALID_REQUEST`(404 검사보다 먼저). 서기 전부 시간 초과 `DEADLINE_EXCEEDED` 는 2행 | §4.6·§14 | W3 begin-failed |
| TEXT_RETRY round 안 실패 | 코드와 무관하게 문구·형량·text_version 불변, active 해제, `retry_round < 3` 이면 +1·`pending_retry_at` = 10·20분 뒤, 3 이면 예약 없음 + WARN(알림 채널 없음). TEXT_RETRY INSERT 는 W4 스케줄러 | §4.6·§7 | W3 begin-failed |
| 실패 generation 기록 | `verdicts.last_failed_generation_id`·`last_failed_code`(verdict 당 마지막 1건) | §4.6 | 사용자 9/14 |
| 404 본문 | `{"code":"NOT_FOUND"}`(verdict 없음·경로 id 가 UUID 아님) — 가짜 백엔드와 같은 이름 | §4.7 | W3 finalize |

### 4.6 intake·제출

| 항목 | 값 | 10 절 | 출처 |
| ---- | -- | ----- | ---- |
| intake 호출 | `POST /internal/v1/intake`, 요청 9 키(snake) `{schema_version:1, submission_id, payload_hash, mode, post_type, amount_krw, category, item, reason}` | §4 | W3 submissions |
| FALLBACK | 연결 실패·timeout·5xx·키 없음·계약 밖 200 → 백엔드가 FALLBACK 으로 등록 허용. 401·403 도 FALLBACK(§3). `BLOCKED` 는 PROCEED 불가. 422 입력 코드(`ITEM_LENGTH`·`REASON_LENGTH`·`AMOUNT`·`INVALID_REQUEST`)는 400 | §4 | W3 submissions·사용자 9/15 |
| 거부 본문 읽기 | `code` 를 최상위에서 읽는다. 옛 `{"detail":{"code"}}` 는 읽지 않고 `HTTP_<status>` 로 본다 | §4·§4.7 | W3 submissions |
| `submission_id` 에코 | 응답에서 `submission_id`·`payload_hash` 를 읽지 않고 호출 단위로 묶는다 | §4 | W3 submissions |
| FINAL_CHECK | NEEDS_CLARIFICATION 이 와도 다시 묻지 않고 `intake_status = UNCLARIFIED` 로 등록(질문은 제출당 1회) | §4 | W3 submissions |
| `revision` | complete 요청 `revision` = 직전 응답의 `payload_hash`(낙관적 동시성 토큰) | §9 | 사용자 9/15 |
| `posts.intake_status` | 최종 PASS → `PASS`, 질문 뒤 PROCEED·FINAL_CHECK 가 NEEDS_CLARIFICATION → `UNCLARIFIED`. `intake_source` 는 마지막 intake 결과 | §2 | W3 submissions |

### 4.7 공개 API(투표·판결 조회·공유 카드·삭제)

| 항목 | 값 | 10 절 | 출처 |
| ---- | -- | ----- | ---- |
| 투표(10 에 없음) | `POST /api/posts/{id}/votes {verdict, reason, roomId}`. 게시물 행 `FOR NO KEY UPDATE` → 없음 404 → 작성자·철회 안 된 공유 방 멤버 아님 403 → `post_type` 에 맞는 두 값·reason 1~500 아님 400 → 마감·확정됨·중복 409 → INSERT 와 같은 트랜잭션에서 평결 확정 시도(D-24 게이트 포함) | SPEC S-14 | W4 public-api |
| 삭제(10 에 없음) | `DELETE /api/posts/{id}` 작성자만(타인 403, 없음 404), §8 무효화 트랜잭션(epoch +1·D-26 진행 중 job CANCELLED), 재삭제 204 | §8 | W4 public-api |
| 판결 조회 권한 | 작성자 또는 `post_rooms.revoked_at IS NULL` 공유 방 멤버. `room_id` 를 주면 철회 안 된 공유 방이어야 한다. 없음·권한 없음·삭제는 모두 같은 404 | §9·§8 | W4 public-api |
| 판결 조회 강도 | `room_id` 방 강도 행, 없으면 `applied_intensity`(비면 `default_intensity`) | §9 | W4 public-api |
| TEMPLATE view | `source = TEMPLATE` 이면 `sentencingReason = null`(§9 양형 이유 숨김). epoch 불일치면 저장 행 대신 templates-v1 즉석 렌더(`{n}` = 표 수, `{m}` = 유죄 표 수) | §9·§8·§10 | W4 public-api |
| 데모 시드(§12·§16.3, W6 PR #14) | `seed` 프로필로 띄울 때만 도는 러너. 사용자 4는 기존 Supabase Auth id 를 `GEOJI_SEED_USER_IDS` 로 받는다(첫째가 데모 C). 게시물은 실제 제출·intake 경로, 판결은 실제 SENTENCE 파이프라인. 짤 이미지 시드 없음. 데모 C 방은 매운맛. **스타벅스 두 건 유죄율은 2/3(0.67)·3/3(1.0)** — 10 §12 사용자 4 라 배심원이 최대 3명이어서 AI `DEMO_SEEDS` 의 0.8 을 만들 수 없다. 0.67 은 양형 밴드 50~69% `[probation]` 이라 0.8 의 `[probation, oneDay]` 와 허용 형량이 다르다. 금액 6,100·카페/간식·item 스타벅스는 AI 상수와 같다. 끝에 데모 C id 5종을 로그로 낸다(AI 시드 명령 입력) | §12·§16.3 | 사용자 9/15·코디네이터 |
| 게시물 댓글 API(10 에 없음) | `GET·POST /api/posts/{postId}/comments {roomId, content}`, `DELETE /api/posts/{postId}/comments/{commentId}`. 목록은 작성자 또는 철회 안 된 공유 방 멤버(아니면 404), 보이는 댓글은 요청자가 볼 수 있는 활성 방 것만. 작성은 그 방 멤버(게시물 작성자 포함), 내용 코드포인트 1~200·공백만 400. 삭제는 본인만(방 철회 뒤에도 허용), 재삭제 204. API.md 13장 | SPEC·§9 | W5 post-comments |
| 공유 카드 응답 | `{postId, postType, juryStatus, intensity, headline, statement[], sentence, sentenceLabel, meme}` 9키. 강도는 applied. 공개 판정은 `ai.evidence` 가 있고 `scope->>'visibility' = 'PUBLIC'` ∧ `invalidated_at IS NULL`(AI `usable_for_share_card` 와 같게). 인용 evidence 가 없어진 ref 는 공개 불가. 템플릿 문장이 모자라면 마지막 문장 | §9 | W4 public-api |

## 5. 10 에 없는 테이블·컬럼

004 초안(`src/test/resources/db/004_verdict_generation.sql`)과 추가분(`004b_*.sql`). 정본은 supabase/migrations 로 사람이 옮긴다. AI 저장소 `database/migrations/` 에는 두지 않는다.

| 대상 | 무엇 | 왜 |
| ---- | ---- | -- |
| `post_rooms(post_id, room_id)` + `revoked_at timestamptz NULL`(004b_privacy) | 게시물 공유 방. 철회는 행 삭제가 아니라 `revoked_at` 표시(`backend` 에 DELETE 권한 없음) | 공유 방·투표 가능 인원·scope |
| `votes(id, post_id, voter_id, room_id, verdict, reason 1~500, created_at)` UNIQUE(post_id, voter_id) | 배심원 표 | 평결·`vote_counts`·`guilty_ratio` 계산 |
| `privacy_invalidations` | §8 무효화 작업 기록. scope 1행, PENDING → DONE/FAILED, attempts·last_error. `backend` 만 | §8 "무효화 작업 기록" |
| `posts` | `vote_deadline_at`·`created_at`·`author_id`·`post_type`·`amount_krw`·`category`, `public_share_enabled boolean DEFAULT false`(004b_internal_read) | 10 §2 요약에 없는 필드 |
| `verdicts` | `target_intensities jsonb`·`default_intensity`(§4.1 jury 출처), `last_failed_generation_id`·`last_failed_code`(§4.6 재전송 판정). `jury_result` 는 평결 enum 한 값, `vote_counts`·`guilty_ratio` 는 `votes` 에서 계산. `verdict_version`·`posts.version`·`audience_version` 은 1부터 | §4.1·§4.6 |
| `verdict_texts.privacy_epoch_snapshot jsonb` | 저장 당시 epoch — §8 조회 비교 | §8 |
| `submissions.intake_result jsonb`, `expires_at` NULL 허용(004b_submissions) | 마지막 IntakeResult, 만료 미구현 | §9 |
| `meme_images.image_url` | 짤 이미지 주소 | §11 |
| `rooms.rule_version int NOT NULL DEFAULT 1` | 규칙 버전 | §4.1·§4.2 |
| `post_comments(id, post_id, room_id, user_id, content 1~200, version, retained_at, deleted_at, created_at)`(004b_post_comments, W5 PR #13) | 게시물 댓글. `retained_at` = RETAIN 을 넣은 시각(확정 전 댓글 스캔용) | §3·§4.1·§8 |
| `ai.privacy_epochs(scope_key text PK, epoch bigint DEFAULT 0)` | 10 §14 가정과 **같다**. `ai_worker` SELECT, `backend` SELECT·INSERT·UPDATE, 행 없으면 0. 백엔드는 잠글 때 epoch 0 행을 먼저 INSERT 하므로 워커가 0 인 행을 볼 수 있다(뜻은 행 없음과 같다). 잠금 순서는 `ORDER BY scope_key COLLATE "C"` | §2·§14 |
| `ai.text_evidence_refs` | PK(verdict_id, text_version, intensity, field_path, evidence_id), `intensity text CHECK(mild·spicy·hell)`, INDEX(evidence_id) | 10 에 없는 제약. 워커는 쓰지 않음 |
| `ai.verdict_commit_records` | `verdict_id` FK → `verdicts`, `committed_at DEFAULT now()` | 10 에 없는 제약. 워커는 쓰지 않음 |
| `ai.jobs` 권한 | `backend` INSERT·SELECT(ON CONFLICT … RETURNING) 동작, `ai_worker` INSERT 거부를 001 복사본 컨테이너 테스트로 확인 | §1 |

## 6. §14 회신 대기 답

| §14 행 | 답 | 출처 |
| ------ | -- | ---- |
| 004 `privacy_epochs` | 가정과 같다(위 §5) | W2 schema |
| `burn_rate` | 0~1 비율 채택(계산식 §4.2) | 사용자 9/14 |
| `recent_verdicts.verdict_id` | 넣을 수 있다. 워커 모델이 받게 되면 알려 달라. 지금 응답엔 없음 | 사용자 9/14 |
| `lease_expired_total` | reaper 가 kind·결과 상태별 INFO 로그로 남긴다 | 사용자 9/14 |
| §5 10단계(TEXT_RETRY 강도) | 채택: 중복 없음 ∧ ⊆ `target_intensities`, 받은 강도만 갱신, 전체 AI 면 `AI_READY`, FINAL 에서 형량·양형 이유가 다르면 422 | 사용자 9/14 |
| §5 422 코드 | `INVALID_DRAFT` 하나(형량 규칙 위반 전용 코드 없음) | 사용자 9/14 |
| watchdog 뒤 이전 job 상태(§6 4단계) | `CANCELLED`. `complete` 경로 안 씀, 늦은 finalize 409 `STALE_GENERATION`(AI 테스트 고정값과 같음). 구현됨 | 사용자 9/14·W4 schedulers |
| D-24 `(제안)` | 채택 | 사용자 9/14 |
| D-24 보류 중 삭제 | 넣는다(§4.5) | W3 jury |
| D-26 `(제안)` | 채택 | 사용자 9/14 |
| D-26 탈퇴 범위 | **미구현, 회신 대기 유지** | W3 privacy |
| §4.4 매핑 통지 | 채택 안 함 | 사용자 9/14 |
| §4.5 trace 프록시 주체 | 백엔드(행 닫기 제안) | 사용자 9/14 |
| §12 데모 C 시드 식별자 | 백엔드 시드(`seed` 프로필)가 끝에 INFO 로 `10 §12 데모 C 시드 식별자 demo_user=U demo_room=R post_ids=P1,P2 verdict_ids=V1,V2 now=ISO8601(UTC, 초, Z)` 와 AI 시드 명령 줄을 남긴다. P·V 는 스타벅스 두 건 생성 순서(`DEMO_SEEDS` 순서와 짝), R 은 매운맛 방, `now` 는 시드 게시물 중 가장 이른 `created_at`(재실행해도 같음). 사용자는 기존 Supabase Auth id 4개를 `GEOJI_SEED_USER_IDS` 로 받는다(첫째 데모 C) | W6 seed |
| `generation-failed` 오류 코드 표 | 8종 그대로 채택 | W3 begin-failed |
| 내부 API 상태 기계(§4.3·§4.6) | 가짜 백엔드와 같고 다른 점 4가지(§8) | W3 begin-failed |
| 거부 응답 본문 | `{"code"}` 하나, 이름 확정(§4.1) | W1·W3 |
| CaseSnapshot | 평면 구현, RETAIN 확장 구현(`banter_strategy` null). comment 경로도 채움("채울 수 있는가" → 채움) | W3 internal-read·W5 post-comments |

## 7. §0.1 할 일 반영 제안

백엔드가 구현을 끝내 `반영` 으로 바꿔도 되는 §0.1 줄.

| §0.1 줄 | 구현 | 출처 |
| ------- | ---- | ---- |
| 양방향 서비스 인증 | 내부 API Bearer 검증·설정 빈 값이면 401 | W1 infra PR 없음(`bff8514`) |
| 9/11 `ai.jobs` 권한 | 컨테이너 테스트로 확인 | W2 jobs |
| 9/11 job INSERT 규약 | 5지점 dedupe·priority·max_attempts·deadline·payload 가 10 §3·`JOB_ROUTES`·`contracts/jobs.py` 와 같다 | W2 jobs |
| 9/11 RETAIN payload version | `version` = `verdict_version` | W2 jobs |
| 9/11 `guilty_ratio` 0..1 | 구현(considering 은 기각 비율) | W3 internal-read |
| 9/11 `reason_source`·`source` 필수 | finalize 스키마 검증 | W3 finalize #3 |
| 9/11 길이·배열 상한 | finalize-v1 대로 | W3 finalize #3 |
| CaseSnapshot 평면 | 구현 | W3 internal-read #8 |
| RETAIN 확장 | 구현(`banter_strategy` null·comment W5) | W3 internal-read #8 |
| RETAIN 삭제 404 | 구현 + 범위 확장(§3) | W3 internal-read #8 |
| 9/14 `draft_hash` canonical 규칙 | 백엔드 재계산 일치 | W2 pure·W3 finalize |
| 9/14 `meme_hints.emotion` 6종 | 밖이면 422, 짤 점수 +2 대조 | W3 finalize #3 |
| 9/14 TEXT_RETRY 강도 집합 | 구현 | W3 finalize #3 |
| 9/14 D-24 SENTENCE 게이트(두 줄) | 구현 | W3 jury #4 |
| 9/14 D-26 진행 중 job 끄기(두 줄) | 구현 | W2 jobs·W3 privacy #6 |
| intake 실제 모델 | 백엔드 호출부 구현 | W3 submissions #5 |
| `{"code"}` 최상위 | intake·trace 모두 최상위에서 읽음 | W3 submissions #5·trace #7 |
| `submission_id` 에코 없음 | 구현 | W3 submissions #5 |
| `FINAL_CHECK` 는 NEEDS_CLARIFICATION 안 냄 | 와도 UNCLARIFIED 등록 | W3 submissions #5 |
| 서기 전부 시간 초과 → `DEADLINE_EXCEEDED` | 2행 처리 | W3 begin-failed #2 |
| TEXT_RETRY round 안 실패는 저장 없이 generation-failed 만 → 다음 round 예약은 백엔드 | 구현. generation-failed 경로 + reaper FAILED(`LEASE_EXPIRED`) 경로 모두 다음 round 예약, TEXT_RETRY INSERT 는 5분 스캔 | W3 begin-failed #2·W4 schedulers |
| 9/14 watchdog 뒤 이전 job 상태 → `CANCELLED` 인지 `complete` 인지 회신 | `CANCELLED` 구현 | W4 schedulers |
| §6 deadline watchdog 250ms | 1~6단계 구현, D-24 보류는 대상 아님 | W4 schedulers |
| 9/14 D-26 삭제 트랜잭션에서 진행 중 job CANCELLED | 공개 삭제 API `DELETE /api/posts/{id}` 로 호출 가능 | W4 public-api |
| §9 판결 조회·폴링·공유 카드 | 구현(`/api`·camelCase·null 허용 차이는 §3·Q12) | W4 public-api |
| 9/14 RETAIN snapshot 확장 필드(comment 부분) | 구현. `comment{comment_id, version, room_id, post_id, post_status:"JUDGED", author_id, content, created_at}`. JUDGED 아님·댓글 삭제·게시물 삭제·댓글 방 철회면 404 `NOT_FOUND` | W5 post-comments |
| §8 댓글 삭제 무효화 | 구현. `post:{post_id}` epoch +1 → 같은 트랜잭션 `deleted_at` → `privacy_invalidations ('post:{id}', 'COMMENT', comment_id)` | W5 post-comments |

## 8. 가짜 백엔드와 다른 동작

AI 저장소 `tests/fakes/backend_app.py` 와 실제 백엔드가 다른 곳. 10 이 이기는 규칙에 따라 백엔드는 10 을 따랐다.

1. **begin-generation 재호출:** 같은 현재 generation 재호출도 요청 job 이 RUNNING ∧ generation 일치 ∧ lease 유효여야 하고, 요청 job 의 `aggregate_id` 가 이 verdict id 여야 한다(가짜는 재호출에서 생략, aggregate 대조 없음)
2. **kind 불일치:** PENDING 에 TEXT_RETRY job, FINAL 에 SENTENCE job 이 begin 하면 409 `STALE_GENERATION`(§4.3 둘째 문장, §13 작업 8)
3. **FINAL(TEXT_RETRY) begin 응답 `deadline_at`:** verdict 마감이 아니라 **TEXT_RETRY job 의 `deadline_at`(INSERT + 20s)** — 워커가 `Deadline.from_db` 로 예산을 잡으므로(가짜는 verdict 마감)
4. **TEXT_RETRY 중 실패:** 코드와 무관하게 다음 round 예약(§4.6 표 3행). 가짜는 1행 코드면 예약 안 함

## 9. 진행 현황과 출처

| 웨이브 | 작업 | 머지 | 10 절 |
| ------ | ---- | ---- | ----- |
| W1 | feat-infra — 서비스 토큰 인증·`{code}` 거부 본문·Testcontainers | `d52117e` | §4.7·§16 |
| W2 | feat-jobs — ai.jobs INSERT 5종·lease 검증·reaper·D-26 job 끄기 | `0f94808` | §3·§4.1·§7·§8 |
| W2 | feat-schema — 004 업무 테이블·ai 3테이블·엔티티·잠금 순서 | `cbb4c65` | §2 |
| W2 | feat-pure — draft_hash·템플릿·짤 점수·양형 밴드·배심 집계 | `8b660a5` | §3·§5·§10·§11 |
| W3 | feat-begin-failed — begin-generation·generation-failed·폴백 | PR #2 | §4.3·§4.6 |
| W3 | feat-finalize — finalize 12단계·짤 고정 | PR #3 | §5·§11 |
| W3 | feat-jury — 평결 확정·D-24 게이트 | PR #4 | §3·§6 |
| W3 | feat-submissions — 제출·complete·intake·PREPARE | PR #5 | §3·§4·§9 |
| W3 | feat-privacy — 삭제·철회 epoch·D-26·무효화 스케줄러 | PR #6 | §8 |
| W3 | feat-trace — trace 조회 프록시 | PR #7 | §4.5 |
| W3 | feat-internal-read — snapshot·resolve-evidence | PR #8 (`45321e7`) | §4.1·§4.2 |
| W4 | feat-schedulers — watchdog·TEXT_RETRY round | 머지 PR #10 `b7c0dea` | §6·§7 |
| W4 | feat-public-api — 투표·판결 조회·공유 카드·삭제 | PR #11 (`21a0792`) | §8·§9 |
| W4 | fix-intake-auth — intake 401·403 FALLBACK | PR #9 (`bf26901`) | §4 |
| W4 | fix-watchdog-held-flake — D-24 보류 테스트 경합(테스트만) | PR #12 (`3191869`) | §6 |
| W5 | feat-post-comments — 게시물 댓글·RETAIN comment·댓글 삭제 무효화 | PR #13 (`834f263`) | §3·§4.1·§8 |
| W6 | feat-seed — 데모 시드(seed 프로필, 짤 시드 없음) | PR #14 (`047b828`) | §12·§16.3 |
| W6 | chore-remove-trial — 기존 동기 재판 경로 제거 | 보류(프론트 전환 확인 전) | §15.2 |

main 게이트(Testcontainers 포함 `./gradlew build`) 마지막 결과: 471 테스트 실패 0(`047b828`).
