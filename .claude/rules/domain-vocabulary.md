---
description: 서비스 용어와 enum 표준, 대문자를 유지하는 식별자, ID 체계, 확정 수치
paths:
  - "docs/**"
  - "src/**"
  - "contracts/**"
  - "tests/**"
  - "scripts/**"
---

# 도메인 용어

## 이름

- 서비스명은 떼거지. 거지방은 방 단위 명칭, 거지야호는 팀명
- AI 역할 다섯: 심문관(Intake), 조서(Context), 드립 후보(Banter), 양형관·서기(Judge), 검수관(Evaluator). 양형관은 OpenAI, 서기는 Grok
- 판결 결과는 유죄, 무죄, 동의, 기각, 각하 5종

## enum은 프론트 값이 표준이다(D-21)

- 강도 `mild|spicy|hell`, 평결 `guilty|notGuilty|agree|disagree|dismissed`, 게시물 `spent|considering`, 형량 `probation|oneDay|life`, 카테고리 11종 고정
- 정본은 `../geoji-web/src/shared/domain/`. 새 예시를 쓸 때 대문자 `MILD/GUILTY/DAYS_1`을 쓰지 않는다. `scripts/probe_writer_latency.py`의 대문자는 치환 전 잔재다
- 대문자를 유지하는 것은 식별자뿐이다. 짤 태그 5종(`GUILTY_HEAVY` 등), `MODEL_EVALUATOR_HELL`, `NO_SPEND`, job kind(`SENTENCE`, `PREPARE`, `RETAIN`, `TEXT_RETRY`), 상태값(`PENDING/FINAL`, `TEMPLATE_READY/AI_READY`), 오류 코드(`AI_NOT_READY`, `STALE_GENERATION`, `EVIDENCE_INVALIDATED`), 소스(`AI/TEMPLATE/RULE`), 정책 버전(`guardrail-v2`)

## ID 체계

- 결정 `D-NN`, 카드 `CT-NN`(문서별 접두어), 근거 라벨 `F0~F6`
- 마이그레이션 `001~005`. 001~003 AI, 004 백엔드, 005 P1
- 마일스톤 M2 9/10, M3 9/15, M4 9/18, 9/20 동결

## 구조와 수치(바꾸면 00 §1 표와 §7 표도 같이)

- 공유 Supabase Postgres + `ai.jobs` 큐 + Python 워커 + 백엔드 finalize. 워커는 업무 테이블을 직접 쓰지 않는다
- 전달은 폴링. Realtime과 SSE는 없다
- 시간 예산은 유죄 7~8초, 그 외 5~6초, 재생성 12~14초. 비용은 건당 약 21원, 상한 40원
- 지출 등록 `item` 30자 이하, `reason` 200자 이하. 판결문 `statement` 합산 300자 이하
