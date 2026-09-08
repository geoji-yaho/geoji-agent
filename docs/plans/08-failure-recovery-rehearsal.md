# 🛠️ [Tech Spec] 기술 명세서: 작업 8 — 장애 복구 · 재시도 round · 관측·알림 · 짤 메타 · 데모 시드 · 리허설 · 동결 (M4 9/16~9/18)

> 근거: proposal2 §8.3 lease·재실행, §11 마감·재시도·삭제 경합(watchdog·round·epoch), §14.2 async 주의, §15 실패 처리·벤더 분리, §17 강도·공개 범위·짤, §18 평가·관측·알림, §19 실행 단위, §20 작업 8 + 먼저 실패시킬 케이스(9초 시점 worker 종료 · 템플릿 이후 늦은 성공 · retry 중 삭제 · backend commit 후 네트워크 끊김), 부록 B 데모 시나리오 / 기획서 §12 운영·§15 M4.
> **선행 문서: 02~07 전부.** 백엔드 몫(watchdog·round 예약·무효화·짤 선택·시드)은 `10-backend-contract.md` §6·§7·§8·§11·§12. 완료 기준(proposal2 §20): **형량 중복 확정·삭제 정보 재노출 0건.** 실제 p95·폴백률·누적 비용 기록.

## 1. 개요 및 구현 목표

### 목적:
- **장애 4종을 재현하고 정의된 폴백으로 끝나는지** 확인: 9초 시점 워커 종료(lease 만료 → reaper/watchdog), 템플릿 이후 늦은 성공(`STALE_GENERATION` 거부), retry 중 삭제(`EVIDENCE_INVALIDATED`·epoch), backend commit 후 네트워크 끊김(commit record 로 같은 결과, 모델 재호출 없음)
- `TEXT_RETRY` 핸들러 완성 — round 예산 20초, round 안 보정 없음, 실패 강도만, 성공 시 `texts`·`source`·`text_version` 만 변경. UNKNOWN 비용 정리 배치
- **관측**(proposal2 §18): `trace_id`·graph·prompt·`guardrail_policy_version`·vendor·`model_id`·노드 지연·조회 개수·recall ID·Evidence 라벨·전략·`attack_angle`·위반 코드·재생성·폴백·토큰·비용. 메트릭 라벨에 개별 ID 금지. 알림 4종
- 짤: 서기 `meme_tag`·`meme_hints`(감정 어휘 enum) → **백엔드 finalize 가 점수 선택**(10 §11). 우리는 점수 규칙 명세와 힌트 품질만
- **데모 시드 · 리허설 A·B·C ×3 · 벤더 장애 2종 · 예산 초과 · 삭제 시연**, runbook, 9/20 동결

### 핵심 플로우:
```
[장애 리허설]  워커 kill@9s ─ lease 만료(15s) ─ reaper → QUEUED (마감 전) / watchdog → 템플릿 (마감 후) ─ 늦은 finalize → 409 STALE_GENERATION
              retry 중 삭제 ─ epoch +1 ─ 워커 finalize 직전 검사 → EVIDENCE_INVALIDATED ─ 백엔드 조회는 즉시 템플릿
              commit 후 네트워크 단절 ─ 같은 finalize 재전송 → commit record 200 ─ 모델 재호출 0
[TEXT_RETRY]   round1 +5분 → round2 +10분 → round3 +20분 (백엔드 예약) ─ 워커: 고정 형량·이유, 실패 강도만, 20초, 보정 없음 ─ 성공: texts·source·text_version 만
[관측]         telemetry/logs.py (structlog JSON, hash·개수·코드) · telemetry/metrics.py (§18 표 9지표, 라벨 = kind/node/vendor/code) · 알림 4종
[시드·리허설]  scripts/seed_agent_db.py (우리 DB) + 백엔드 시드(실제 파이프라인 통과) → 리허설 표 3회 기록 → runbook → 9/20 동결 태그
```

## 2. 작업 범위 (Scope Boundary)

### In-Scope
| # | 항목 | 등급 | 난이도 |
|---|---|:--:|:--:|
| 3.1 | 장애 4종 재현 테스트(실제 Postgres + 가짜 백엔드 + 실제 워커 프로세스) | Critical | M |
| 3.2 | `TEXT_RETRY` 핸들러 완성 + UNKNOWN 비용 정리 배치 | High | S |
| 3.3 | `telemetry/logs.py`·`metrics.py` — 필드·지표 9종·알림 4종 | High | M |
| 3.4 | 짤 힌트 품질(감정 enum) + 점수 규칙 명세 전달 | Medium | S |
| 3.5 | 데모 시드(우리 DB) + 백엔드 시드 요구 + 리허설 체크리스트 3회 | Critical | M |
| 3.6 | runbook · 비용 경고 · 동결 절차 | High | S |

### Out-of-Scope
- watchdog·round 예약·무효화 스케줄러·짤 점수 실행·시드 생성(백엔드, 10). 우리는 재현·검증·규칙 제공
- Prometheus/Grafana·LangSmith(P1, 09). `metrics.py` 는 JSON 스냅샷 + 로그 카운터까지
- Web Push 문구(백엔드·프론트, headline 재사용)

### 다른 파트에 요청 (백엔드·프론트)
| 대상 | 요청 | 기한 |
|---|---|---|
| 백엔드 | watchdog(250ms)·round 예약(5/10/20분)·`TEXT_RETRY` payload `intensities[]`·무효화 스케줄러 완성(10 §6·§7·§8) | 9/16 |
| 백엔드 | 짤 점수 선택을 finalize 에서(10 §11): `meme_images` 메타 컬럼, 최근 노출 감점, `meme_image_id` 고정, 후보 없으면 결과별 기본 이미지 | 9/17 |
| 백엔드 | 시드(10 §12): 방 3·사용자 4·게시물 12·댓글 20, 판결 10건은 **실제 파이프라인 통과**(≈ 250원). 데모 C 사용자의 스타벅스 2건 `post_id` 를 우리 시드에 전달 | 9/17 |
| 백엔드 | 우리 `/health/live` 를 5분 헬스체크에, 알림 채널 연결 | 9/18 |
| 프론트 | 결과별 기본 짤 5장, `view=null` 대기 메시지, 템플릿 노출 후 30초 폴링·재진입 갱신 | 9/18 |

### 팀 결정 대기
- 알림 채널·비용 경고 임계(초안 일 5,000원), LangSmith(P1), 짤 제작 담당·수량(10~15), 감정 어휘 6종 확정

## 3. 기술 상세 설계 (Technical Design)

### 3.1 장애 4종 재현 (`tests/integration/test_failures.py`, proposal2 §20 작업 8)
| 케이스 | 재현 | 기대 |
|---|---|---|
| 9초 시점 worker 종료 | SENTENCE 처리 중 t=9s 에 SIGKILL | lease 15초 만료 → (마감 전이면) reaper 가 `QUEUED`·재claim·새 generation / (마감 후면) watchdog 이 `FINAL(RULE)+TEMPLATE_READY`, job `CANCELLED`. 어느 쪽이든 **형량 FINAL 1회**, 이전 generation 의 finalize 는 409 |
| 템플릿 이후 늦은 성공 | 워커를 일부러 12초 지연 → watchdog 이 먼저 템플릿 | 늦은 finalize → `409 STALE_GENERATION` → 폐기·complete. 화면은 템플릿 유지, round1 이 5분 뒤 |
| retry 중 삭제 | `TEXT_RETRY` 진행 중 백엔드가 post 삭제(epoch +1) | 워커 finalize 직전 epoch 검사 → `EVIDENCE_INVALIDATED`, 저장 0. 백엔드 조회는 즉시 템플릿/차단. 무효화 후 `node_results` 재사용 0 |
| backend commit 후 네트워크 끊김 | 가짜 백엔드가 commit 뒤 응답을 끊음 | 워커 재전송(같은 본문) → commit record 로 200 동일 `text_version`. **모델 재호출 0**(`llm_calls` 건수 불변) |
- 추가: 벤더 한쪽 장애 2종(06 §4.2), 예산 초과(`WRITER_NODE_TIMEOUT_SECONDS=0.1`), 마지막 표 동시 도착(백엔드 테스트, 10 §13)

### 3.2 `TEXT_RETRY` 핸들러 · UNKNOWN 정리 (`application/sentence_case.py` `mode=REGENERATE`, proposal2 §11.2)
- `begin-generation`(템플릿이 이미 노출된 `FINAL` 상태에서만 시작) → 고정 형량·이유 → `load_valid_prep`(삭제된 prep 읽지 않음) → 서기(**payload `intensities[]` 만**) → ⑤ → ⑥ → finalize. 예산 20초, **round 안 보정 없음**(실패 = 다음 round), 형량 필드가 기존과 다르면 finalize 가 거부
- 성공: `texts`·`source`·`text_version` 만 변경, 이미지·형량·양형 이유 유지. 마지막 round 실패·예산 초과 → 템플릿 유지 + 운영 알림(백엔드)
- UNKNOWN 정리: `uv run geoji-ai ledger-sweep --older-than 24h` — `UNKNOWN` 호출의 예약액을 `spent` 로 확정(보수적)하고 리포트. 백엔드 스케줄러 또는 cron 하루 1회

### 3.3 관측 (`telemetry/logs.py`·`metrics.py`, proposal2 §18)
- 로그(JSON): `trace_id`·`job_id`·`generation_id`·`dossier_id`·`graph_name/version`·`prompt_bundle_version`·`guardrail_policy_version`·`vendor`·`model_id`·노드별 `latency_ms`·`ok`·조회 결과 개수·recall memory ID·Evidence 라벨·`banter_strategy`·`attack_angle`·위반 코드·`repair_count`·폴백 원인·토큰·`micro_usd`. **원문 대신 hash·개수·코드.** 인증 헤더·사유·댓글 원문 금지
- 지표(라벨에 개별 ID 금지):
| 지표 | 라벨 | 해석 |
|---|---|---|
| `queue_wait_seconds` | kind | 모델이 빨라도 슬롯·claim 지연일 수 있음 |
| `first_result_latency_seconds` p50/p95/p99 | path(guilty/other/regen) | 평결 확정 → 첫 저장. 프론트 노출과 구분 |
| `llm_duration_seconds` | node, vendor | 어느 벤더·노드가 느린지 |
| `template_first_rate`, `retry_recovery_rate` | — | 초기 품질 저하와 복구 |
| `evaluation_repair_rate` | intensity | 검수 재생성률 — 프롬프트 회귀 신호 |
| `stale_finalize_total`, `lease_expired_total` | kind | 중복·워커 종료·heartbeat 지연 |
| `evaluation_failure_total` | code | 정책 위반·근거 오류·강도 문제 |
| `case_cost_micro_usd`, `unknown_calls` | vendor | 실지출·과금 불확실 |
| `invalidated_evidence_total` | — | 삭제·공유 변경 후 오래된 입력 사용 시도 |
- 알림 초기안(백엔드 채널로): 5분간 queue oldest age 목표 초과 / 재시도 소진 / finalize DB 오류 / 구조적 형량 규칙 위반 저장 시도. **보정 가능한 오류 1건마다 알림을 보내지 않는다.** 비용 경고 일 5,000원(초안)
- `GET /internal/v1/metrics/snapshot`(서비스 인증) — 지표 JSON. 데모 C "관측 화면" 은 백엔드 `GET /posts/{id}/verdict` 의 `view` + 우리 `GET /internal/v1/trials/{post_id}/trace`(dossier 라벨·recall 출처·노드 타임라인, 서비스 인증) 를 프론트가 내부 화면에서 보여준다 — 10 §4.5 `(제안)`

### 3.4 짤 (proposal2 §17, 10 §11)
- 서기 `meme_hints.emotion` 어휘 enum(01 스키마): `DISAPPROVAL` `ABSURD_SERIOUSNESS` `SMUG` `PITY` `CELEBRATION` `RESIGNATION`. `keywords` ≤ 5
- 점수 규칙(백엔드 finalize): 태그 필터 → `+3` 전략 일치 · `+2` 감정 일치 · `+1` 키워드 교집합 · `−5` 같은 사용자 최근 노출 5장 → `crc32(post_id+image_id)` tie-break → `meme_image_id` 고정. 후보 0 → 결과별 기본 이미지
- 우리 몫: 서기 출력의 `meme_tag` 교정(⑤ 4), 힌트 어휘 준수율(골든셋 자동 검사에 추가)

### 3.5 데모 시드 · 리허설 (proposal2 부록 B)
- `scripts/seed_agent_db.py`: `ai.banter_examples`(06 승인분) + 04 데모 C 메모리(백엔드가 준 `post_id`) + 로컬용 `meme_catalog` fixture. 멱등
- 리허설 표(3회 반복, 매회 `first_result_latency`·`cost` 기록):
| 시나리오 | 절차 | 확인 | 목표 |
|---|---|---|---|
| A 사유 심문 | "그냥" 등록 → 바텀시트 → 보완(`FINAL_CHECK`) → 등록 | 질문 1문장·1회, 중복 post 없음, PREPARE job 1개 | intake ≤ 2.5초 |
| B 거지방식 판결 | 30분 방, 3명 투표 **`disagree`(부결)** → 판결 화면 → 공유 카드 | 양형 0호출·서기+검수, `texts` 방 강도 행, 짤 선택, 카드 `PUBLIC` 근거만 | 마지막 표 → 첫 저장 ≤ 6초 |
| C 개인화 | 스타벅스 3번째 → 판결 → trace 화면 | `PRIOR`·반복 AGGREGATE 라벨 인용, recall 출처 표시 | ≤ 8초 |
| 장애 1 xAI | `XAI_API_KEY` 무효 → 유죄 | 형량 AI 정상, 문구 TEMPLATE, `VENDOR_UNAVAILABLE`, round1 예약 | ≤ 10초 |
| 장애 2 OpenAI | `OPENAI_API_KEY` 무효 | 양형 RULE, 문구 TEMPLATE(검수 불가) | ≤ 10초 |
| 예산 초과 | `WRITER_NODE_TIMEOUT_SECONDS=0.1` | `DEADLINE_EXCEEDED`/TEMPLATE, 응답 ≤ deadline+0.5s | |
| 삭제 | 판결 뒤 게시물 삭제 | 조회 즉시 차단·템플릿, retry 저장 0, `invalidated_evidence_total` +1 | 즉시 |
- 시나리오 B 는 `dismissed`(정족수 미달)가 아니라 `disagree`(부결)다(proposal2 §3 #9)

### 3.6 runbook · 동결 (`docs/runbook.md`)
- 키·결제 잔액, 심사 14일 × 일 200건 × 40원 ≈ 11만 원 여유. 비용 경고 초과 시 `IMMEDIATE_REPAIR_MAX=0`·`TEXT_RETRY_ROUNDS=1` 로 낮춘다
- 헬스 `/health/live` 5분(백엔드 모니터링), `/health/ready` 배포 직후. 로그 `trace_id` 로 백엔드와 연결(`X-Trace-Id`)
- 롤백: 이미지 태그 `ai-YYYYMMDD-N`, 이전 태그 재배포 ≤ 5분. 프롬프트 롤백 = `PROMPT_BUNDLE_VERSION` 기준선. 정책 롤백 = `GUARDRAIL_POLICY_VERSION=guardrail-v1`
- 동결(9/20): 핫픽스만. 프롬프트·정책 변경도 동결(회귀 없이 배포 금지). `.env`·토큰 회전 절차, `SERVICE_AUTH_TOKEN` 회전

## 4. 완료 기준 (DoD)

### 4.1 정량 목표
| 지표 | 목표 | 측정 |
|---|---|---|
| 형량 중복 확정 | 장애 4종 + 동시 도착에서 **0** | 테스트 + 백엔드 commit record |
| 삭제 정보 재노출 | 삭제 후 조회·retry·캐시 재노출 **0** | 테스트 |
| 늦은 성공 | `STALE_GENERATION` 거부 100%, 모델 재호출 0 | `llm_calls` 건수 |
| 리허설 | A·B·C 각 3회 목표 시간 안, 장애·예산·삭제 각 1회 | 체크리스트 |
| 실측 기록 | `first_result_latency` p95, 폴백률, 누적 비용 → `00-INDEX.md` §7 | metrics snapshot |
| 관측 | verdict 실행 100% 에 trace 필드, 라벨에 ID 0 | 로그 검사 |

### 4.2 검증 테스트 시나리오
- **`tests/integration/test_failures.py`** — 장애 4종 각각(§3.1 표) + 벤더 2종 + 예산 초과
- **`tests/integration/test_text_retry.py`** — 고정 형량 다르면 거부 / `intensities[]` 만 재생성 / round 예산 20초 / 삭제된 prep 미사용 / 성공 시 `text_version` +1·이미지 유지
- **`tests/unit/test_metrics.py`** — 9지표 계산, 라벨에 UUID 패턴 없음, 로그에 사유·토큰 없음
- **`tests/unit/test_ledger_sweep.py`** — UNKNOWN 24h 정리
- **골든셋 자동 검사 추가**: `meme_hints.emotion ∈ enum`

### 4.3 동작 확인 가이드 (수동)
```bash
uv run pytest tests/integration/test_failures.py -q
uv run scripts/seed_agent_db.py --demo-user $U --demo-room $R --post-ids $P1,$P2
curl -s localhost:8100/internal/v1/metrics/snapshot -H "Authorization: Bearer $SERVICE_AUTH_TOKEN" | jq '.first_result_latency_seconds, .template_first_rate, .case_cost_micro_usd'
XAI_API_KEY=invalid uv run geoji-ai worker &      # 장애 1 리허설 인스턴스
uv run geoji-ai ledger-sweep --older-than 24h
```

### 최종 완료 기준:
- [ ] **형량 중복 확정·삭제 정보 재노출 0건**(proposal2 §20 작업 8) + 실제 p95·폴백률·누적 비용 기록
- [ ] 리허설 A·B·C ×3, 장애 2종·예산·삭제 시연 기록
- [ ] 짤 점수 규칙·감정 enum 이 백엔드 finalize 에 반영되어 공유 카드에 짤이 뜬다(기획서 M4)
- [ ] runbook·알림·헬스 등록, 9/20 동결 태그

## 5. 작업 분할 (Task Breakdown — 카드 연동)

| # | 카드명 | 설명 | 라벨 | 예상 | 선행 |
|---|---|---|---|:--:|---|
| OP-01 | 장애 4종 테스트 | 프로세스 kill·지연·삭제·네트워크 단절 재현 | test | 0.75d | 05, 06 |
| OP-02 | `TEXT_RETRY`·sweep | REGENERATE 완성, `intensities[]`, 20초, `ledger-sweep` | worker | 0.5d | 05 GR-03 |
| OP-03 | 관측 | 로그 필드, 지표 9, snapshot·trace 엔드포인트, 알림 훅 | telemetry | 0.75d | 02, 06 |
| OP-04 | 짤 힌트·규칙 전달 | emotion enum, 골든셋 검사, 백엔드 규칙 문서 | domain | 0.25d | 06 |
| OP-05 | 시드·리허설 | `seed_agent_db.py`, 백엔드 시드 요구, 표 3회 실행·기록 | qa | 0.75d | OP-01~04, 백엔드 |
| OP-06 | runbook·동결 | 비용 경고·헬스·롤백·동결 절차, 9/20 태그 | ops | 0.25d | OP-05 |

**OP-01** — [ ] kill@9s / [ ] 늦은 성공 409 / [ ] retry 중 삭제 / [ ] commit 후 단절 / [ ] 벤더 2종·예산
**OP-02** — [ ] REGENERATE 규칙 5 / [ ] sweep CLI / [ ] 테스트
**OP-03** — [ ] 로그 필드 / [ ] 지표 9 / [ ] snapshot·trace / [ ] 알림 4종 훅 / [ ] ID 라벨 검사
**OP-04** — [ ] enum·스키마 / [ ] 골든셋 검사 / [ ] 10 §11 전달
**OP-05** — [ ] 시드 / [ ] 백엔드 시드 확인 / [ ] 7행 × 3회 기록
**OP-06** — [ ] runbook / [ ] 헬스·알림 / [ ] 동결 태그
