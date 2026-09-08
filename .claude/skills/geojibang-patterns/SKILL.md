---
name: geojibang-patterns
description: "Use when working in geojibang, especially before writing or editing a docs/plans/NN-*.md tech spec, recording a team decision (D-NN) or card status (CT-NN) across the plan set, quoting enum or contract values, or writing a docs(plans) commit — conventions measured from git history and the 11 plan documents"
metadata:
  version: "1.0.0"
  source: local-git-analysis
  analyzed_commits: "10"
---

# Geojibang Patterns

이 저장소(`geoji-agent`, 로컬 이름 `geojibang`)는 떼거지 서비스의 **AI 파트 저장소**다. 9/8 기준 코드는 실측 스크립트 1개뿐이고, 실제 작업물은 `docs/plans/` 의 계획서 11개다. 아래 규칙은 그 문서와 커밋 10개에서 측정한 것이다.

## Commit Conventions

측정: 커밋 10개 중 머지 2개를 뺀 8개 전부 `type(scope): 한국어 제목` 형식(100%).

- **type·scope**: `docs(plans)` 6회, `chore(gitignore)`·`chore`, `feat(prompt)`, `test(grok)`. 계획서 갱신은 항상 `docs(plans)`.
- **제목은 한국어**, 결정 기록 커밋은 날짜를 앞에 둔다: `docs(plans): 9/8 인터뷰 결정 기록 — 서버 코드 대조·D-20·D-21·폴링·배포`. 항목 나열은 가운뎃점(`·`), 구분은 em dash(`—`) 또는 `+`.
- **본문은 `- ` 불릿**으로 "어느 문서의 어느 절이 왜 바뀌었는지"를 쓴다. 예: `- 01 계약서 + 이를 인용하는 00·04~08·10 동일 규칙 적용`, `- 짤 태그 5종·MODEL_EVALUATOR_HELL·전략명·NO_SPEND 는 식별자라 유지`. 문서는 번호(`01`, `10 §15.2`)로만 부른다.
- **트레일러**: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` 과 `Claude-Session: https://claude.ai/code/session_…` 를 붙인다.
- **브랜치·머지**: 문서 작업은 `docs/<topic>` 브랜치(`docs/plans-proposal2`)에서 PR 로 `main` 에 머지했고, 이후 소규모 갱신은 `main` 직접 커밋 후 push. 사용자가 "커밋하고 푸시해줘"라고 하면 두 동작을 한 번에 한다.
- **한 커밋 = 한 결정의 전파**: 결정 하나가 여러 문서에 걸치면(예: enum 치환 → 00·01·04~08·10, 8개 파일) 한 커밋으로 묶는다.

## Code Architecture

```
docs/plans/                 # 추적되는 유일한 문서 폴더 (.gitignore: docs/*  !docs/plans/)
  00-INDEX.md               # 매트릭스·문서 맵·Phase 게이트·의존성 사슬·§8 정합성·§8.4 결정 대장
  01~08-<slug>.md           # 작업 1~8 기술 명세서 (날짜 순, 9/9 → 9/18)
  09-p1-roadmap.md          # 심사 이후 P1 (날짜 없음, 착수 게이트·수용 기준만)
  10-backend-contract.md    # 백엔드 담당 필독 계약서. §14 미결, §15 9/8 결정 기록
scripts/probe_writer_latency.py   # PEP 723 인라인 의존성, `uv run` 으로 실행
scripts/probe_out/<YYYYMMDD-HHMMSS>-<provider>-<model>[-split].json   # 실측 결과, 추적함
.env.example                # 키 이름만. .env 는 절대 커밋하지 않는다
geoji-server/ geoji-web/    # 백엔드·프론트 저장소의 로컬 체크아웃. 의도적으로 untracked — git add 하지 않는다
```

- 문서 번호 `NN-` 가 곧 참조 ID 다. 다른 문서를 인용할 때는 `10 §15.2`, `01 CT-07`, `08 §3.4` 처럼 **번호 + 절/카드** 만 쓴다. 파일명은 링크할 때만.
- 근거 문서 `docs/proposal/proposal2.md` 와 기획서는 **git 에 없다**. 계획서는 proposal2 없이도 구현할 수 있게 계약·DDL·수치를 옮겨 적는다. 새 절을 쓸 때도 "proposal2 §N 참고"로 끝내지 말고 내용을 복사한다.
- 계획 문서가 말하는 코드 경로(`src/geoji_ai/`, `contracts/`, `database/migrations/`, `tests/`)는 **아직 존재하지 않는다**. 이 저장소 루트 = proposal2 의 `services/ai/`.

## Plan Document Template

01~09 는 같은 골격을 쓴다. 새 계획서나 절을 추가할 때 이 순서·제목을 그대로 따른다.

```
# 🛠️ [Tech Spec] 기술 명세서: 작업 N — 주제 · 주제 · 주제 (마감 날짜 또는 M2 9/10)

> 근거: proposal2 §a(내용), §b(내용) …, §20 작업 N + 먼저 실패시킬 케이스(케이스 1 · 케이스 2 · …).
> **선행 문서: 01·02.** 백엔드 몫은 `10-backend-contract.md` §n. 이 문서는 **우리 쪽**만 다룬다.
> 한 줄 요약 또는 완료 기준(proposal2 §20): **굵게 핵심 조건**.

## 1. 개요 및 구현 목표
### 목적:            (불릿, 굵은 핵심어)
### 핵심 플로우:     (코드 블록 트리/화살표 다이어그램)
### 현상태          (| 항목 | 값 | 표)
## 2. 작업 범위 (Scope Boundary)
### In-Scope        (| # | 항목 | 등급 | 난이도 | — # 은 3.1, 3.2 … 절 번호와 일치)
### Out-of-Scope    (불릿, "→ 작업 N" 으로 소속 문서 명시)
### 다른 파트에 요청 (백엔드·프론트)   (| 대상 | 요청 | 기한 |)
### 팀 결정 대기
## 3. 기술 상세 설계 (Technical Design)
### 3.x 제목 (`구현/파일/경로.py`, proposal2 §n)
## 4. 완료 기준 (DoD)
### 4.1 정량 목표    (| 지표 | 목표 | 측정 | — 측정 열은 테스트 파일명)
### 4.2 검증 테스트 시나리오   (**`tests/…/test_x.py`** 별 `- [ ]` 체크박스)
### 4.3 동작 확인 가이드 (수동)   (```bash 블록, `uv run …`)
### 최종 완료 기준:  (`- [ ]` 체크박스, 확정된 것은 `- [x]` + "— 9/8 확정")
## 5. 작업 분할 (Task Breakdown — 카드 연동)
```

- **등급·난이도 어휘**: 등급 `Critical/High/Medium/Low`, 난이도 `S(≤ 0.5일)/M(1~2일)/L(3일+)`, 카드 예상은 `0.25d/0.5d/1d`.
- **§5 카드 표** 열은 `| # | 카드명 | 설명 | 라벨 | 예상 | 선행 |`. 카드 ID 는 문서별 접두어 + 2자리(`CT-01` 계약, 다른 문서도 같은 꼴). 표 아래에 카드별 한 줄 체크리스트: `**CT-02** — [ ] 항목 / [ ] 항목 / [ ] 항목`.
- **10 계약서**는 예외 템플릿: `## 0. 읽는 법` 표(절·내용·필요 시점) → 절별 계약 → `§13 수용 검사` → `§14 미결(| ID | 항목 | 기한 |)` → `§15 날짜별 결정 기록`.
- 문서 안 표기 습관: 숫자·단위 사이 공백 없음(`7~8초`, `0.5d`), 한글과 코드 사이는 한 칸(`` `ai.jobs` 큐 ``), 강조는 `**…**`, 이모지는 제목에만(🛠️ 📋 🤝, 2×2 배치에 🟢🔵⚪🔴).

## Decision Ledger Workflow

가장 자주 반복된 작업 흐름(커밋 3eaa76f → ffefb1b → 4720d20 → 752d63b).

1. **결정은 두 곳에 기록한다**: `10-backend-contract.md` §15 의 날짜별 표(`| ID | 결정 | 백엔드가 할 일 | AI 파트가 할 일 |`)와 `00-INDEX.md` §8.4 의 체크리스트. 백엔드 무관한 AI 내부 결정은 10 §15.3 별도 표.
2. **미결 → 확정 표기**: 원래 항목을 지우지 않고 `~~D-20~~ **9/8 확정(10 §15.2)** — 남은 것: …` 로 바꾼다. 체크리스트는 `- [ ]` → `- [x] **D-20** — 9/8 확정: 내용(참조)`. 예전 문장을 남길 때는 `(원문)` 접두.
3. **인용하는 문서 전부 갱신**: 결정 ID(`D-NN`)나 enum 을 언급한 문서를 grep 해서 같은 커밋에서 고친다. 갱신 뒤 "결정했는데 아직 미결로 남은 문장"이 없는지 사실관계 검증을 한 번 돌린다(752d63b 는 이 검증으로 25곳을 고쳤다).
4. **카드 상태**: 완료된 카드 항목은 `~~…~~(9/8 완료 — 내용)` 로 취소선 + 사유. 부분 완료도 같은 방식.
5. **실측 뒤 채울 결정**은 빈칸을 남긴다: `sentencing p90 ____초 → 유지 / 후보`. 빈칸을 임의 값으로 채우지 않는다.
6. **백엔드에 제안하는 항목**에는 `(제안)` 표시를 붙이고 10 §14 미결 표에 기한과 함께 올린다.
7. 커밋 메시지 본문에 갱신한 문서 번호와 절을 나열한다(위 Commit Conventions).

## Domain Vocabulary (9/8 확정, 문서 전체 공통)

- **enum 은 프론트 값이 표준**(D-21): 강도 `mild|spicy|hell`, 평결 `guilty|notGuilty|agree|disagree|dismissed`, 게시물 `spent|considering`, 형량 `probation|oneDay|life`, 카테고리 11종 고정. 계획서·계약서에 새 예시를 쓸 때 대문자 `MILD/GUILTY/DAYS_1` 을 쓰지 않는다(`scripts/probe_writer_latency.py` 의 대문자는 치환 전 잔재).
- **대문자를 유지하는 식별자**: 짤 태그 5종(`GUILTY_HEAVY` 등), `MODEL_EVALUATOR_HELL`, `NO_SPEND`, job kind(`SENTENCE`, `PREPARE`, `RETAIN`, `TEXT_RETRY`), 상태값(`PENDING/FINAL`, `TEMPLATE_READY/AI_READY`), 오류 코드(`AI_NOT_READY`, `STALE_GENERATION`, `EVIDENCE_INVALIDATED`), 소스(`AI/TEMPLATE/RULE`), 정책 버전 문자열(`guardrail-v2`).
- **ID 체계**: 결정 `D-NN`, 카드 `CT-NN`(문서별 접두어), 근거 라벨 `F0~F6`, 마이그레이션 `001~005`(001~003 AI, 004 백엔드, 005 P1), 마일스톤 `M2 9/10 · M3 9/15 · M4 9/18 · 9/20 동결`.
- **구조 결정**: 공유 Supabase Postgres + `ai.jobs` 큐 + Python 워커 + 백엔드 finalize. 워커는 업무 테이블을 직접 쓰지 않는다. 전달은 **폴링**(Realtime·SSE 없음). 배포는 백엔드 EC2 1대 + Docker Compose, AI 파트는 이미지 2개와 compose 조각만. 모델은 Grok(서기) + OpenAI luna(심문관·양형관·검수관).
- **수치**: 유죄 7~8초, 그 외 5~6초, 재생성 12~14초, 건당 약 21원(상한 40원), 지출 등록 `item` ≤ 30자·`reason` ≤ 200자, 판결문 `statement` 합산 ≤ 300자. 수치를 바꾸면 00-INDEX §1 표와 §7 표도 같이 바꾼다.

## Testing Patterns

코드가 없으므로 테스트도 계획 수준이다. 계획서가 약속한 규칙:

- 각 문서 헤더의 **"먼저 실패시킬 케이스"** 가 곧 테스트 목록이다. §4.2 는 이 케이스를 `tests/<layer>/test_<topic>.py` 별 체크박스로 푼다. 새 기능을 계획할 때 케이스를 먼저 쓰고 설계를 쓴다.
- 테스트 디렉터리 약속: `tests/contracts/`(거부 케이스·fixture·동등성), `tests/unit/`, `tests/integration/`(실제 Postgres), `tests/evaluations/`(골든셋 회귀·심문 평가), `tests/fakes/`(가짜 백엔드 `backend_app.py`).
- **일반 CI 는 fake provider 만** 쓴다. 실제 벤더 호출은 실측 스크립트와 작업 6 의 골든셋 회귀에서만.
- 프롬프트 변경은 **골든셋 회귀 → 사람 검수** 두 관문을 지난다. 단일 LLM-as-judge 점수로 통과시키지 않는다.
- 실측 스크립트 실행·결과: `uv run scripts/probe_writer_latency.py --model … --n N [--split] [--dry-run]`. 결과 JSON 은 `scripts/probe_out/` 에 타임스탬프 파일로 남기고 커밋한다. 검수 기준점은 파일명으로 인용한다(`v5.3 = 20260907-150704-*.json`).
