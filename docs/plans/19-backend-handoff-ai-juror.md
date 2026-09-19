# 🤝 [Handoff] 백엔드 전달: 데모 AI 배심원 떼거지봇 — `JURY_VOTE` 잡 · jury-votes 내부 API · ai-member 공개 API (9/20)

> 짝 문서: **18**(AI 쪽 명세). 상시 계약(인증·헤더·거부 본문·finalize·배포)은 **10**. 이 문서는 9/20 에 새로 생긴 백엔드 몫만 담는다. 9/19 까지의 전달 사항은 전부 반영돼 `archive/10-backend-contract-2026-09-19.md` 로 보관했다.
> 한 줄 요약: 방에 '데모용 AI 유저 추가' 를 누르면 고정 AI 사용자 **떼거지봇**이 멤버가 되고 템플릿 글 2개가 올라가며, 이후 그 방에 사람이 글을 올리면 워커가 LLM 으로 표·사유를 만들어 **봇 명의로 투표**한다. 백엔드는 봇 계정, 잡 INSERT, 표를 넣는 내부 API, 버튼용 공개 API 를 만든다.

## 0. 백엔드가 할 일 체크리스트

상태는 `미전달` → `전달 9/NN` → `반영`. 바꾸는 것은 사용자다.

| # | 할 일 | 절 | 상태 |
|---|---|---|---|
| 1 | Supabase auth 사용자 **떼거지봇** 1개 + `profiles` 행(닉네임 `떼거지봇`, `monthly_budget` 기본값). id 를 서버 환경변수 **`GEOJI_AI_JUROR_USER_ID`** 로 | §2 | 미전달 |
| 2 | `JobKind.JURY_VOTE(60, 2, null)` + `PostCreator.create` 에서 조건에 맞는 방마다 `ai.jobs` INSERT | §3 | 미전달 |
| 3 | `GET /internal/v1/ai-jobs/{job_id}/snapshot` 이 `JURY_VOTE` job 도 받게(PREPARE 와 같은 모양) | §4 | 미전달 |
| 4 | 새 내부 API `POST /internal/v1/posts/{postId}/jury-votes` → `PostVoteService.cast(postId, 봇, …)` | §5 | 미전달 |
| 5 | 새 공개 API `POST /api/rooms/{roomId}/ai-member` — 봇 멤버 추가 + 템플릿 글 2개, 멱등 | §6·§7 | 미전달 |
| 6 | 테스트 DDL 복사본 `001_ai_jobs.sql` 의 `kind` CHECK 에 `'JURY_VOTE'`(운영 DB 는 AI 마이그레이션 006 이 바꾼다) | §8 | 미전달 |

AI 파트가 할 일(참고): 006 을 운영 DB 에 적용(②를 배포하기 **전에**), 새 이미지 태그 전달. 프론트 버튼은 18 §2.

## 1. 흐름

```
사람이 글 등록 ─▶ PostCreator: posts + post_rooms + PREPARE job + (봇이 멤버인 방마다) JURY_VOTE job   ← 같은 트랜잭션
                                                        │
워커(JURY 슬롯) claim ─▶ snapshot(jury=null) ─▶ 방 강도 ─▶ LLM(juror) 1회 ─▶ 검증 실패면 템플릿 표
                                                        │
                    POST /internal/v1/posts/{postId}/jury-votes ─▶ PostVoteService.cast(postId, 봇, {verdict, reason, roomId})
                                                        │
                    사람 1 + 봇 1 방이면 정족수 min(2, 가능 인원)=1 → 그 자리에서 평결 확정 → D-24 게이트 → SENTENCE → 판결문
```

- 봇 글(템플릿 2개)은 사람이 한 표 던지면(가능 인원 1) 확정된다. 봇은 자기 글에 투표하지 않는다(작성자 제외, INSERT 조건에서도 뺀다)
- 사람 표와 완전히 같은 검증·정족수·확정 경로를 탄다. 봇을 위한 별도 tally 규칙은 없다

## 2. 떼거지봇 계정 · 환경변수

- 고정 1명, 전 방 공용. Supabase auth 사용자 1개를 관리자가 만들고 `profiles` 행을 넣는다(온보딩 API 를 봇 JWT 로 부르기 어렵면 시드 러너의 `insertProfileIfAbsent` 와 같은 쿼리로)
- 서버 환경변수 `GEOJI_AI_JUROR_USER_ID`(UUID). 비어 있으면 §3 INSERT 를 하지 않고 §5 는 403, §6 은 503
- AI 쪽 코드는 이 id 를 모른다. job payload 의 `voter_id` 를 그대로 되돌려 보낸다

## 3. `ai.jobs` INSERT — `JURY_VOTE` (10 §3 표에 더하는 행)

| 업무 트랜잭션 | kind / event_type | dedupe_key | priority | max_attempts | deadline_at | payload | aggregate_id / version |
|---|---|---|---:|---:|---|---|---|
| 게시물 저장 — 공유 방 중 **봇이 멤버이고 작성자가 봇이 아닌 방마다 1개**, PREPARE 와 같은 트랜잭션 | `JURY_VOTE` / `jury.vote_requested` | `jury-vote:{post_id}:{room_id}:{voter_id}` | 60 | 2 | null | `{post_id, post_version, room_id, voter_id}` — 네 키 모두 필수, `voter_id` 는 봇 id | `post_id` / `post_version` |

- payload 에 알 수 없는 키가 있으면 워커가 거부한다(10 §3 규칙 그대로)
- `deadline_at` 은 NULL. 폴백(템플릿 표)은 워커 안에서 하므로 watchdog 대상이 아니다
- `JobKind.JURY_VOTE(60, 2, null)`. 운영 `ai.jobs.kind` CHECK 는 AI 마이그레이션 006 이 넓힌다(§8) — **006 적용 전에 INSERT 하면 CHECK 위반**

## 4. snapshot — `JURY_VOTE` 도 같은 응답

`GET /internal/v1/ai-jobs/{job_id}/snapshot`(10 §4.1)이 `JURY_VOTE` job 을 받으면 **PREPARE 와 같은 `CaseSnapshot`** 을 준다.

- `jury = null`. `room_snapshots`·`audience.room_ids`·`privacy_versions` 는 공유 방 **전부**(판결 방 하나가 아니다)
- 워커가 `payload.room_id` 로 방을 고른다. 그 방이 `room_snapshots` 에 없으면(공유 철회) 모델을 부르지 않고 job 을 complete 한다
- 검증(job 존재 ∧ `RUNNING` ∧ `X-Generation-Id` 일치 ∧ lease 유효, 아니면 409 `STALE_GENERATION`)과 삭제 404 는 기존과 같다

## 5. 내부 API `POST /internal/v1/posts/{post_id}/jury-votes`

워커가 `JURY_VOTE` job 하나당 한 번 부른다. 백엔드는 **`PostVoteService.cast(postId, voter_id, PostVoteRequest(verdict, reason, room_id))`** 를 실행한다 — 사람 표와 같은 검증·정족수·평결 확정·D-24 게이트를 탄다.

- 인증·헤더 5종·거부 본문 `{"code"}` 는 10 §4.7 그대로. 워커 read timeout 2초, 4xx 재전송 없음, 5xx 는 같은 바이트 최대 2회
- 요청 본문 `{job_id, generation_id, room_id, voter_id, verdict, reason, source}`. `verdict` 는 `spent` 글이면 `guilty|notGuilty`, `considering` 이면 `agree|disagree`. `reason` 은 1~60자(워커가 보장, DB CHECK 는 500). `source` 는 `"AI"|"TEMPLATE"`
- 응답 **201** `{"vote_id": "<uuid>"}`

검증 순서와 거부 코드:

| 순서 | 조건 | 응답 |
|---|---|---|
| 1 | job 존재 ∧ `RUNNING` ∧ `X-Generation-Id`(=본문 `generation_id`) 일치 ∧ lease 유효 (§4 와 같음) | 아니면 409 `STALE_GENERATION` |
| 2 | `voter_id` == `GEOJI_AI_JUROR_USER_ID` | 아니면 403 `NOT_AI_JUROR` (워커가 남의 표를 넣지 못하게. 설정이 비어 있어도 403) |
| 3 | 글 존재 ∧ 삭제 안 됨 | 404 `NOT_FOUND` |
| 4 | 마감 전 ∧ 그 방 평결 미확정 ∧ 공유 철회 안 됨 ∧ 봇이 그 방 멤버 | 아니면 409 `VOTING_CLOSED` |
| 5 | 아직 안 투표 | 이미 투표 409 `ALREADY_VOTED` |
| 6 | `verdict` 가 글 유형에 맞음 ∧ `reason` 비어 있지 않음 ∧ 500자 이하 | 아니면 422 `INVALID_REQUEST` |
| 7 | `cast` 성공 | 201 |

- 워커 처리: 201·`VOTING_CLOSED`·`ALREADY_VOTED`·`STALE_GENERATION` → job complete / 404 → cancel / `NOT_AI_JUROR` → fail(재시도 없음, attempts 소진 뒤 운영 알림) / 401 → `BACKEND_AUTH`(60초 뒤 재시도)
- 응답 유실 뒤 재전송은 `ALREADY_VOTED` 409 로 받고 워커는 성공으로 본다. 별도 commit record 는 없다
- `source` 는 로그·집계용이다. `votes` 에 저장 컬럼이 없으면 버려도 된다(§10 미결). 표는 `source` 와 무관하게 들어간다 — **모델이 죽어도 표는 들어간다**(사용자 결정)

## 6. 공개 API `POST /api/rooms/{roomId}/ai-member`

'데모용 AI 유저 추가' 버튼이 부른다. 요청자(JWT)가 그 방 멤버여야 한다. 본문 없음.

1. `room_members(roomId, 봇)` 없으면 추가
2. 봇 명의 템플릿 글 2개(§7)가 그 방에 없으면 `SubmissionService.submit(봇, …, roomIds=[roomId])` → `NEEDS_INPUT` 이면 `complete(PROCEED)`(시드 러너 `findOrSubmit` 과 같다). 봇 글도 사람 글과 같은 PREPARE 를 탄다. 봇 글에는 `JURY_VOTE` 를 만들지 않는다(작성자 == 봇)
3. 응답 `201 {"userId": "<봇 id>", "nickname": "떼거지봇", "postIds": ["<spent>", "<considering>"]}`. 이미 다 있으면 `200` 같은 모양(멱등)

| 상황 | 상태 코드 | 바디 |
|---|---|---|
| 방 없음 · 요청자가 멤버 아님 | 404 | `{"message": "방을 찾을 수 없습니다."}` |
| `GEOJI_AI_JUROR_USER_ID` 미설정 | 503 | `{"code": "AI_JUROR_NOT_CONFIGURED"}` |
| JWT 없음 | 401 | - |

## 7. 템플릿 글 2개 (시드 재사용)

| postType | amountKrw | category | item | reason |
|---|---:|---|---|---|
| `spent` | 32000 | 교통/택시 | 심야 택시 | 막차가 끊겨서 어쩔 수 없었어요 |
| `considering` | 189000 | 쇼핑/패션 | 무선 이어폰 | 기존 이어폰 한쪽이 안 들려요 |

- 이미 심문관 검증을 지난 시드 값이라 질문 없이 등록될 가능성이 크다. 질문이 나와도 `PROCEED`
- 멱등 판단은 시드 러너처럼 `(author=봇, room, postType, amount, category, item, reason)` 일치로

## 8. 마이그레이션 006 · 배포

- **006(AI 소유, AI 가 적용)**: `ALTER TABLE ai.jobs DROP CONSTRAINT jobs_kind_check; ADD CONSTRAINT jobs_kind_check CHECK (kind IN ('PREPARE','SENTENCE','TEXT_RETRY','RETAIN','JURY_VOTE'));` `DATABASE_URL=<Session Pooler URL> uv run geoji-ai migrate` 1회. 순서: **006 적용 → 새 AI 이미지 배포 → 백엔드 ②~⑤ 배포**
- 백엔드 저장소의 테스트 DDL 복사본 `src/test/resources/db/001_ai_jobs.sql` 은 백엔드가 직접 고친다(체크리스트 ⑥)
- AI 이미지: 워커 슬롯 기본값에 `"JURY": 1` 이 늘었다. **`.env` 에 `WORKER_SLOTS` 를 직접 적어 두었다면 `JURY` 를 더해야** 봇이 투표한다. 안 적었으면 할 일 없음. 새 키 `JUROR_TIMEOUT_SECONDS`(10)·`JUROR_MAX_OUTPUT_TOKENS`(120)는 기본값으로 둔다
- 프롬프트 파일이 늘어 `prompt_bundle_version` 이 바뀐다. 새 태그와 대조값은 이미지 전달 때 이 표에 적는다: 태그 `____`, `prompt_bundle_version` `____`
- 운영 확인: 사람 1 + 봇 방에서 글 등록 → 5~10초 뒤 피드에 떼거지봇 표 → 평결 확정 → 판결문. 워커 로그 `docker logs geoji-ai-worker | grep jury_vote_summary` 의 `outcome` 이 `AI`(모델) 또는 `TEMPLATE`(폴백)

## 9. 백엔드 수용 검사

| 케이스 | 기대 |
|---|---|
| 봇이 멤버인 방 A·아닌 방 B 에 글 공유 | `JURY_VOTE` job 1개(A 만), PREPARE 1개 |
| 봇이 자기 템플릿 글 등록 | `JURY_VOTE` 0개 |
| 같은 글 두 번 등록 시도(중복 완료) | job 1개(dedupe) |
| jury-votes 에 사람 id 로 `voter_id` | 403 `NOT_AI_JUROR`, 표 없음 |
| jury-votes 재전송(같은 job) | 두 번째는 409 `ALREADY_VOTED`, 표 1개 |
| 마감 지난 글에 jury-votes | 409 `VOTING_CLOSED` |
| 사람 1 + 봇 1 방, 봇 표 도착 | 같은 요청 안에서 평결 확정, SENTENCE 게이트 진입 |
| ai-member 두 번 | 두 번째 200, 멤버 1행·글 2개 그대로 |
| `GEOJI_AI_JUROR_USER_ID` 미설정 | ai-member 503, INSERT 0개 |

## 10. 미결 (백엔드 회신 필요)

| ID | 항목 | 기한 |
|---|---|---|
| 봇 계정 | 떼거지봇 auth 사용자를 누가 만드나(백엔드 관리자 콘솔). id 는 AI 코드에 필요 없고 운영 확인 때만 | 9/20 |
| `source` 저장 | `votes` 에 `source`(AI/TEMPLATE) 를 저장할지. 저장하면 프론트가 "AI 표" 라벨을 붙일 수 있다. 안 저장해도 동작에는 영향 없음 | 9/20 |
| 방 목록의 봇 표시 | 방 상세·멤버 응답에 "떼거지봇 있음" 플래그를 줄지. 없으면 프론트가 멤버 닉네임으로 판단(18 §2) | 9/20 |
