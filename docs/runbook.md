# 떼거지 AI 파트 운영 runbook

심사 기간(9/21~10/5) 운영과 9/20 동결 절차. 정본은 `docs/plans/08-failure-recovery-rehearsal.md` §3.6 이다.
**이 문서는 새 값을 정하지 않는다.** 값은 가리키는 곳이 정본이다.

| 무엇 | 정본 |
|---|---|
| 환경변수 키 이름 | `.env.example` |
| 환경변수 기본값·설명 | `README.md` "설정" 표 |
| 계약(내부 API·finalize·watchdog·round) | `docs/plans/10-backend-contract.md` |
| 관측 지표·알림 조건 | 08 §3.3 |
| 장애 재현·리허설 | 08 §3.1·§3.5 |
| 백엔드에 넘긴 것 | `docs/plans/10-backend-contract.md` §0.1·§16 |

비밀값(`DATABASE_URL`·`SERVICE_AUTH_TOKEN`·`OPENAI_API_KEY`·`XAI_API_KEY`·`ALERT_DISCORD_WEBHOOK_URL`)은
이 문서·로그·채팅에 원문을 적지 않는다. 키 이름만 쓴다.

## 1. 키 · 결제 잔액

- 벤더 키: `OPENAI_API_KEY`(양형·검수·조서), `XAI_API_KEY`(서기·드립). 역할 → 벤더는 README "설정" 표
- 잔액 기준: 심사 14일 × 일 200건 × 건당 상한 40원 ≈ 11만 원 여유(08 §3.6). 건당 상한은
  `docs/plans/00-INDEX.md` §1 "비용" 행
- [ ] 심사 시작 전 두 벤더 콘솔에서 잔액이 위 기준 이상인지 확인
- [ ] 키가 운영 환경에만 있고 `.env` 가 git 에 없는지 확인

## 2. 비용 경고와 대응

- 경고 기준: `COST_ALERT_KRW_PER_DAY`(값은 README "설정" 표, 결정 08 §3.3). `GEOJI_EVAL=1` 평가 실행분은
  별도 집계
- 초과 알림을 받으면 다음 두 값을 낮추고 워커를 재기동한다(08 §3.6):
  - `IMMEDIATE_REPAIR_MAX=0`
  - `TEXT_RETRY_ROUNDS=1`
- [ ] 낮춘 시각·이유를 기록한다
- (초안, 계획서에 절차 없음) 되돌리는 조건은 정해지지 않았다. 원래 값은 README "설정" 표

## 3. 헬스체크

| 엔드포인트 | 언제 | 누가 |
|---|---|---|
| `GET /health/live` | 5분마다 | 백엔드 모니터링(08 §2 요청 표) |
| `GET /health/ready` | 배포 직후 1회 | 배포한 사람 |

- 응답 모양은 README "실행 절차"(`ready` 는 키·`DATABASE_URL`·DB 연결이 안 되면 503)
- [ ] 배포 직후 `/health/ready` 가 200 인지 확인. 503 이면 응답의 `missing`·`db` 를 보고 키·접속부터 본다

## 4. 추적 · 관측

- 로그는 JSON 이고 `trace_id` 로 백엔드 로그와 잇는다. 백엔드 → AI 호출 헤더 `X-Trace-Id`(10 §4)
- 로그에 원문 사유·댓글·인증 헤더는 없다. 해시·개수·코드만 있다(08 §3.3)

지표 스냅샷과 사건 trace(서비스 인증, 08 §3.3·§4.3). 포트는 README "실행 절차"의 로컬 값이다.

```bash
curl -s localhost:8100/internal/v1/metrics/snapshot -H "Authorization: Bearer $SERVICE_AUTH_TOKEN" \
  | jq '.first_result_latency_seconds, .template_first_rate, .case_cost_micro_usd'
curl -s localhost:8100/internal/v1/trials/$POST_ID/trace -H "Authorization: Bearer $SERVICE_AUTH_TOKEN" | jq .
```

- 알림 채널: 디스코드 웹훅 `ALERT_DISCORD_WEBHOOK_URL`. 알림 조건 4종은 08 §3.3(보정 가능한 오류 1건마다
  보내지 않는다)

### 판결문이 기본 문구로 나오거나 생성이 끝나지 않을 때

먼저 실행 중인 worker의 로그를 확인한다. `TEMPLATE_READY`는 대체 문구 저장이며 실제 모델 생성
성공을 뜻하지 않는다. 저장소 테스트 통과만으로 운영 모델·키·백엔드 연결 상태를 확인할 수 없다.

```bash
docker compose -f docker-compose.prod.yml logs --since 30m ai-worker \
  | rg 'sentence_summary|sentence_fallback|sentence_call|HANDLER_ERROR|ledger_reserve_failed'
```

같은 `job_id`·`generation_id`의 `sentence_call` → `sentence_fallback` → `sentence_summary`를
따라가며 최초 실패 역할과 코드를 확인한다.

| 기록 | 확인할 것 |
| --- | --- |
| `AI_NOT_READY` | worker에 두 모델 키가 모두 없는지 확인. API와 worker 설정은 각각 확인한다 |
| 서기 `NO_BUDGET`·`DEADLINE_EXCEEDED` | 백엔드 마감과 AI 노드 상한이 맞는지 확인. 현재 기본값은 SENTENCE 90초·TEXT_RETRY 60초이며, 옛 10초 마감이면 검수 예약 30초 때문에 서기를 시작하지 못한다 |
| `AUTH`·`TRANSPORT`·`TIMEOUT` | 해당 역할의 벤더 키·연결·시간 제한 확인. 키 원문은 공유하지 않는다 |
| `BUDGET_EXCEEDED` | `llm_budget_exceeded`의 예약액과 사건 상한을 확인. 별도 검수 모델을 고가 모델로 바꾸면 기본 출력 토큰 상한에서도 호출 전에 차단될 수 있다 |
| `EVAL_FAILED` | `sentence_fallback`의 상세 이유 확인. 검수 거부, 불완전 응답, 전 강도 템플릿을 구분한다 |
| 서기 `SCHEMA_INVALID` | v5.6 카드 규격은 제목 20자·본문 1항목 30자이며 줄바꿈·공백값을 금지한다. INITIAL은 남은 시간·기존 재작성 예산 안에서 1회 보정하며, 불합격 응답은 캐시하지 않는다. TEXT_RETRY 회차 안 보정은 없다 |
| finalize `422`·`SCHEMA_INVALID` | 백엔드의 계약·정책 버전과 AI 설정을 비교한다 |

9/17 카드 규격 배포 전에는 백엔드 본문 최소 개수 2→1과 공유 템플릿 갱신이 필요하다(10 §5·§10).
서기 출력 토큰 상한 700은 그대로다. 제목·본문의 길이를 줄였으며 밈 선택·검수 메타데이터는 유지한다.
FakeLLM 회귀 통과는 실제 모델의 문구 품질·토큰 사용량·지연시간이나 실제 카드 줄바꿈을 검증하지 않는다.

현재 실행 환경의 서기 프롬프트·모델·토큰 상한은 다음 명령으로 모델 호출 없이 확인한다.
스크립트가 포함된 최신 이미지에서 실행해야 한다.

```bash
docker compose -f docker-compose.prod.yml exec ai-worker \
  python scripts/probe_verdict_cards.py --out /tmp/cards-dry.json
```

실제 세 강도 출력 확인은 같은 명령에 `--execute`를 추가한다(xAI 최대 3회 유료 호출).
키는 컨테이너 환경에서 읽으며 명령이나 채팅에 직접 넣지 않는다. `--out` 보고서의
`writer_version`·`prompt_bundle`·각 요청의 토큰/시간 상한을 배포 버전과 대조한다.
이 검사는 서기 단독이며, 양형·검수·백엔드 저장 성공은 별도 사건 trace로 확인한다.
`success`는 카드 형식 검사 결과이고 근거 라벨 검사 결과는 `evidence_validation`에 따로 기록한다.

9/17 회귀 테스트로 검수 결과 병합의 두 오류를 재현했다. `evidence_labels` 배열 때문에 동일 위반이
제거되지 않아 계약 상한을 초과하던 경로는 중첩 값 비교로 고쳤다. 별도 지옥맛 검수 응답에서 필수
검사가 null·누락이면 예외로 중단되던 경로는 `EVAL_FAILED`를 보고하도록 고쳤다. 이 증거만으로
개별 운영 장애의 원인을 단정하지 않으며, 해당 사건의 로그와 대조한다.

## 5. 원장 정리 (하루 1회)

`UNKNOWN` 호출의 예약액을 `spent` 로 확정(보수적)하고 리포트한다(08 §3.2). 백엔드 스케줄러 또는 cron.

```bash
uv run geoji-ai ledger-sweep --older-than 24h
```

- [ ] 하루 1회 실행 결과(호출 건수·micro-USD)를 기록

## 6. 롤백

| 대상 | 방법 | 정본 |
|---|---|---|
| 이미지 | 이전 이미지 태그(`ai-YYYYMMDD-N`)로 재배포. 목표 ≤ 5분 | 08 §3.6 |
| 프롬프트 | `PROMPT_BUNDLE_VERSION` 을 기준선으로 되돌림 | 08 §3.6, 06 §3.3 |
| 정책 | `GUARDRAIL_POLICY_VERSION=guardrail-v1` | 08 §3.6 |

- [ ] (초안, 계획서에 절차 없음) 배포할 때마다 직전 이미지 태그를 기록해 둔다(롤백 대상)
- [ ] 롤백 뒤 `/health/ready` 200 확인(§3)

## 7. 동결 (9/20)

- 9/20 부터 **핫픽스만** 배포한다(08 §3.6)
- 프롬프트·정책 변경도 동결한다. 골든셋 회귀 없이 배포하지 않는다(`testing` 룰 "프롬프트 변경")
- [ ] 동결 태그를 찍는다(사람 몫)
- [ ] 동결 뒤 핫픽스는 PR 에 무엇을 왜 고쳤는지와 회귀 결과를 남긴다

## 8. 비밀값 회전

- 대상: `SERVICE_AUTH_TOKEN`(백엔드와 공유), 벤더 키, `ALERT_DISCORD_WEBHOOK_URL`
- `.env` 는 git 에 넣지 않는다. 운영 값의 전달 경로는 `docs/plans/10-backend-contract.md` §16.1
- 아래 두 절차는 **초안**이다. 계획서(08 §3.6)에는 "`SERVICE_AUTH_TOKEN` 회전" 이름만 있고 절차·겹침 기간은 정해지지 않았다
- [ ] `SERVICE_AUTH_TOKEN` 회전: 백엔드와 교체 시각을 맞춘다 → 양쪽 설정을 같은 새 값으로 바꾼다 →
  API·워커 재기동 → 내부 호출이 401 없이 도는지 로그로 확인
- [ ] 벤더 키 회전: 새 키로 교체 → 재기동 → `/health/ready` 200 → 옛 키 폐기

## 9. 장애 리허설 체크리스트 (08 §3.5)

A·B·C 는 3회, 장애·예산·삭제는 각 1회(08 §4.1). 매회 `first_result_latency`·비용을 기록한다.
기록 칸은 리허설 때 채운다.

자동 재현 테스트(가짜 벤더, 시간 축소)는 먼저 통과해 있어야 한다.

```bash
TEST_DATABASE_URL=... uv run pytest tests/integration/test_failures.py -q
```

| 시나리오 | 절차 | 확인 | 목표 | 1회 | 2회 | 3회 |
|---|---|---|---|---|---|---|
| A 무엇을 심문 | "감각적 쾌락 추구" 등록 → 솔직 팝업 → 수정(`FINAL_CHECK`) → 등록 | 팝업 1회, 중복 post 없음, PREPARE job 1개 | intake ≤ 2.5초 | | | |
| B 거지방식 판결 | 30분 방, 3명 투표 **`disagree`(부결)** → 판결 화면 → 공유 카드 | 양형 0호출·서기+검수, `texts` 방 강도 행, 짤 선택, 카드 `PUBLIC` 근거만 | 마지막 표 → 첫 저장 ≤ 6초 | | | |
| C 개인화 | 스타벅스 3번째 → 판결 → trace 화면 | `PRIOR`·반복 AGGREGATE 라벨 인용, recall 출처 표시 | ≤ 8초 | | | |
| 장애 1 xAI | `XAI_API_KEY` 무효 → 유죄 | 형량 AI 정상, 문구 TEMPLATE, `VENDOR_UNAVAILABLE`, round1 예약 | ≤ 10초 | | — | — |
| 장애 2 OpenAI | `OPENAI_API_KEY` 무효 | 양형 RULE, 문구 TEMPLATE(검수 불가) | ≤ 10초 | | — | — |
| 예산 초과 | `WRITER_NODE_TIMEOUT_SECONDS=0.1` | `DEADLINE_EXCEEDED`/TEMPLATE, 응답 ≤ deadline+0.5s | | | — | — |
| 삭제 | 판결 뒤 게시물 삭제 | 조회 즉시 차단·템플릿, retry 저장 0, `invalidated_evidence_total` +1 | 즉시 | | — | — |

- 시나리오 B 는 `dismissed`(정족수 미달)가 아니라 `disagree`(부결)다(08 §3.5)
- 장애 1 리허설 인스턴스: `XAI_API_KEY=invalid uv run geoji-ai worker &`(08 §4.3)
- 예산 초과 리허설 전에 설정이 그 값을 받는지 먼저 확인한다(자동 테스트 `test_07a` 참고)

기록:

| 항목 | 값 |
|---|---|
| `first_result_latency` p95 | |
| 폴백률 | |
| 누적 비용 | |

실측 값은 `docs/plans/00-INDEX.md` §7 에도 옮긴다(08 §4.1, 사람 몫).

## 10. 데모 시드

백엔드 시드(10 §12)가 끝나고 데모 C 사용자의 스타벅스 2건 `post_id` 를 받은 뒤 돌린다. 두 번 돌려도
행이 늘지 않는다.

```bash
uv run scripts/seed_agent_db.py \
  --banter-csv pairs.csv --banter-candidates candidates.jsonl \
  --demo-user $U --demo-room $R --post-ids $P1,$P2
```

- 인자가 없는 단계는 건너뛴다. 접속은 `--url` 또는 `DATABASE_URL`
- `meme_catalog` 은 우리 DDL 에 없어 만들지 않는다. 짤 메타는 백엔드 `meme_images` 소유다(10 §11)
