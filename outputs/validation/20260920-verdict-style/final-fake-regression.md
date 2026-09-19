# 골든셋 회귀 리포트

판정: **FAIL**

- 자동 검사 위반 52건
- judge 관련성 3.00 < 기준 4.0
- judge 거지방다움 3.00 < 기준 4.0
- judge 재미 3.00 < 기준 3.5
- judge 강도 적합 3.00 < 기준 4.0
- judge 납득 3.00 < 기준 4.0

## 실행

| 항목 | 값 |
|---|---|
| bundle_version | bundle-24a2318ecfdf |
| policy_version | guardrail-v2 |
| dry_run | True |
| quick | False |
| only_hell | False |
| cases | 50 |
| writer_model | grok-4.20-0309-non-reasoning |
| judgment_model(judge·검수) | gpt-5.6-luna |
| evaluator_hell_model | gpt-5.6-luna |
| temperature | 미적용 |
| graph_calls | {'evaluator': 50, 'writer': 110} |
| judge_calls | 110 |
| policy_fixture_calls | 1 |
| elapsed_s | 1.8 |

## 자동 검사

| 검사 | 위반 |
|---|---|
| schema | 0 |
| evidence | 0 |
| length | 0 |
| death_words | 0 |
| intensity_lexicon | 0 |
| verdict_contradiction | 0 |
| sentence_contradiction | 0 |
| expect | 51 |
| meme_emotion | 0 |
| headline_duplication | 1 |
| attack_angles | 0 |

headline 중복률 97.3% (상한 10%)

## judge

| 축 | 평균 | 기준 | 기준선 |
|---|---|---|---|
| 관련성 | 3.00 | ≥ 4.0 |  |
| 거지방다움 | 3.00 | ≥ 4.0 |  |
| 재미 | 3.00 | ≥ 3.5 |  |
| 강도 적합 | 3.00 | ≥ 4.0 |  |
| 납득 | 3.00 | ≥ 4.0 |  |

## 정책 fixture(01 부록 A)

`guardrail-v2`: 일치
- (열린 질문) 비속어는 닫힌 허용 목록 검사 대상이다. '들어쳐먹을래'는 목록 밖 변형이므로 PROFANITY_OUT_OF_LIST 후보이며, 목록 확정 후 기대값을 고정한다.

## 위반 목록

| 검사 | 사건 | 강도 | 내용 |
|---|---|---|---|
| expect | g01 | mild | 인용 없음: ['F1', 'F2', 'F3'] 중 하나 |
| expect | g01 | hell | 인용 없음: ['F1', 'F2', 'F3'] 중 하나 |
| expect | g02 | mild | 인용 없음: ['F1'] 중 하나 |
| expect | g02 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g02 | hell | 인용 없음: ['F1'] 중 하나 |
| expect | g03 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g04 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g05 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g06 | mild | 인용 없음: ['F1', 'F2', 'F3'] 중 하나 |
| expect | g06 | hell | 인용 없음: ['F1', 'F2', 'F3'] 중 하나 |
| expect | g07 | mild | 인용 없음: ['F1'] 중 하나 |
| expect | g07 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g07 | hell | 인용 없음: ['F1'] 중 하나 |
| expect | g08 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g09 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g10 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g11 | mild | 인용 없음: ['F1', 'F2', 'F3'] 중 하나 |
| expect | g11 | hell | 인용 없음: ['F1', 'F2', 'F3'] 중 하나 |
| expect | g12 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g13 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g14 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g15 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g16 | mild | 인용 없음: ['F1'] 중 하나 |
| expect | g16 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g16 | hell | 인용 없음: ['F1'] 중 하나 |
| expect | g17 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g18 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g19 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g20 | mild | 인용 없음: ['F1', 'F2', 'F3'] 중 하나 |
| expect | g20 | hell | 인용 없음: ['F1', 'F2', 'F3'] 중 하나 |
| expect | g21 | mild | 인용 없음: ['F1'] 중 하나 |
| expect | g21 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g21 | hell | 인용 없음: ['F1'] 중 하나 |
| expect | g22 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g23 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g24 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g25 | mild | 인용 없음: ['F1'] 중 하나 |
| expect | g25 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g25 | hell | 인용 없음: ['F1'] 중 하나 |
| expect | g26 | mild | 인용 없음: ['F1', 'F2', 'F3'] 중 하나 |
| expect | g26 | hell | 인용 없음: ['F1', 'F2', 'F3'] 중 하나 |
| expect | g27 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g28 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g29 | mild | 인용 없음: ['F1'] 중 하나 |
| expect | g29 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | g29 | hell | 인용 없음: ['F1'] 중 하나 |
| expect | g30 | spicy | 전략 'REPEAT_OFFENSE' 가 허용 밖 |
| expect | h09 | hell | 인용 없음: ['F1'] 중 하나 |
| expect | h14 | hell | 인용 없음: ['F1'] 중 하나 |
| expect | h15 | hell | 인용 없음: ['F1'] 중 하나 |
| expect | h19 | hell | 인용 없음: ['F1', 'F2'] 중 하나 |
| headline_duplication | - | - | 중복률 97.3% > 10% |

## 판결문

| 사건 | 강도 | 출처 | 각도 | 전략 | headline |
|---|---|---|---|---|---|
| g01 | mild | AI | CONVERSION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g01 | spicy | AI | CONVERSION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g01 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g02 | mild | AI | CONVERSION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g02 | spicy | AI | CONVERSION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g02 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g03 | mild | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g03 | spicy | AI | EXCUSE_DISSECTION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g03 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g04 | mild | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g04 | spicy | AI | EXCUSE_DISSECTION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g04 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g05 | mild | AI | CONVERSION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g05 | spicy | AI | CONVERSION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g05 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g06 | mild | AI | CONVERSION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g06 | spicy | AI | CONVERSION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g06 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g07 | mild | AI | CONVERSION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g07 | spicy | AI | CONVERSION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g07 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g08 | mild | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g08 | spicy | AI | EXCUSE_DISSECTION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g08 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g09 | mild | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g09 | spicy | AI | EXCUSE_DISSECTION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g09 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g10 | mild | AI | CONVERSION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g10 | spicy | AI | CONVERSION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g10 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g11 | mild | AI | CONVERSION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g11 | spicy | AI | CONVERSION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g11 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g12 | mild | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g12 | spicy | AI | EXCUSE_DISSECTION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g12 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g13 | mild | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g13 | spicy | AI | EXCUSE_DISSECTION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g13 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g14 | mild | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g14 | spicy | AI | EXCUSE_DISSECTION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g14 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g15 | mild | AI | CONVERSION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g15 | spicy | AI | CONVERSION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g15 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g16 | mild | AI | CONVERSION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g16 | spicy | AI | CONVERSION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g16 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g17 | mild | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g17 | spicy | AI | EXCUSE_DISSECTION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g17 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g18 | mild | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g18 | spicy | AI | EXCUSE_DISSECTION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g18 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g19 | mild | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g19 | spicy | AI | EXCUSE_DISSECTION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g19 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g20 | mild | AI | CONVERSION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g20 | spicy | AI | CONVERSION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g20 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g21 | mild | AI | CONVERSION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g21 | spicy | AI | CONVERSION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g21 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g22 | mild | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g22 | spicy | AI | EXCUSE_DISSECTION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g22 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g23 | mild | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g23 | spicy | AI | EXCUSE_DISSECTION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g23 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g24 | mild | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g24 | spicy | AI | EXCUSE_DISSECTION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g24 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g25 | mild | AI | CONVERSION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g25 | spicy | AI | CONVERSION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g25 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g26 | mild | AI | CONVERSION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g26 | spicy | AI | CONVERSION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g26 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g27 | mild | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g27 | spicy | AI | EXCUSE_DISSECTION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g27 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g28 | mild | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g28 | spicy | AI | EXCUSE_DISSECTION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g28 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g29 | mild | AI | CONVERSION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g29 | spicy | AI | CONVERSION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g29 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| g30 | mild | AI | CONVERSION | CHEAPER_ALTERNATIVE | 택시 12,000원, 유죄 |
| g30 | spicy | AI | CONVERSION | REPEAT_OFFENSE | 또 늦잠, 또 택시 |
| g30 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h01 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h02 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h03 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h04 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h05 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h06 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h07 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h08 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h09 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h10 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h11 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h12 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h13 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h14 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h15 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h16 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h17 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h18 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h19 | hell | AI | CONVERSION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
| h20 | hell | AI | EXCUSE_DISSECTION | CHEAPER_ALTERNATIVE | 늦잠값이 지하철 여덟 번 |
