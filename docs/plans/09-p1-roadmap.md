# 🛠️ [Tech Spec] 기술 명세서: 심사 이후 로드맵 (P1) — reflect · Hindsight · 모델 실험 · 플래그 · 잔여 항목

> 근거: proposal2 §2.1 결정 3·10, §12 기능 플래그(`ROOM_COMMENT_STYLE_ENABLED`·`PUBLIC_HISTORY_CALLBACK_ENABLED`·`REFLECT_ENABLED`·`HINDSIGHT_ENABLED` 기본 false), §16.3 Hindsight, §18 오프라인 도구(reflect 워커), §19(Kubernetes 는 기존 운영 환경이 있을 때만), §20 "심사 이후" 행, §22 D-03·D-05 / 기획서 §16 향후 확장.
> **착수 게이트: 9/20 배포 동결 이후.** 심사·투표 기간(9/21~10/5)에는 핫픽스만. 실제 착수는 10/6 이후, 본선 준비 기간이 있으면 그 안에서 우선순위를 다시 정한다(D-08).
> 01~08 과 달리 날짜가 없다. 항목마다 **착수 게이트**와 **수용 기준**만 둔다. 심사 전에 이 문서의 항목을 당기지 않는다 — 데모 시나리오 A·B·C 는 08 까지로 성립한다.

## 1. 개요 및 구현 목표

### 목적:
- proposal2 가 P1·플래그로 미룬 것을 한 곳에 모아 왜 미뤘는지, 언제 다시 볼지, 무엇이 되면 끝인지 적는다
- **YAGNI 판정** — "이미 아픈 것"(데모·심사에서 드러난 한계)과 "수요 미확인"(포트폴리오 설명용)을 가른다

### YAGNI 판정
| 항목 | 분류 | 근거 |
|---|---|---|
| A reflect 워커 | 이미 아픈 것(조건부) | 사용자당 판결 10건을 넘으면 recall 참조 20개로는 "패턴" 이 문장에 안 나온다. 심사 2주 `recall_user` 후보 수 집계로 판정 |
| B Hindsight 어댑터 | 수요 미확인 | 별도 서비스 운영·과금·개인정보(D-03). Postgres recall 이 Hit@3 90% 를 넘기면 불필요. 인터페이스만으로 포트폴리오 설명 가능(proposal2 §16.3) |
| C 방 댓글 말투 켜기(`ROOM_COMMENT_STYLE_ENABLED`) | D-04 대기 | retain 은 P0 에서 이미 한다. 고지 문구 확정 즉시 켤 수 있다 |
| H 서버 `AiClient` 3종 동기 엔드포인트(주간 상 이름 발명 · 도전 과제 서술 · 순찰 위험 문구) | 9/8 결정 P1 | `geoji-server` 가 `StubAiClient` 로 데모 가능. 그래프 없이 Grok 1프롬프트 × 3, 각 0.25d. 10 §15.3 |
| D 공개 이력 콜백(`PUBLIC_HISTORY_CALLBACK_ENABLED`) | 수요 미확인 | 공개 공유 카드에서 과거 이력을 언급하는 것 — 공개 범위 정책 확정 뒤 |
| E 드립 후보 OpenAI 대체 | 비용(수요 미확인) | 건당 −3원. 골든셋 judge 가 같을 때만 |
| F `grok-4.6` 재평가 | 외부 의존 | xAI 가 추론 해제를 열 때까지 착수 불가 |
| G 잔여 소항목 | 혼합 | §3.5 |

### 핵심 플로우:
```
[심사 종료 10/5] ─▶ 08 metrics snapshot 2주 집계 ─▶ A 게이트(recall 포화율) · E 실험(반나절) · F(xAI 릴리스 확인)
                 ─▶ D-03 재검토 ─▶ B 착수 여부           ─▶ D-04 확정 ─▶ C 플래그 on(코드 변경 없음, 검수관 ROOM 항목 활성)
```

## 2. 작업 범위 (Scope Boundary)

### In-Scope
| # | 항목 | 등급 | 난이도 |
|---|---|:--:|:--:|
| 3.1 | A — reflect 워커 + `ai.memory_summaries` + 조서 편입 | Medium | M |
| 3.2 | B — Hindsight 어댑터(같은 `MemoryPort`, `HINDSIGHT_ENABLED`) | Low | L |
| 3.3 | C·D — 플래그 켜기 절차(고지·정책 선행) | Low | S |
| 3.4 | E·F — 모델 실험 2종 | Low | S |
| 3.5 | G — 잔여 소항목(LangGraph checkpointer·Prometheus/Grafana·LangSmith·`node_results` 보존 정책·물리 삭제·CI 실비·형 집행 연동·temperature 튜닝·D-23 밴드 후보) | Low~Medium | S~M |

### Out-of-Scope
- 온라인 선호 학습, 판결마다 실시간 이미지 생성, 파인튜닝(proposal1 §19). 유명 짤 선화·수집기·Metadata Agent(D-09 범위 밖). 기획서 P2 항목

### 다른 파트에 요청 (백엔드·프론트)
| 대상 | 요청 | 시점 |
|---|---|---|
| 팀 | D-03 Hindsight self-host(Docker) vs Cloud, 과금 주체, 개인정보 처리방침 | B 착수 전 |
| 팀 | D-04 댓글 고지 문구·위치 확정 → C 플래그 | 결정 즉시 |
| 백엔드 | 형 집행 추적(P1) 완료 시 `CaseSnapshot.defendant.serving` 추가 → G-6 | 해당 시 |

### 팀 결정 대기
- D-08 일정 재산정(본선 준비 기간에 무엇을 당길지) — §4 표를 회의 자료로
- LangSmith 도입, CI 에서 회귀 평가기 실비 실행(06 §2)

## 3. 기술 상세 설계 (Technical Design)

### 3.1 A — reflect 워커 (proposal2 §18 오프라인 도구, proposal1 §8.4)
| 항목 | 내용 |
|---|---|
| 트리거 | 개인: 같은 카테고리 SPEND 3회 이상(30일) ∧ 요약 없음 또는 요약 후 새 사실 ≥ 3. 방: COMMENT ≥ 20 ∧ 작성자 ≥ 3(D-05) ∧ 요약 후 새 댓글 ≥ 10 |
| 실행 | `REFLECT` job kind 추가(001 CHECK 확장 — `schema_version` 유지, 마이그레이션 `005`) 또는 `uv run geoji-ai reflect --since 1h` cron. BACKGROUND 슬롯 |
| 모델 | `MODEL_JUDGMENT` 1호출. 입력 = 뱅크 사실(라벨), 출력 `{summary ≤ 120자, evidence_labels[], kind ∈ PERSONAL_PATTERN|ROOM_STYLE, sensitive}` |
| 폐기 | `evidence_labels` 비거나 입력 밖 → 폐기. `sensitive=true`(정체성·건강·재정 상태 추정·제3자) → 폐기 + 로그. 방 요약에 닉네임 → 폐기 |
| 저장 | `ai.memory_summaries(id, bank_type, bank_id, kind, summary, evidence_ids uuid[], prompt_version, privacy_versions, updated_at, invalidated_at)` — 뱅크·kind 당 1행 upsert |
| 소비 | 04 `build_evidence` 가 `R-SUM` 입력으로 AGGREGATE 성격 Evidence(`MODEL_INFERENCE`) 추가. 방 요약은 서기 `style_summary`(표현에만) |
| 삭제 | 무효화 SQL 에 `memory_summaries` 추가. 근거 사실이 삭제되면 요약도 폐기 |
| 수용 | 근거 없는 반복 주장 0·삭제된 기억 recall 0·민감 추론 0(라벨 20), 데모 C 결과 동일 |

### 3.2 B — Hindsight 어댑터 (proposal2 §16.3)
| 항목 | 내용 |
|---|---|
| 형태 | `adapters/hindsight_memory.py` 가 `MemoryPort` 를 구현. `HINDSIGHT_ENABLED=true` 로 전환, Postgres 어댑터는 롤백용으로 유지 |
| 뱅크 | `user:{id}`·`room:{id}` 그대로 bank id. **recall 은 여전히 참조만** — Hindsight 결과를 `MemoryCandidate` 로 바꾸고 본문은 백엔드 `resolve-evidence` 를 거친다(결정 20 유지) |
| retain·reflect | Hindsight 자동 추출(토큰 과금)·reflect 루프 사용, A 워커는 끈다 |
| 삭제 | 뱅크·문서 삭제 API 로 04 §3.5 와 같은 의미. 통합 테스트 재사용 |
| 운영 | Docker slim ≈ 500MB, 자체 PG. 배포처·과금·개인정보 검토 선행(D-03) |
| 수용 | 04 지표 동일 통과 + Postgres 대비 Hit@3 +5%p 아니면 채택하지 않는다 |

### 3.3 C·D — 플래그 켜기
- C: D-04 문구 반영 확인 → `ROOM_COMMENT_STYLE_ENABLED=true` → `resolve-evidence` 가 `style_comments` 반환 → 서기·드립 입력에 말투 예시 ≤ 3 → 검수관 `ROOM` 별명 모욕 항목 활성. 골든셋에 방 댓글 포함 사건 5개 추가 후 회귀
- D: 공개 공유 카드 정책(어느 과거 이력을 공개 문구에 써도 되는가) 확정 → `PUBLIC_HISTORY_CALLBACK_ENABLED=true` → 서기 `PUBLIC` 문구에 `PUBLIC` scope Evidence 만 인용(04 visibility 이미 지원)

### 3.4 E·F — 모델 실험
- E: 06 골든셋(`prepare_cases` 10 + 드립 30)으로 `MODEL_BANTER=gpt-5.6-luna` — judge 거지방다움·재미 −0.2 이내 ∧ 필터 탈락률 +5%p 이내면 채택(−3원/건)
- F: xAI 가 추론 해제를 열면 `scripts/probe_writer_latency.py --model grok-4.6 --reasoning-effort none --split --n 10` — p90 ≤ 4.5초 ∧ ≤ 15원 ∧ 회귀 PASS ∧ 재미 +0.3 이면 채택

### 3.5 G — 잔여 소항목
| # | 항목 | 내용 | 난이도 | 게이트 |
|---|---|---|:--:|---|
| G-1 | LangGraph checkpointer | 노드 단위 재개(현재는 job 단위 + `node_results`). 긴 그래프가 생길 때 | S | 노드 수 증가 |
| G-2 | Prometheus/Grafana | `metrics snapshot` → exporter, 대시보드 | M | 트래픽이 데모 규모를 넘을 때 |
| G-3 | LangSmith | `LANGSMITH_ENABLED` 시 트레이스 전송·평가 데이터셋 관리 | S | 팀 결정 |
| G-4 | `node_results` 보존 정책·물리 삭제 | 24h 초안 확정, `deleted_at` 30일 purge, `trial_prep`·`llm_calls` 보존 90일 | S | 개인정보 처리방침 |
| G-5 | CI 회귀 실비 실행 | Actions 에서 `--quick`(월 ≈ 1만 원) | S | 팀 결정 |
| G-6 | 형 집행 연동 | `defendant.serving` → AGGREGATE Evidence, 서기 "수감 중" 소재 | S | 백엔드 P1 |
| G-7 | temperature·headline 다양성 | 06 평가기 `--temperature` 스윕 | S | 리허설에서 반복 관찰 시 |
| G-8 | D-23 밴드별 사전 후보 | 작업 3 실측 결과가 2초 초과일 때만 — PREPARE 에서 밴드 3개 후보, SENTENCE 에서 선택 | M | 실측 |
| G-9 | 지옥맛 검수관 terra 상시 여부 | 심사 기간 위반 집계 후 | S | 06 결과 |
| G-10 | Kubernetes | 팀 운영 환경이 있을 때만. 기본은 Compose | M | 팀 |

## 4. 완료 기준 (DoD)

### 항목별 착수 게이트 · 수용 기준
| 항목 | 착수 게이트 | 수용 기준 | 예상 |
|---|---|---|:--:|
| A reflect | 심사 2주 집계에서 후보 ≥ 15개인 사용자 ≥ 20% **또는** 본선 데모에 "장기 패턴" 장면 | §3.1 수용 + 데모 C 동일 | 2d |
| B Hindsight | D-03 결정 | §3.2 수용 | 3d + 운영 |
| C 댓글 말투 | D-04 확정 | 회귀 PASS(방 댓글 사건 5) | 0.5d |
| D 공개 콜백 | 공개 범위 정책 | 공유 카드 `PUBLIC` 근거만 인용 테스트 | 0.5d |
| E·F 실험 | 골든셋 존재 / xAI 릴리스 | §3.4 | 각 0.5d |
| G-1~10 | 각 행 | 각 행 | 0.25~2d |

### 최종 완료 기준:
- [ ] 착수한 항목은 01~08 형식의 별도 명세로 승격하고 여기서는 상태만 갱신한다
- [ ] 착수하지 않은 항목은 폐기 사유(수요 미확인 유지 / 실험 결과 미달)를 남긴다
- [ ] `00-INDEX.md` §8.4 의 P1 결정(D-03·D-04·D-08·LangSmith·CI)이 갱신됨

## 5. 작업 분할 (Task Breakdown — 카드 연동)

| # | 카드명 | 설명 | 라벨 | 예상 | 선행 |
|---|---|---|---|:--:|---|
| P1-01 | 심사 집계·게이트 판정 | 08 snapshot 2주 집계 → A·G-7·G-9 판정, D-08 자료 | analysis | 0.25d | 심사 종료 |
| P1-02 | A reflect | `005` 마이그레이션, 트리거, 프롬프트·폐기, 조서 편입, 무효화, 테스트 | memory | 2d | P1-01 |
| P1-03 | B Hindsight | 어댑터·전환·테스트 재사용·운영 문서 | memory | 3d | D-03 |
| P1-04 | C·D 플래그 | 고지·정책 확인, 골든셋 추가, 회귀 | flags | 1d | D-04 |
| P1-05 | E·F 실험 | 실행·비교표·결정 | eval | 1d | 06, xAI |
| P1-06 | G 소항목 | 게이트 열린 것만 | misc | 각 | 각 |

**P1-01** — [ ] 집계 SQL / [ ] 판정 표 / [ ] D-08 자료
**P1-02** — [ ] 005 / [ ] 트리거 / [ ] 프롬프트·스키마 / [ ] 폐기 규칙(라벨 20) / [ ] `R-SUM` / [ ] 무효화
**P1-03** — [ ] 어댑터 / [ ] 참조 변환 / [ ] 테스트 / [ ] 운영·롤백
**P1-04** — [ ] C / [ ] D / [ ] 회귀
**P1-05** — [ ] E / [ ] F / [ ] 기록
**P1-06** — [ ] 게이트 확인 / [ ] 구현 / [ ] INDEX
