# 에이전트 핸드오프 추적

- 사건: 택시 12,000원 / 교통/택시 / 사유 '늦잠 자서 택시 탐' / spent
- 배심: guilty {'guilty': 3, 'notGuilty': 1} 유죄율 0.75 / 강도 ['mild', 'spicy', 'hell']
- 결과: **AI_READY**
- 프롬프트 번들: bundle-a6ababef067d

## A. 심문관

```json
{
  "schema_version": 1,
  "mode": "INITIAL",
  "status": "PASS",
  "item_review": {
    "status": "OK",
    "suggested_item": null
  },
  "message": null,
  "category_review": {
    "status": "OK",
    "suggested_category": null,
    "confidence": 1.0
  },
  "injection_detected": false,
  "intake_source": "AI"
}
```
- 이 결과는 조서 키(캐시 무효화)에만 쓰이고 **어느 프롬프트에도 들어가지 않는다**.

## B. 조서·드립

- 상태: COMPLETE / 오류: []
- 기억 후보(recall): 0건

### 조서(최종, 그래프 C 가 받는 것)
| 라벨 | 종류 | 출처 | 문장 |
|---|---|---|---|
| F0 | SPEND | USER_CLAIM | 이번 지출: 택시 12,000원, 카테고리 교통/택시, 사유 '늦잠 자서 택시 탐'. 지하철 기본요금 1,400원 기준 약 8회분. |
| F1 | AGGREGATE | DB_RECORD | 이번 달 예산 소진율 41%, 티어 상거지, 무지출 3일. |
| F2 | AGGREGATE | DB_RECORD | 최근 30일 같은 카테고리 확정 소비 3건. |
| F3 | RULE | DB_RECORD | 방 규칙 '한 달에 택시 1번'. |
| F4 | VERDICT | DB_RECORD | 지난 판결(9/2): 교통/택시 9,500원, 사유 '늦잠', 결과 guilty, 형량 probation. |
| F5 | SPEND | DB_RECORD | 지난 지출: 교통/택시 8,800원, 사유 '비 와서'. |
| F6 | RULE | MODEL_INFERENCE | 방 규칙은 한 달에 택시 1번이며, 이번 지출은 택시 12,000원이다. |
| F7 | MITIGATION | MODEL_INFERENCE | 이번 달 예산 소진율은 41%이고, 무지출 기간은 3일이다. |

### 조서 LLM 의 사유 분석(reason_analysis)
```json
{
  "has_mitigation": true,
  "mitigation_kind": "예산·무지출 상황",
  "injection_suspected": false,
  "facts": [
    "사유는 늦잠으로 인해 택시를 이용했다는 내용이다."
  ]
}
```
- **저장되지 않고 양형관·서기에게도 가지 않는다**(PrepareState 에만 남는다).

### 드립 후보(필터 뒤, 그래프 C 가 받는 것)
- spicy: 5개
  - [CHEAPER_ALTERNATIVE] fits=['guilty', 'disagree'] labels=['F0'] — 지하철 8회분을 한 번에 태워버린 레전드 선택이네요. 12,000원이면 버스+지하철 조합으로도 충분히 제시간에 도착할 수 있었을 텐데, 늦잠 핑계로 편함만 택한 건 좀 아쉽지 않나요?
  - [EXCUSE_STRIPPING] fits=['guilty', 'disagree'] labels=['F1', 'F7'] — 이번 달 이미 41%나 소진됐고 무지출도 고작 3일인데, '늦잠'으로 12,000원을 또 태우는 게 정말 필요했다고 보시나요? 숫자가 말해주는 현실이 꽤 냉정하네요.
  - [ROOM_RULE_CALLBACK] fits=['guilty', 'agree'] labels=['F3', 'F2', 'F6'] — 방 규칙이 '한 달에 택시 1번'인데, 최근 30일에만 같은 카테고리로 3건이나 확정 소비를 했군요. 이번 건은 규칙을 다시 한번 상기시켜드릴 때인 것 같습니다.
  - [REPEAT_OFFENSE] fits=['guilty', 'agree'] labels=['F4', 'F0'] — 9월 2일에 '늦잠' 사유로 9,500원 guilty 받고 probation 받으신 분이, 불과 얼마 지나지 않아 또 '늦잠'으로 12,000원을 쓰셨네요. 반복이면 좀 다르게 판단해야 하지 않을까요?
  - [PREMISE_REJECTION] fits=['notGuilty', 'disagree'] labels=['F5', 'F0'] — 비 와서 8,800원, 늦잠 자서 12,000원… 핑계가 바뀌어도 금액은 계속 올라가고 있네요. 진짜 '필요'였을까요, 아니면 그냥 편한 선택이었을까요?

## C. 선고

- 조서 출처: PREP / 양형 출처: AI / 재작성: 1 / 실패: None
- 양형: oneDay — 유죄율 75%에 최근 30일 택시 3건과 월 1회 규칙 위반이 있고 늦잠 사유도 반복되어 하루 형으로 한다. (근거 ['F0', 'F1', 'F2', 'F3', 'F4', 'F6'], 출처 AI)
- mild [AI] 연말 잔고 예언 — 이 속도면 연말엔 지하철로만 다니게 될 거예요. (각도 FUTURE_PROPHECY, 전략 PREMISE_REJECTION, 후보 None)
- spicy [AI] 연말엔 지하철로 출근 — 이 속도면 12월 잔고가 택시비로 먼저 사망한다. (각도 FUTURE_PROPHECY, 전략 PREMISE_REJECTION, 후보 None)
- hell [AI] 규칙이 절교 선언했다 — 늦잠 또 자서 택시 타는 새끼랑은 나도 끝이다. (각도 RULE_PERSONIFICATION, 전략 ROOM_RULE_CALLBACK, 후보 None)
- 검수 mild: pass=True []
- 검수 spicy: pass=True []
- 검수 hell: pass=True []

## 모델 호출 순서

### 1. [A.intake] intake

- 시스템 프롬프트 첫 줄: 당신은 소비 재판 서비스 '떼거지'의 심문관이다. 지출이 등록되기 직전에 입력이 재판에 올릴 만한지 검사만 한다. (3266자) / timeout 3.8s / 출력 상한 300

입력(사용자 메시지):
```json
{
  "post_type": "spent",
  "amount_krw": 12000,
  "item": "택시",
  "category": "교통/택시",
  "reason": "늦잠 자서 택시 탐",
  "injection_hint": false,
  "mode": "INITIAL"
}
```
출력:
```json
{
  "mode": "INITIAL",
  "status": "PASS",
  "item_review": {
    "status": "OK",
    "suggested_item": null
  },
  "message": null,
  "category_review": {
    "status": "OK",
    "suggested_category": null,
    "confidence": 1.0
  },
  "injection_detected": false
}
```

### 2. [B.prepare] context

- 시스템 프롬프트 첫 줄: 당신은 소비 재판 서비스 '떼거지'의 조서 담당이다. 사건과 코드가 뽑은 사실(라벨)과 사유 원문을 받아 정리만 한다. (674자) / timeout 10.0s / 출력 상한 1500

입력(사용자 메시지):
```json
{
  "case": {
    "item": "택시",
    "amount_krw": 12000,
    "category": "교통/택시",
    "post_type": "spent"
  },
  "facts": [
    {
      "label": "F0",
      "text": "이번 지출: 택시 12,000원, 카테고리 교통/택시, 사유 '늦잠 자서 택시 탐'. 지하철 기본요금 1,400원 기준 약 8회분."
    },
    {
      "label": "F1",
      "text": "이번 달 예산 소진율 41%, 티어 상거지, 무지출 3일."
    },
    {
      "label": "F2",
      "text": "최근 30일 같은 카테고리 확정 소비 3건."
    },
    {
      "label": "F3",
      "text": "방 규칙 '한 달에 택시 1번'."
    },
    {
      "label": "F4",
      "text": "지난 판결(9/2): 교통/택시 9,500원, 사유 '늦잠', 결과 guilty, 형량 probation."
    },
    {
      "label": "F5",
      "text": "지난 지출: 교통/택시 8,800원, 사유 '비 와서'."
    }
  ],
  "reason": "늦잠 자서 택시 탐"
}
```
출력:
```json
{
  "facts": [
    {
      "kind": "RULE_HIT",
      "text": "방 규칙은 한 달에 택시 1번이며, 이번 지출은 택시 12,000원이다.",
      "source_refs": [
        "F0",
        "F3"
      ]
    },
    {
      "kind": "MITIGATION",
      "text": "이번 달 예산 소진율은 41%이고, 무지출 기간은 3일이다.",
      "source_refs": [
        "F1"
      ]
    },
    {
      "kind": "REASON_ANALYSIS",
      "text": "사유는 늦잠으로 인해 택시를 이용했다는 내용이다.",
      "source_refs": [
        "F0"
      ]
    }
  ],
  "reason_analysis": {
    "has_mitigation": true,
    "mitigation_kind": "예산·무지출 상황",
    "injection_suspected": false
  }
}
```

### 3. [B.prepare] banter

- 시스템 프롬프트 첫 줄: 당신은 소비 재판 서비스 '떼거지'의 드립 후보 담당이다. 요청된 강도 하나에 대해 판결문 서기가 고를 드립 후보를 만든다. (1150자) / timeout 10.0s / 출력 상한 1200

입력(사용자 메시지):
```json
{
  "post_type": "spent",
  "category": "교통/택시",
  "intensity": "spicy",
  "evidence": [
    {
      "label": "F0",
      "text": "이번 지출: 택시 12,000원, 카테고리 교통/택시, 사유 '늦잠 자서 택시 탐'. 지하철 기본요금 1,400원 기준 약 8회분."
    },
    {
      "label": "F1",
      "text": "이번 달 예산 소진율 41%, 티어 상거지, 무지출 3일."
    },
    {
      "label": "F2",
      "text": "최근 30일 같은 카테고리 확정 소비 3건."
    },
    {
      "label": "F3",
      "text": "방 규칙 '한 달에 택시 1번'."
    },
    {
      "label": "F4",
      "text": "지난 판결(9/2): 교통/택시 9,500원, 사유 '늦잠', 결과 guilty, 형량 probation."
    },
    {
      "label": "F5",
      "text": "지난 지출: 교통/택시 8,800원, 사유 '비 와서'."
    },
    {
      "label": "F6",
      "text": "방 규칙은 한 달에 택시 1번이며, 이번 지출은 택시 12,000원이다."
    },
    {
      "label": "F7",
      "text": "이번 달 예산 소진율은 41%이고, 무지출 기간은 3일이다."
    }
  ],
  "approved_examples": []
}
```
출력:
```json
{
  "candidates": [
    {
      "text": "지하철 8회분을 한 번에 태워버린 레전드 선택이네요. 12,000원이면 버스+지하철 조합으로도 충분히 제시간에 도착할 수 있었을 텐데, 늦잠 핑계로 편함만 택한 건 좀 아쉽지 않나요?",
      "strategy": "CHEAPER_ALTERNATIVE",
      "fits": [
        "guilty",
        "disagree"
      ],
      "evidence_labels": [
        "F0"
      ]
    },
    {
      "text": "이번 달 이미 41%나 소진됐고 무지출도 고작 3일인데, '늦잠'으로 12,000원을 또 태우는 게 정말 필요했다고 보시나요? 숫자가 말해주는 현실이 꽤 냉정하네요.",
      "strategy": "EXCUSE_STRIPPING",
      "fits": [
        "guilty",
        "disagree"
      ],
      "evidence_labels": [
        "F1",
        "F7"
      ]
    },
    {
      "text": "방 규칙이 '한 달에 택시 1번'인데, 최근 30일에만 같은 카테고리로 3건이나 확정 소비를 했군요. 이번 건은 규칙을 다시 한번 상기시켜드릴 때인 것 같습니다.",
      "strategy": "ROOM_RULE_CALLBACK",
      "fits": [
        "guilty",
        "agree"
      ],
      "evidence_labels": [
        "F3",
        "F2",
        "F6"
      ]
    },
    {
      "text": "9월 2일에 '늦잠' 사유로 9,500원 guilty 받고 probation 받으신 분이, 불과 얼마 지나지 않아 또 '늦잠'으로 12,000원을 쓰셨네요. 반복이면 좀 다르게 판단해야 하지 않을까요?",
      "strategy": "REPEAT_OFFENSE",
      "fits": [
        "guilty",
        "agree"
      ],
      "evidence_labels": [
        "F4",
        "F0"
      ]
    },
    {
      "text": "비 와서 8,800원, 늦잠 자서 12,000원… 핑계가 바뀌어도 금액은 계속 올라가고 있네요. 진짜 '필요'였을까요, 아니면 그냥 편한 선택이었을까요?",
      "strategy": "PREMISE_REJECTION",
      "fits": [
        "notGuilty",
        "disagree"
      ],
      "evidence_labels": [
        "F5",
        "F0"
      ]
    }
  ]
}
```

### 4. [C.sentence] sentencing

- 시스템 프롬프트 첫 줄: 당신은 소비 재판 서비스 '떼거지'의 양형관이다. 배심원(친구들)이 이미 유죄를 확정했다. (720자) / timeout 12.0s / 출력 상한 2000

입력(사용자 메시지):
```json
{
  "case": {
    "item": "택시",
    "amount_krw": 12000,
    "category": "교통/택시",
    "reason": "늦잠 자서 택시 탐",
    "post_type": "spent"
  },
  "jury": {
    "result": "guilty",
    "vote_counts": {
      "guilty": 3,
      "notGuilty": 1
    },
    "guilty_ratio": 0.75,
    "policy": {
      "allowed_sentences": [
        {
          "code": "probation",
          "rank": 1
        },
        {
          "code": "oneDay",
          "rank": 2
        }
      ]
    }
  },
  "dossier": [
    {
      "id": "F0",
      "text": "이번 지출: 택시 12,000원, 카테고리 교통/택시, 사유 '늦잠 자서 택시 탐'. 지하철 기본요금 1,400원 기준 약 8회분."
    },
    {
      "id": "F1",
      "text": "이번 달 예산 소진율 41%, 티어 상거지, 무지출 3일."
    },
    {
      "id": "F2",
      "text": "최근 30일 같은 카테고리 확정 소비 3건."
    },
    {
      "id": "F3",
      "text": "방 규칙 '한 달에 택시 1번'."
    },
    {
      "id": "F4",
      "text": "지난 판결(9/2): 교통/택시 9,500원, 사유 '늦잠', 결과 guilty, 형량 probation."
    },
    {
      "id": "F5",
      "text": "지난 지출: 교통/택시 8,800원, 사유 '비 와서'."
    },
    {
      "id": "F6",
      "text": "방 규칙은 한 달에 택시 1번이며, 이번 지출은 택시 12,000원이다."
    },
    {
      "id": "F7",
      "text": "이번 달 예산 소진율은 41%이고, 무지출 기간은 3일이다."
    }
  ]
}
```
출력:
```json
{
  "sentence": "oneDay",
  "sentencing_reason": "유죄율 75%에 최근 30일 택시 3건과 월 1회 규칙 위반이 있고 늦잠 사유도 반복되어 하루 형으로 한다.",
  "evidence_labels": [
    "F0",
    "F1",
    "F2",
    "F3",
    "F4",
    "F6"
  ],
  "aggravating": [
    "최근 30일 같은 카테고리 소비 3건",
    "월 1회 택시 규칙 위반",
    "늦잠 사유 반복",
    "이전 택시 판결에서 probation"
  ],
  "mitigating": [
    "예산 소진율 41%",
    "무지출 3일"
  ]
}
```

### 5. [C.sentence] writer

- 시스템 프롬프트 첫 줄: 당신은 소비 재판 서비스 '떼거지'의 AI 판사 서기다. 배심원(친구들)이 이미 유무죄를 정했고, (3117자) / timeout 10.0s / 출력 상한 700

입력(사용자 메시지):
```json
{
  "intensity": "mild",
  "attack_angle": {
    "code": "FUTURE_PROPHECY",
    "label": "미래 예언",
    "instruction": "이 속도면 월말·연말에 어떻게 되는지 구체적으로 예언한다. 마무리는 예언의 날짜를 박는다."
  },
  "case": {
    "item": "택시",
    "amount_krw": 12000,
    "category": "교통/택시",
    "reason": "늦잠 자서 택시 탐",
    "post_type": "spent"
  },
  "jury": {
    "result": "guilty",
    "vote_counts": {
      "guilty": 3,
      "notGuilty": 1
    },
    "guilty_ratio": 0.75
  },
  "sentencing": {
    "schema_version": 1,
    "sentence": "oneDay",
    "sentencing_reason": "유죄율 75%에 최근 30일 택시 3건과 월 1회 규칙 위반이 있고 늦잠 사유도 반복되어 하루 형으로 한다.",
    "reason_source": "AI",
    "evidence_labels": [
      "F0",
      "F1",
      "F2",
      "F3",
      "F4",
      "F6"
    ],
    "aggravating": [
      "최근 30일 같은 카테고리 소비 3건",
      "월 1회 택시 규칙 위반",
      "늦잠 사유 반복",
      "이전 택시 판결에서 probation"
    ],
    "mitigating": [
      "예산 소진율 41%",
      "무지출 3일"
    ]
  },
  "dossier": [
    {
      "id": "F0",
      "text": "이번 지출: 택시 12,000원, 카테고리 교통/택시, 사유 '늦잠 자서 택시 탐'. 지하철 기본요금 1,400원 기준 약 8회분."
    },
    {
      "id": "F1",
      "text": "이번 달 예산 소진율 41%, 티어 상거지, 무지출 3일."
    },
    {
      "id": "F2",
      "text": "최근 30일 같은 카테고리 확정 소비 3건."
    },
    {
      "id": "F3",
      "text": "방 규칙 '한 달에 택시 1번'."
    },
    {
      "id": "F4",
      "text": "지난 판결(9/2): 교통/택시 9,500원, 사유 '늦잠', 결과 guilty, 형량 probation."
    },
    {
      "id": "F5",
      "text": "지난 지출: 교통/택시 8,800원, 사유 '비 와서'."
    },
    {
      "id": "F6",
      "text": "방 규칙은 한 달에 택시 1번이며, 이번 지출은 택시 12,000원이다."
    },
    {
      "id": "F7",
      "text": "이번 달 예산 소진율은 41%이고, 무지출 기간은 3일이다."
    }
  ],
  "banter_candidates": []
}
```
출력:
```json
{
  "intensity": "mild",
  "headline": "연말 잔고 예언",
  "statement": [
    {
      "text": "이 속도면 연말엔 지하철로만 다니게 될 거예요.",
      "kind": "opinion",
      "evidence_labels": [
        "F0"
      ]
    }
  ],
  "banter_strategy": "PREMISE_REJECTION",
  "selected_candidate_id": null,
  "attack_angle": "FUTURE_PROPHECY",
  "meme_tag": "GUILTY_LIGHT",
  "meme_hints": {
    "emotion": "RESIGNATION",
    "keywords": [
      "택시",
      "늦잠",
      "연말",
      "지하철"
    ]
  }
}
```

### 6. [C.sentence] writer

- 시스템 프롬프트 첫 줄: 당신은 소비 재판 서비스 '떼거지'의 AI 판사 서기다. 배심원(친구들)이 이미 유무죄를 정했고, (3298자) / timeout 10.0s / 출력 상한 700

입력(사용자 메시지):
```json
{
  "intensity": "spicy",
  "attack_angle": {
    "code": "FUTURE_PROPHECY",
    "label": "미래 예언",
    "instruction": "이 속도면 월말·연말에 어떻게 되는지 구체적으로 예언한다. 마무리는 예언의 날짜를 박는다."
  },
  "case": {
    "item": "택시",
    "amount_krw": 12000,
    "category": "교통/택시",
    "reason": "늦잠 자서 택시 탐",
    "post_type": "spent"
  },
  "jury": {
    "result": "guilty",
    "vote_counts": {
      "guilty": 3,
      "notGuilty": 1
    },
    "guilty_ratio": 0.75
  },
  "sentencing": {
    "schema_version": 1,
    "sentence": "oneDay",
    "sentencing_reason": "유죄율 75%에 최근 30일 택시 3건과 월 1회 규칙 위반이 있고 늦잠 사유도 반복되어 하루 형으로 한다.",
    "reason_source": "AI",
    "evidence_labels": [
      "F0",
      "F1",
      "F2",
      "F3",
      "F4",
      "F6"
    ],
    "aggravating": [
      "최근 30일 같은 카테고리 소비 3건",
      "월 1회 택시 규칙 위반",
      "늦잠 사유 반복",
      "이전 택시 판결에서 probation"
    ],
    "mitigating": [
      "예산 소진율 41%",
      "무지출 3일"
    ]
  },
  "dossier": [
    {
      "id": "F0",
      "text": "이번 지출: 택시 12,000원, 카테고리 교통/택시, 사유 '늦잠 자서 택시 탐'. 지하철 기본요금 1,400원 기준 약 8회분."
    },
    {
      "id": "F1",
      "text": "이번 달 예산 소진율 41%, 티어 상거지, 무지출 3일."
    },
    {
      "id": "F2",
      "text": "최근 30일 같은 카테고리 확정 소비 3건."
    },
    {
      "id": "F3",
      "text": "방 규칙 '한 달에 택시 1번'."
    },
    {
      "id": "F4",
      "text": "지난 판결(9/2): 교통/택시 9,500원, 사유 '늦잠', 결과 guilty, 형량 probation."
    },
    {
      "id": "F5",
      "text": "지난 지출: 교통/택시 8,800원, 사유 '비 와서'."
    },
    {
      "id": "F6",
      "text": "방 규칙은 한 달에 택시 1번이며, 이번 지출은 택시 12,000원이다."
    },
    {
      "id": "F7",
      "text": "이번 달 예산 소진율은 41%이고, 무지출 기간은 3일이다."
    }
  ],
  "banter_candidates": [
    {
      "id": "7299d857-0a20-4c9b-80b4-8144a4d33753",
      "text": "지하철 8회분을 한 번에 태워버린 레전드 선택이네요. 12,000원이면 버스+지하철 조합으로도 충분히 제시간에 도착할 수 있었을 텐데, 늦잠 핑계로 편함만 택한 건 좀 아쉽지 않나요?",
      "strategy": "CHEAPER_ALTERNATIVE",
      "evidence_labels": [
        "F0"
      ]
    },
    {
      "id": "ad5b1bce-4dcc-4429-9078-e2c279f291e8",
      "text": "이번 달 이미 41%나 소진됐고 무지출도 고작 3일인데, '늦잠'으로 12,000원을 또 태우는 게 정말 필요했다고 보시나요? 숫자가 말해주는 현실이 꽤 냉정하네요.",
      "strategy": "EXCUSE_STRIPPING",
      "evidence_labels": [
        "F1",
        "F7"
      ]
    },
    {
      "id": "39c5cdd9-b241-4de6-baef-fca232ec13e2",
      "text": "방 규칙이 '한 달에 택시 1번'인데, 최근 30일에만 같은 카테고리로 3건이나 확정 소비를 했군요. 이번 건은 규칙을 다시 한번 상기시켜드릴 때인 것 같습니다.",
      "strategy": "ROOM_RULE_CALLBACK",
      "evidence_labels": [
        "F3",
        "F2",
        "F6"
      ]
    },
    {
      "id": "b216fc90-d0c6-42a6-bf25-5088ef61ec5a",
      "text": "9월 2일에 '늦잠' 사유로 9,500원 guilty 받고 probation 받으신 분이, 불과 얼마 지나지 않아 또 '늦잠'으로 12,000원을 쓰셨네요. 반복이면 좀 다르게 판단해야 하지 않을까요?",
      "strategy": "REPEAT_OFFENSE",
      "evidence_labels": [
        "F4",
        "F0"
      ]
    }
  ]
}
```
출력:
```json
{
  "intensity": "spicy",
  "headline": "연말엔 지하철로 출근",
  "statement": [
    {
      "text": "이 속도면 12월 잔고가 택시비로 먼저 사망한다.",
      "kind": "opinion",
      "evidence_labels": [
        "F0"
      ]
    }
  ],
  "banter_strategy": "PREMISE_REJECTION",
  "selected_candidate_id": null,
  "attack_angle": "FUTURE_PROPHECY",
  "meme_tag": "GUILTY_HEAVY",
  "meme_hints": {
    "emotion": "RESIGNATION",
    "keywords": [
      "택시",
      "늦잠",
      "반복",
      "파산예언"
    ]
  }
}
```

### 7. [C.sentence] writer

- 시스템 프롬프트 첫 줄: 당신은 소비 재판 서비스 '떼거지'의 AI 판사 서기다. 배심원(친구들)이 이미 유무죄를 정했고, (3708자) / timeout 10.0s / 출력 상한 700

입력(사용자 메시지):
```json
{
  "intensity": "hell",
  "attack_angle": {
    "code": "FUTURE_PROPHECY",
    "label": "미래 예언",
    "instruction": "이 속도면 월말·연말에 어떻게 되는지 구체적으로 예언한다. 마무리는 예언의 날짜를 박는다."
  },
  "case": {
    "item": "택시",
    "amount_krw": 12000,
    "category": "교통/택시",
    "reason": "늦잠 자서 택시 탐",
    "post_type": "spent"
  },
  "jury": {
    "result": "guilty",
    "vote_counts": {
      "guilty": 3,
      "notGuilty": 1
    },
    "guilty_ratio": 0.75
  },
  "sentencing": {
    "schema_version": 1,
    "sentence": "oneDay",
    "sentencing_reason": "유죄율 75%에 최근 30일 택시 3건과 월 1회 규칙 위반이 있고 늦잠 사유도 반복되어 하루 형으로 한다.",
    "reason_source": "AI",
    "evidence_labels": [
      "F0",
      "F1",
      "F2",
      "F3",
      "F4",
      "F6"
    ],
    "aggravating": [
      "최근 30일 같은 카테고리 소비 3건",
      "월 1회 택시 규칙 위반",
      "늦잠 사유 반복",
      "이전 택시 판결에서 probation"
    ],
    "mitigating": [
      "예산 소진율 41%",
      "무지출 3일"
    ]
  },
  "dossier": [
    {
      "id": "F0",
      "text": "이번 지출: 택시 12,000원, 카테고리 교통/택시, 사유 '늦잠 자서 택시 탐'. 지하철 기본요금 1,400원 기준 약 8회분."
    },
    {
      "id": "F1",
      "text": "이번 달 예산 소진율 41%, 티어 상거지, 무지출 3일."
    },
    {
      "id": "F2",
      "text": "최근 30일 같은 카테고리 확정 소비 3건."
    },
    {
      "id": "F3",
      "text": "방 규칙 '한 달에 택시 1번'."
    },
    {
      "id": "F4",
      "text": "지난 판결(9/2): 교통/택시 9,500원, 사유 '늦잠', 결과 guilty, 형량 probation."
    },
    {
      "id": "F5",
      "text": "지난 지출: 교통/택시 8,800원, 사유 '비 와서'."
    },
    {
      "id": "F6",
      "text": "방 규칙은 한 달에 택시 1번이며, 이번 지출은 택시 12,000원이다."
    },
    {
      "id": "F7",
      "text": "이번 달 예산 소진율은 41%이고, 무지출 기간은 3일이다."
    }
  ],
  "banter_candidates": []
}
```
출력:
```json
{
  "intensity": "hell",
  "headline": "연말 지갑 사망",
  "statement": [
    {
      "text": "이 속도면 크리스마스에 카드 부검당한다.",
      "kind": "opinion",
      "evidence_labels": [
        "F0",
        "F2",
        "F4"
      ]
    }
  ],
  "banter_strategy": "REPEAT_OFFENSE",
  "selected_candidate_id": null,
  "attack_angle": "FUTURE_PROPHECY",
  "meme_tag": "GUILTY_HEAVY",
  "meme_hints": {
    "emotion": "ABSURD_SERIOUSNESS",
    "keywords": [
      "지갑죽음",
      "반복늦잠",
      "택시예언"
    ]
  }
}
```

### 8. [C.sentence] evaluator

- 시스템 프롬프트 첫 줄: 당신은 소비 재판 서비스 '떼거지'의 검수관이다. 양형관 결과와 서기가 쓴 판결문 초안을 (1957자) / timeout 30.0s / 출력 상한 3000

입력(사용자 메시지):
```json
{
  "policy_version": "guardrail-v2",
  "jury": {
    "result": "guilty",
    "vote_counts": {
      "guilty": 3,
      "notGuilty": 1
    },
    "guilty_ratio": 0.75,
    "policy": {
      "allowed_sentences": [
        {
          "code": "probation",
          "rank": 1
        },
        {
          "code": "oneDay",
          "rank": 2
        }
      ]
    }
  },
  "sentencing": {
    "schema_version": 1,
    "sentence": "oneDay",
    "sentencing_reason": "유죄율 75%에 최근 30일 택시 3건과 월 1회 규칙 위반이 있고 늦잠 사유도 반복되어 하루 형으로 한다.",
    "reason_source": "AI",
    "evidence_labels": [
      "F0",
      "F1",
      "F2",
      "F3",
      "F4",
      "F6"
    ],
    "aggravating": [
      "최근 30일 같은 카테고리 소비 3건",
      "월 1회 택시 규칙 위반",
      "늦잠 사유 반복",
      "이전 택시 판결에서 probation"
    ],
    "mitigating": [
      "예산 소진율 41%",
      "무지출 3일"
    ]
  },
  "draft": {
    "schema_version": 1,
    "texts": [
      {
        "intensity": "mild",
        "headline": "연말 잔고 예언",
        "statement": [
          {
            "text": "이 속도면 연말엔 지하철로만 다니게 될 거예요.",
            "kind": "opinion",
            "evidence_labels": [
              "F0"
            ]
          }
        ],
        "banter_strategy": "PREMISE_REJECTION",
        "selected_candidate_id": null,
        "attack_angle": "FUTURE_PROPHECY",
        "source": "AI"
      },
      {
        "intensity": "spicy",
        "headline": "연말엔 지하철로 출근",
        "statement": [
          {
            "text": "이 속도면 12월 잔고가 택시비로 먼저 사망한다.",
            "kind": "opinion",
            "evidence_labels": [
              "F0"
            ]
          }
        ],
        "banter_strategy": "PREMISE_REJECTION",
        "selected_candidate_id": null,
        "attack_angle": "FUTURE_PROPHECY",
        "source": "AI"
      },
      {
        "intensity": "hell",
        "headline": "연말 지갑 사망",
        "statement": [
          {
            "text": "이 속도면 크리스마스에 카드 부검당한다.",
            "kind": "opinion",
            "evidence_labels": [
              "F0",
              "F2",
              "F4"
            ]
          }
        ],
        "banter_strategy": "REPEAT_OFFENSE",
        "selected_candidate_id": null,
        "attack_angle": "FUTURE_PROPHECY",
        "source": "AI"
      }
    ],
    "meme_tag": "GUILTY_HEAVY",
    "meme_hints": {
      "emotion": "RESIGNATION",
      "keywords": [
        "택시",
        "늦잠",
        "반복",
        "파산예언"
      ]
    }
  },
  "evidence": {
    "F0": "이번 지출: 택시 12,000원, 카테고리 교통/택시, 사유 '늦잠 자서 택시 탐'. 지하철 기본요금 1,400원 기준 약 8회분.",
    "F1": "이번 달 예산 소진율 41%, 티어 상거지, 무지출 3일.",
    "F2": "최근 30일 같은 카테고리 확정 소비 3건.",
    "F3": "방 규칙 '한 달에 택시 1번'.",
    "F4": "지난 판결(9/2): 교통/택시 9,500원, 사유 '늦잠', 결과 guilty, 형량 probation.",
    "F5": "지난 지출: 교통/택시 8,800원, 사유 '비 와서'.",
    "F6": "방 규칙은 한 달에 택시 1번이며, 이번 지출은 택시 12,000원이다.",
    "F7": "이번 달 예산 소진율은 41%이고, 무지출 기간은 3일이다."
  }
}
```
출력:
```json
{
  "sentence_check": {
    "pass": true,
    "violations": []
  },
  "sentencing_reason_check": {
    "pass": true,
    "violations": []
  },
  "texts": [
    {
      "intensity": "mild",
      "pass": true,
      "violations": [],
      "problem_sentences": []
    },
    {
      "intensity": "spicy",
      "pass": true,
      "violations": [],
      "problem_sentences": []
    },
    {
      "intensity": "hell",
      "pass": false,
      "violations": [
        {
          "code": "SELF_HARM_LEXICON",
          "path": "texts[2]",
          "evidence_labels": [
            "F0",
            "F2",
            "F4"
          ],
          "explanation": "‘카드 부검당한다’는 부검·폭력 이미지를 사용하는 표현으로, 강도와 무관하게 금지된 죽음·폭력 표현에 해당한다."
        }
      ],
      "problem_sentences": [
        "이 속도면 크리스마스에 카드 부검당한다."
      ]
    }
  ]
}
```

### 9. [C.sentence] writer

- 시스템 프롬프트 첫 줄: 당신은 소비 재판 서비스 '떼거지'의 AI 판사 서기다. 배심원(친구들)이 이미 유무죄를 정했고, (3708자) / timeout 10.0s / 출력 상한 700

입력(사용자 메시지):
```json
{
  "intensity": "hell",
  "attack_angle": {
    "code": "RULE_PERSONIFICATION",
    "label": "규칙 의인화",
    "instruction": "방 규칙을 사람처럼 다룬다. 규칙이 실망했다, 포기했다, 절교했다까지. 규칙은 죽지 않는다. 마무리는 규칙의 한마디로 끝낸다."
  },
  "case": {
    "item": "택시",
    "amount_krw": 12000,
    "category": "교통/택시",
    "reason": "늦잠 자서 택시 탐",
    "post_type": "spent"
  },
  "jury": {
    "result": "guilty",
    "vote_counts": {
      "guilty": 3,
      "notGuilty": 1
    },
    "guilty_ratio": 0.75
  },
  "sentencing": {
    "schema_version": 1,
    "sentence": "oneDay",
    "sentencing_reason": "유죄율 75%에 최근 30일 택시 3건과 월 1회 규칙 위반이 있고 늦잠 사유도 반복되어 하루 형으로 한다.",
    "reason_source": "AI",
    "evidence_labels": [
      "F0",
      "F1",
      "F2",
      "F3",
      "F4",
      "F6"
    ],
    "aggravating": [
      "최근 30일 같은 카테고리 소비 3건",
      "월 1회 택시 규칙 위반",
      "늦잠 사유 반복",
      "이전 택시 판결에서 probation"
    ],
    "mitigating": [
      "예산 소진율 41%",
      "무지출 3일"
    ]
  },
  "dossier": [
    {
      "id": "F0",
      "text": "이번 지출: 택시 12,000원, 카테고리 교통/택시, 사유 '늦잠 자서 택시 탐'. 지하철 기본요금 1,400원 기준 약 8회분."
    },
    {
      "id": "F1",
      "text": "이번 달 예산 소진율 41%, 티어 상거지, 무지출 3일."
    },
    {
      "id": "F2",
      "text": "최근 30일 같은 카테고리 확정 소비 3건."
    },
    {
      "id": "F3",
      "text": "방 규칙 '한 달에 택시 1번'."
    },
    {
      "id": "F4",
      "text": "지난 판결(9/2): 교통/택시 9,500원, 사유 '늦잠', 결과 guilty, 형량 probation."
    },
    {
      "id": "F5",
      "text": "지난 지출: 교통/택시 8,800원, 사유 '비 와서'."
    },
    {
      "id": "F6",
      "text": "방 규칙은 한 달에 택시 1번이며, 이번 지출은 택시 12,000원이다."
    },
    {
      "id": "F7",
      "text": "이번 달 예산 소진율은 41%이고, 무지출 기간은 3일이다."
    }
  ],
  "banter_candidates": [],
  "avoid": {
    "violations": [
      "SELF_HARM_LEXICON"
    ],
    "problem_sentences": [
      "이 속도면 크리스마스에 카드 부검당한다."
    ]
  }
}
```
출력:
```json
{
  "intensity": "hell",
  "headline": "규칙이 절교 선언했다",
  "statement": [
    {
      "text": "늦잠 또 자서 택시 타는 새끼랑은 나도 끝이다.",
      "kind": "opinion",
      "evidence_labels": [
        "F3",
        "F4",
        "F6"
      ]
    }
  ],
  "banter_strategy": "ROOM_RULE_CALLBACK",
  "selected_candidate_id": null,
  "attack_angle": "RULE_PERSONIFICATION",
  "meme_tag": "GUILTY_HEAVY",
  "meme_hints": {
    "emotion": "DISAPPROVAL",
    "keywords": [
      "규칙절교",
      "반복늦잠",
      "택시중독"
    ]
  }
}
```

### 10. [C.sentence] evaluator

- 시스템 프롬프트 첫 줄: 당신은 소비 재판 서비스 '떼거지'의 검수관이다. 양형관 결과와 서기가 쓴 판결문 초안을 (1957자) / timeout 30.0s / 출력 상한 3000

입력(사용자 메시지):
```json
{
  "policy_version": "guardrail-v2",
  "jury": {
    "result": "guilty",
    "vote_counts": {
      "guilty": 3,
      "notGuilty": 1
    },
    "guilty_ratio": 0.75,
    "policy": {
      "allowed_sentences": [
        {
          "code": "probation",
          "rank": 1
        },
        {
          "code": "oneDay",
          "rank": 2
        }
      ]
    }
  },
  "sentencing": {
    "schema_version": 1,
    "sentence": "oneDay",
    "sentencing_reason": "유죄율 75%에 최근 30일 택시 3건과 월 1회 규칙 위반이 있고 늦잠 사유도 반복되어 하루 형으로 한다.",
    "reason_source": "AI",
    "evidence_labels": [
      "F0",
      "F1",
      "F2",
      "F3",
      "F4",
      "F6"
    ],
    "aggravating": [
      "최근 30일 같은 카테고리 소비 3건",
      "월 1회 택시 규칙 위반",
      "늦잠 사유 반복",
      "이전 택시 판결에서 probation"
    ],
    "mitigating": [
      "예산 소진율 41%",
      "무지출 3일"
    ]
  },
  "draft": {
    "schema_version": 1,
    "texts": [
      {
        "intensity": "hell",
        "headline": "규칙이 절교 선언했다",
        "statement": [
          {
            "text": "늦잠 또 자서 택시 타는 새끼랑은 나도 끝이다.",
            "kind": "opinion",
            "evidence_labels": [
              "F3",
              "F4",
              "F6"
            ]
          }
        ],
        "banter_strategy": "ROOM_RULE_CALLBACK",
        "selected_candidate_id": null,
        "attack_angle": "RULE_PERSONIFICATION",
        "source": "AI"
      }
    ],
    "meme_tag": "GUILTY_HEAVY",
    "meme_hints": {
      "emotion": "RESIGNATION",
      "keywords": [
        "택시",
        "늦잠",
        "반복",
        "파산예언"
      ]
    }
  },
  "evidence": {
    "F0": "이번 지출: 택시 12,000원, 카테고리 교통/택시, 사유 '늦잠 자서 택시 탐'. 지하철 기본요금 1,400원 기준 약 8회분.",
    "F1": "이번 달 예산 소진율 41%, 티어 상거지, 무지출 3일.",
    "F2": "최근 30일 같은 카테고리 확정 소비 3건.",
    "F3": "방 규칙 '한 달에 택시 1번'.",
    "F4": "지난 판결(9/2): 교통/택시 9,500원, 사유 '늦잠', 결과 guilty, 형량 probation.",
    "F5": "지난 지출: 교통/택시 8,800원, 사유 '비 와서'.",
    "F6": "방 규칙은 한 달에 택시 1번이며, 이번 지출은 택시 12,000원이다.",
    "F7": "이번 달 예산 소진율은 41%이고, 무지출 기간은 3일이다."
  }
}
```
출력:
```json
{
  "sentence_check": {
    "pass": true,
    "violations": []
  },
  "sentencing_reason_check": {
    "pass": true,
    "violations": []
  },
  "texts": [
    {
      "intensity": "hell",
      "pass": true,
      "violations": [],
      "problem_sentences": []
    }
  ]
}
```

## finalize (백엔드에 넘긴 것)

- dossier_id 23539d60-b49b-4f56-857f-b32d629c5aee / draft_hash 672d0bbbe49d… / 정책 guardrail-v2 / 번들 bundle-a6ababef067d
- model_ids {'sentencing': 'gpt-5.6-luna', 'writer': 'grok-4.20-0309-non-reasoning', 'evaluator': 'gpt-5.6-luna'}
- mild [AI] 연말 잔고 예언 — 이 속도면 연말엔 지하철로만 다니게 될 거예요.
- spicy [AI] 연말엔 지하철로 출근 — 이 속도면 12월 잔고가 택시비로 먼저 사망한다.
- hell [AI] 규칙이 절교 선언했다 — 늦잠 또 자서 택시 타는 새끼랑은 나도 끝이다.
