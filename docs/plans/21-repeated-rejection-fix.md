# 반복 반려 복구 수정 계획

> 2026-09-20 사용자 승인: 원인 분석의 개선 방향을 바탕으로 계획을 작성하고 순차 수정한다.
> 실행은 `geoji-harness`의 명세 대조 → 구현 → 테스트 검증 순서이며 각 단계는 실패 테스트부터 시작한다.

**목표:** 반려된 초안의 캐시 재사용으로 재생성이 실패하는 문제를 없애고, 검사 적용 조건과 근거 정보를 일치시킨다.

**구조:** 기존 그래프·LLM 게이트웨이·원장 인터페이스를 유지한다. 새 작성 시도만 캐시 키를 분리하고 같은 논리 작업의 재실행은 재사용한다. 형량 검사 대상은 사건 유형으로 결정하며 공개 검수 보고서 계약은 유지한다.

**기술:** Python 3.12, LangGraph, Pydantic, pytest, 기존 FakeLLM·메모리 원장. 외부 API/DB 스키마·모델·예산·배포 설정 변경 없음.

## 1. 조사 결과와 선택

- `write_one`은 구조 검증 후 검수 전 초안을 캐시한다. 이 캐시는 의미 검수 통과를 보증하지 않는다.
- INITIAL에서 반려 보고서도 캐시한다. 같은 요청은 TEXT_RETRY에서도 재사용되어 새 응답을 받을 기회가 없다.
- finalize 422 재작성은 `avoid={}`이며 기법 순환도 없어 요청이 같아질 수 있다. TEXT_RETRY뿐 아니라 이 경로도 분리해야 한다.
- `_avoid`는 위반 코드와 문제 문장만 전달해 검수관의 설명·경로·근거 라벨을 버린다.
- 무형량 사건을 통과시키는 규칙은 프롬프트에만 있고 모델 오판은 전체 실패로 전파된다.
- 검수 입력은 근거 유형을 잃는다. 골든셋은 모든 근거를 DB_RECORD로 승격하고 운영과 다른 fact_type을 그대로 사용한다.

선택: 전체 캐시 비활성화나 프롬프트 완화 대신 **논리 작성 시도별 캐시 격리**를 적용한다. 이는 같은 작업의 중복 호출을 줄이면서 새 작성 기회를 보장한다. 반려 보고서는 재사용 성공 결과로 저장하지 않는다.

## 2. 범위와 제약

- 코드/테스트/이 계획서만 수정하며 커밋·푸시·유료 모델 호출·운영 DB 접근은 하지 않는다.
- `guardrail-v2`와 외부 EvaluationReport/FinalizeRequest/Job payload를 유지한다.
- 적용 대상이 없는 형량 검사만 코드로 처리한다. 문구 검수·안전 검사·형량이 필요한 사건은 계속 검증한다.
- TEXT_RETRY payload에는 과거 반려 상세가 없다. 작업 사이 피드백 영속화는 백엔드 계약 확장 없이 추가하지 않는다. 이번에는 현재 작업의 repair에 상세 피드백을 전달한다.
- 새 시도에서 실제 모델 호출이 재개되므로 실패 캐시를 읽던 때보다 비용은 발생한다. 기존 예산·시간·재시도 상한은 유지한다.
- 중간 하네스 기록은 기존 파일을 덮어쓰지 않고 `_workspace/rejection-fix/`에 저장한다.

## 3. 순차 구현

### 3.1 새 작성 시도와 통신 재실행의 캐시 분리

파일: `src/geoji_ai/application/llm_gateway.py`, `src/geoji_ai/graphs/sentencing.py`,
`tests/unit/test_llm_gateway.py`, `tests/unit/test_graph_c_cache.py`.

- [x] 게이트웨이를 포함한 실패 재현 테스트를 작성하고 기존 코드에서 실패를 확인한다.
- [x] application 내부 `CallScope`/`case_scope`에 선택적 `cache_scope`를 추가한다. 설정하지 않은 노드는 기존 키를 유지한다.
- [x] 서기·검수 요청에 다음 논리 시도 범위를 넣어 request_hash를 분리한다. `generation_id`는 제외하여 같은 job의 lease 재획득은 재사용한다.

```python
cache_scope = {
    "job_id": state["job"].id,
    "mode": state["mode"],
    "round": payload.round if isinstance(payload, TextRetryPayload) else 0,
    "call_index": call_index,
}
```

- [x] 게이트웨이는 `cache_scope`가 있을 때만 해시 입력에 추가한다. DB 컬럼·LedgerPort는 유지한다.
- [x] 검수 실패 보고서의 `remember` 호출을 제거한다. 초안 캐시는 구조 통과 결과이며 의미 통과 보증이 아님을 주석에 명시한다.
- [x] `_avoid`에 기존 필드를 유지하고 `violation_details`로 code/path/explanation/evidence_labels를 전달한다.
- [x] INITIAL 반려 → TEXT_RETRY 성공, 다른 round, 같은 job 재실행, finalize 422, 실패 보고서 미저장, 상세 피드백 전달을 검증한다.

```python
assert retry.failure is None
assert new_provider_roles == {"writer": 1, "evaluator": 1}
assert replay_provider_calls == 0
assert rejected_report_cache_entries == []
```

실행: `.venv/bin/python -m pytest tests/unit/test_llm_gateway.py tests/unit/test_graph_c_cache.py tests/unit/test_graph_c.py tests/unit/test_graph_c_invalidated.py -q`

### 3.2 형량 검사 적용 여부를 사건 유형으로 결정

파일: `src/geoji_ai/contracts/llm_schemas.py`, `src/geoji_ai/graphs/sentencing.py`,
`tests/unit/test_graph_c_evaluation.py`, `tests/unit/test_graph_c.py` 및 기존 LLM 스키마 테스트.

- [x] 무죄/구매 예정 사건의 형량 오반려, malformed/missing texts, 유죄 형량 누락 테스트를 먼저 실패시킨다.
- [x] `spent && guilty`만 형량 검사 대상이다. 그 외 사건은 decision도 None이어야 한다. 유죄 재생성의 고정 형량 누락 등 불일치는 모델 호출 전 실패시킨다.
- [x] `evaluator_schema(..., include_sentencing_checks=False)`는 LLM 출력에서 두 형량 검사를 제외한다. 기본값 True로 기존 호출을 보존한다.
- [x] 정상적인 무형량 사건에 한해 코드가 공개 보고서의 두 형량 검사에 `pass=True, violations=[]`를 채운다. 실제 문구 보고서는 계속 엄격히 검증한다.
- [x] 정상 무형량·반려 문구·불완전 문구·유죄 검사 유지·분리 검수·TEXT_RETRY 불변을 검증한다.

```python
assert "sentence_check" not in evaluator_request_schema["properties"]
assert finalized.sentencing is None
assert finalized.evaluation.sentence_check.pass_ is True
```

실행: `.venv/bin/python -m pytest tests/contracts tests/unit/test_graph_c_evaluation.py tests/unit/test_graph_c.py tests/unit/test_fake_llm.py -q`

### 3.3 검수에도 근거의 성격 보존

파일: `src/geoji_ai/graphs/sentencing.py`, `tests/unit/test_graph_c_evaluation.py`, `tests/unit/test_verdict_quality.py`, `tests/unit/test_agent_handoff.py`.

- [x] 서기와 검수 입력의 USER_CLAIM/DB_RECORD/MODEL_INFERENCE가 동일하게 보존되는 테스트를 먼저 실패시킨다.
- [x] 기존 `evidence: {label: text}`는 유지하고 `evidence_metadata`에 라벨별 epistemic_type·fact_type을 추가한다.
- [x] 사용자 사유/추론을 새 DB 사실로 변환하거나 외부 식별자를 추가 노출하지 않는다.

```python
assert evaluator_payload["evidence_metadata"]["F0"]["epistemic_type"] == "USER_CLAIM"
assert evaluator_payload["evidence"]["F0"] == writer_payload["dossier"][0]["text"]
```

실행: `.venv/bin/python -m pytest tests/unit/test_graph_c_evaluation.py tests/unit/test_verdict_quality.py tests/unit/test_graph_c.py -q`

### 3.4 골든셋 타입 변환과 운영 캐시 회귀 보강

파일: `tests/evaluations/run_regression.py`, `tests/unit/test_golden_eval.py`.

- [x] 반복 태그 사례에서 REPETITION/FUTURE_PROPHECY가 허용되는 테스트와 F0 사용자 주장·분석 추론 타입 테스트를 먼저 실패시킨다.
- [x] 골든셋 어댑터에서 THIS_CASE→SPEND/USER_CLAIM, PATTERN·PRIOR_REASON→SPEND/DB_RECORD, PRIOR_VERDICT→VERDICT/DB_RECORD, RULE_HIT→RULE/DB_RECORD, STATUS→AGGREGATE/DB_RECORD, REASON_ANALYSIS·MITIGATION→MITIGATION/MODEL_INFERENCE로 명시 매핑한다. 입력 텍스트/라벨은 유지한다.
- [x] 평가 파일의 원래 kind는 fixture 문맥으로 유지한다. 운영 분류에 없는 종류를 사실로 묵인하지 않고 명시적으로 거부한다.
- [x] 초기 반려 이후 별도 job/round와 동일 job 재실행은 3.1의 실제 게이트웨이 회귀가 담당한다. FakeLLM 골든셋 점수를 의미 품질 통과로 보고하지 않는다.

```python
assert dossier.facts[0].epistemic_type == "USER_CLAIM"
assert "REPETITION" in [angle.value for angle in writer_angles(case.full_snapshot, dossier)]
```

실행: `.venv/bin/python -m pytest tests/unit/test_golden_eval.py tests/unit/test_graph_c_cache.py -q`

## 4. 최종 검증과 완료 기준

- [x] 관련 실패 재현이 수정 후 통과한다. 무효 epoch·시간/비용 부족·검수 실패·hash 일치 기존 검증을 유지한다.
- [x] `.venv/bin/python -m pytest -q --ignore=tests/integration`
- [x] `.venv/bin/ruff check .` / `.venv/bin/ruff format --check .` 실행 후 기존 범위 밖 오류는 분리 보고한다. 수정 파일은 전부 통과해야 한다.
- [x] `git diff --check`
- [x] 독립 검증 담당이 계획서 케이스와 테스트를 대조한다.
- [x] 수정 파일/검증 결과/운영 검증 한계를 기록한다.

실제 Postgres 검증은 전용 TEST_DATABASE_URL이 있을 때만 수행한다. 실제 모델의 오반려율·문구 품질·지연·비용 개선은 별도 실측 대상이다. 배포 후에는 `llm_node_result_reused`, `sentence_call`, `sentence_fallback`, `sentence_summary`를 job/강도별로 대조해 새 round가 실제로 호출되는지 확인한다.

## 5. 실행 기록

2026-09-20 계획 작성. 수정 전 기준선: 계약·단위 1,405개 통과, `ruff check src tests` 통과(앞선 분석 단계). 현재 작업은 로컬 `codex/fix-repeated-rejection` 브랜치에서 수행한다.

- 명세 대조: `_workspace/rejection-fix/01_spec.md`. 네 단계 모두 외부 계약·DB·포트 변경 없이 진행 가능.
- 전체 Ruff 기준선: `outputs/validation/20260919-five-cases/run_five_cases.py`의 기존 lint 17건과 format 1파일 실패. 범위 밖 파일은 유지한다.
- 형량 적용 조건·근거 전달 RED: `test_graph_c_evaluation.py` 10 failed / 7 passed. 정상 무형량 6건, 유죄 재시도 고정 형량 누락 1건, 무형량 사건의 불필요 형량 2건, 근거 메타데이터 1건이 의도한 assertion으로 실패했다.


### 구현·검증 완료

- §3.1~3.4 순차 완료. 캐시/형량 적용 조건/근거 메타데이터/골든셋 타입을 수정했고 공개 계약·DB·모델·예산 상한은 유지했다.
- 구현 기록: `_workspace/rejection-fix/02_impl_cache.md`, `_workspace/rejection-fix/02_impl_evaluation.md`.
- 독립 검증: `_workspace/rejection-fix/03_test.md`. 초기 전체 게이트에서 기존 handoff 테스트의 신규 입력 키 미반영 1건을 발견하여 키·내용 assertion을 갱신했다. 무형량 malformed pass/violation 3건과 골든셋 8종 매핑도 보강했다.
- 최종 `env -u TEST_DATABASE_URL .venv/bin/python -m pytest -q --ignore=tests/integration`: **1,513 passed**, 1 warning(Starlette TestClient의 AnyIO deprecation), 24.45초.
- 변경 Python 10파일 Ruff 검사·포맷 및 `git diff --check` 통과. 전체 Ruff는 앞서 기록한 범위 밖 파일의 lint 17건·format 1파일만 실패한다.
- 전용 TEST_DATABASE_URL이 없어 Postgres 통합 테스트는 실행하지 않았다. 전용 테스트 DB 설정 후 `.venv/bin/python -m pytest tests/integration -q`로 검증할 수 있다.
- 실제 모델·운영 DB 호출, 배포, commit/push는 수행하지 않았다. 실제 반려율·문구 품질·비용·지연 개선은 아직 측정하지 않았다.
- 새 job/round/422 보정은 모델을 다시 호출할 수 있고, 같은 job의 통신 재실행은 캐시를 재사용한다. 새로운 호출의 추가 비용은 기존 예산 내에서 제한된다.

## 6. 후속 실측 참고

9/20 팀원 공유 [검수 반려 심사대](https://claude.ai/artifact/DCxS4NHUh2wx6GPXYugDsp)를 확인했다. 자료 표기는 골든셋 50사건, GUARDRAIL-V2.5, BUNDLE-09839941837C이며 실패 33사건, 반려 항목 131건(UNGROUNDED_CLAIM 71·INTENSITY_MISMATCH 49·VERDICT_CONTRADICTION 8·SELF_HARM_LEXICON 3), 서기 166호출·검수 99호출·총 265초다. 직접 재실행한 측정값은 아니다. 반려 항목은 같은 사건·강도·재작성·경로에서 중복될 수 있어 사건 반려율로 계산하지 않는다. 자료의 33/50=66%도 해당 실행의 실패율이며 운영 반려율을 뜻하지 않는다.

- 사실 오류와 오반려를 나눠 평가한다. 예를 들어 월간 예산 소진율을 단일 구매의 지출 비율로 바꾸거나 구매 횟수를 동일 사유의 반복 횟수로 바꾸는 것은 사실 오류다. 조건부 표현·비유를 사실 단정으로 읽은 사례는 사람 판정으로 확인할 후보이며 일괄 허용하지 않는다.
- 동일 사건·모델·프롬프트 번들·강도·재시도 조건으로 수정 전후를 비교한다. 자료의 검수 프롬프트 버전은 현재 checkout과 대조하고, 버전이 다른 기존 수치를 그대로 기준선으로 사용하지 않는다.
- 첫 검수/보정 후/최종 사건 성공률과 강도별 성공률, 사람이 확인한 오반려·위반 누락, 재미·자연스러움·강도 적합도를 함께 측정한다. 반려된 초안만으로 위반 누락률을 추정하지 않고 통과 초안도 표본 검수한다.
- 토큰 사용량·실제 호출 수·재시도 수·전체 비용·성공 사건당 비용·사건별 지연 p50/p95를 기록한다. 자료의 총 265초와 호출 수만으로 사건별 지연이나 비용을 확정하지 않는다.
- 실제 모델 실행은 표본과 비용 상한을 정한 뒤 별도로 수행한다. 이번 커밋·푸시 단계에서는 유료 호출하지 않는다.

## 7. 9/20 발견 — 근거 없는 사건에서 hell 강도 요구가 무조건이다 (미검증, 동결 이후)

빈 방 골든셋(`tests/evaluations/golden/thin_evidence.jsonl`)을 만들며 실모델로 확인했다. **프롬프트는 고치지 않았다.**

**현상.** 아래 세 조건이 동시에 성립하면 검수 통과 영역이 좁아져 판결문이 `EVAL_FAILED` 로 끝난다.

1. 조서에 `F0`(이번 지출) 하나뿐 — 과거·판결·방 규칙 근거 0
2. 평결이 `notGuilty` 또는 `agree`
3. 방 강도가 `hell`

1차 초안은 `INTENSITY_MISMATCH`("hell 인데 면박이 없다"), 재작성 초안은 `VERDICT_CONTRADICTION`("무죄 사유를 사치·핑계로 비난했다")으로 반려된다. 재작성 상한이 1회라 그다음은 TEMPLATE 치환이고, 대상 강도가 하나면 `all(TEMPLATE)` 이라 `EVAL_FAILED:ALL_TEMPLATE` 이다.

**어긋난 자리.** `guardrail-v2.7` 의 `INTENSITY_MISMATCH` 는 spicy 절에만 조건을 달고 hell 절에는 달지 않는다.

> 놀릴 근거가 있는데 spicy가 단순 지적·상담에 머물고 …, hell이 가벼운 장난·되묻기에 그쳐 핑계의 허점을 찌르는 면박이 없다.

면제는 네 줄 뒤 별도 문장(`정당한 필요 소비·사유 부족으로 놀릴 근거가 없으면 억지 조롱을 요구하지 않는다`)으로 있는데, 실제 반려문은 그것을 적용하지 않고 hell 절을 그대로 읽었다. 서기 쪽도 같은 모양이다. `writer/hell-v6.4.md` 는 무조건문으로 `선택의 모순을 조롱하는 반응이 남아야 한다`·`친절한 위로·교훈으로 마지막에 수위를 낮추지 않는다` 를 요구하면서 같은 파일에 `승인·무죄에도 퉁명스러울 수 있지만 필요성은 인정한다` 를, `writer/common-v6.4.md` 는 `놀릴 근거가 없으면 roast_target=null이다` 를 둔다. 정책 의도는 양쪽 다 일치한다. 어긋난 것은 **조건이 문장에 붙어 있는지**이고, 근거가 있으면 두 요구를 동시에 만족할 수 있어 드러나지 않는다.

`VERDICT_CONTRADICTION` 판정 자체는 정확했다. 고칠 대상이 아니다.

**측정(9/20, `bundle-8e868116333c`·`guardrail-v2.7`·서기 `grok-4.20-0309-non-reasoning`·검수 `gpt-5.6-luna`).**

| 사건 | 근거 수 | 평결 | 강도 | 결과 |
|---|---|---|---|---|
| 빈 방 `t02`(당시 hell) | 1 | `notGuilty` | `hell` | 5회 중 **2회 `EVAL_FAILED`** |
| `hell_boundary` `h13`·`h16`·`h17`·`h20` | 2 | `notGuilty`·`agree` | `hell` | 4회 중 **4회 통과**(`h16` 만 재작성 1회, `PERSONAL_ATTACK`) |

기존 골든 4건이 전부 통과하므로 조건 2·3 만으로는 재현되지 않는다. 표본이 4대 5라 인과는 단정하지 않는다. 통과한 `t02` 2회는 모두 조서에 없는 반복(`또 반복하지 말고`·`매번 이렇게 … 반복할 거야?`)을 지어내 빠져나갔다. `UNGROUNDED_CLAIM` 판정을 끈 뒤(PR #67)의 맞바꿈이 드러난 자리다.

**제안(적용 전).** 문구만 고치고 정책은 그대로 둔다. `policy_version` 은 `guardrail-v2` 유지, 안전 코드는 건드리지 않는다.

| # | 파일 | 변경 |
|---|---|---|
| 1 | `prompts/evaluator/guardrail-v2.8.md` | hell 절에 spicy 와 같은 조건을 붙인다 — `놀릴 근거가 있는데 hell이 가벼운 장난·되묻기에 그쳐 …` |
| 2 | `prompts/writer/hell-v6.5.md` | `선택의 모순을 조롱하는 반응이 남아야 한다` → `입력에 모순이 있으면 그것을 조롱하는 반응이 남아야 한다. 없으면 퉁명스러운 인정으로 끝낸다` |

**지금 적용하지 않은 이유.** 이 실패는 LLM 판정이라 **로컬에서 검증할 수 없다.** `FakeLLM` 은 고정 fixture 를 돌려주므로 `--dry-run` 골든셋으로는 `INTENSITY_MISMATCH` 가 줄었는지 알 수 없고, `testing` 룰이 프롬프트 변경에 골든셋 회귀와 사람 검수 두 관문을 요구한다. `guardrail-v2.3` 도 같은 종류의 완화였는데 골든셋 회귀에서 더 나빠져 되돌렸다(`6670cfa`). 9/20 동결일이라 유료 회귀를 돌리지 않았다.

**다음에 할 일.** 같은 사건·모델·번들·강도로 v2.7 기준선과 v2.8 을 각각 골든셋 50건 1회씩 돌린다. `INTENSITY_MISMATCH` 횟수·사건 실패 수·judge 강도 적합을 대조하고, 빈 방은 `--only-thin-evidence` 로 따로 본다(`t02`·`t03` 을 `hell` 로 되돌려 재현부터 만든다). 오반려가 줄고 위반 누락이 늘지 않았는지는 §6 기준대로 사람이 확인한다.

**`all(TEMPLATE)` 은 버그가 아니다.** 05 §3 에 적힌 설계이고, `10 §4.6` 에 따라 `EVAL_FAILED` 를 받은 백엔드가 즉시 폴백(형량 `fallback_sentence`, 문구 TEMPLATE)한 뒤 `TEXT_RETRY` round 1 을 예약한다. 사용자 화면은 "판결 없음" 이 아니라 "AI 문구 대신 고정 문구 + 재시도" 다.
