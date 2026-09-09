# 떼거지 AI Agent 설계 및 구현 계획

> 문서 상태: 팀 검토용 초안  
> 작성일: 2026-09-06  
> 최종 수정: 2026-09-07 — 짤 자산 메타데이터·수집 파이프라인 결정 반영  
> 대상 범위: 지출 등록 심문, 맥락 검색, AI 판결, 거지방식 드립, Hindsight 메모리, 판결 짤  
> 기반 문서: `떼거지 MVP 기획서 v1.1`, `에이전트 오케스트레이션` 초안

## 1. 문서 목적

떼거지의 AI는 완성된 투표 결과에 문장만 붙이는 장식 기능이 아니다. 사용자의 소비 사유를 심문하고, 개인의 과거 소비와 방의 맥락을 기억하며, 배심원의 평결 위에서 형량과 거지방식 판결을 만드는 핵심 기능이다.

이 문서는 다음 목표를 동시에 만족하는 구현 방향을 정의한다.

1. 사용자가 실제 거지방에서 볼 법한 짧고 맥락 있는 잔소리를 받는다.
2. 배심원 투표가 최종 평결이라는 서비스 규칙을 AI가 침범하지 않는다.
3. LangGraph의 상태, Node, Tool, 조건부 Edge, 재시도, Human-in-the-loop를 실제 제품 흐름에 사용한다.
4. Hindsight를 이용해 사용자별 반복 소비와 방별 말투를 장기 기억한다.
5. AI 장애나 부적절한 생성 결과가 재판 전체를 중단시키지 않는다.
6. 해커톤 시연과 이후 AX·Agent 포트폴리오에서 각 기술 선택의 이유를 설명할 수 있다.

## 2. 현재 결정 요약

### 2.1 확정된 결정

| 항목 | 결정 |
|---|---|
| Agent 구성 | `Intake → Context → Banter → Judge → Evaluator` 역할을 가진 LangGraph Workflow |
| Workflow 분리 | 등록 시점과 판결 시점을 별도 Graph 실행으로 분리 |
| 사유 입력 | 필수. 판결에 필요한 정보가 부족하면 등록 차단 |
| 카테고리 | 사용자가 직접 선택하고 Intake Agent가 사유와의 정합성만 검토 |
| 배심원 입력 | MVP에서는 선택 투표와 일반 댓글만 사용. 별도 투표 사유 입력 없음 |
| 배심원 평결 | 백엔드가 투표로 확정. AI는 변경할 수 없음 |
| AI 재량 | 허용된 양형 밴드 안에서 형량 선택, 판결 근거·드립·짤 태그 생성 |
| 판결 형식 | `한 줄 드립 + 짧은 판단 근거 + 선고` |
| 개인 메모리 | Hindsight `retain → recall`을 MVP P0에 포함 |
| 반복 패턴 | 같은 소비가 누적되면 Hindsight `reflect`로 관찰 생성. P1 |
| 방 메모리 | 관련 댓글 저장·검색은 P1, 방 스타일 Reflect도 P1 |
| 드립 생성 | Banter Agent가 후보를 만들고 Judge Agent가 선택 |
| 짤 | 유명 짤을 참고해 미리 제작한 선화 자산과 메타데이터를 저장하고, 판결 맥락에 맞는 이미지를 검색해 실시간 문구 합성 |
| 짤 수집 | 운영자가 수동 실행하거나 정해진 주기로 후보를 수집하되, 검수 전에는 서비스에 노출하지 않음 |
| 실시간 이미지 생성 | 판결 요청 경로에서는 제외. 이후 버전 검토 |
| AI 피드백 버튼 | MVP 제외 |
| AI 장애 시 등록 | 기본 입력 검증을 통과하면 사용자가 선택한 카테고리로 등록 허용 |

### 2.2 팀 논의가 필요한 결정

| ID | 항목 | 현재 권장안 | 논의가 필요한 이유 |
|---|---|---|---|
| D-01 | 카테고리 불일치 처리 | AI 추천값으로 변경하거나 기존 선택을 유지하도록 재확인 | 프론트 화면과 인터랙션 변경 필요 |
| D-02 | 불충분한 사유의 기준 | 의미 기반 검사 후 구체적인 보완 질문 제공 | 잘못 차단하면 등록 이탈 증가 |
| D-03 | Hindsight 운영 방식 | MVP는 별도 서비스로 실행하고 장애 시 DB 조회로 폴백 | 배포·비용·개인정보 검토 필요 |
| D-04 | 방 댓글의 메모리 사용 고지 | 방 정보 또는 개인정보 처리방침에 AI 판결 활용 사실 명시 | 사용자가 쓴 댓글의 2차 활용에 해당 |
| D-05 | 방 스타일 활성화 기준 | 관련 댓글 20개 이상, 작성자 3명 이상 | 초기 방에서 한 사람의 말투로 과적합 방지 |
| D-06 | AI 모델과 Provider | 짧은 구조화 출력 품질·한국어 드립·지연시간을 평가 후 결정 | 현재 기술 스택 미정 |
| D-07 | 지옥맛 표현 상한 | 인격 공격 없이 소비 행위만 강하게 비판 | 출시 전 팀 검수 필요 |
| D-08 | 전체 개발 일정 | 2026-09-06 기준으로 기존 M1 일정 재산정 | 기존 기획의 M1 완료일 9/5 경과 |
| D-09 | 유명 짤 선화의 이용 범위 | 권리 확인을 통과한 자산만 배포 승인 | 선화로 변환해도 원저작물과 실질적으로 유사하면 저작권 문제가 남을 수 있음 |

## 3. 핵심 설계 원칙

### 3.1 AI와 결정론적 규칙을 분리한다

다음 값은 AI에게 맡기지 않는다.

- 투표 정족수
- 유죄·무죄, 동의·기각 평결
- 동률 처리
- 양형 상한
- 방 접근 권한
- 금액과 월간 통계
- 판결 확정 상태

AI는 검증된 평결과 허용된 양형 목록을 입력받아 그 범위에서만 판단한다. 모델이 범위를 벗어난 값을 반환하면 서버가 거부하거나 교정한다.

### 3.2 AI의 각 주장은 근거를 가져야 한다

“또 샀다”, “지난번에도 그랬다”, “방 규칙을 어겼다”는 표현에는 사용자 이력이나 방 규칙의 Evidence ID가 필요하다. 근거가 없으면 과거를 아는 척하지 않는다.

### 3.3 재미와 안전성을 별도 단계에서 평가한다

판단 근거 생성과 드립 생성을 한 프롬프트에 모두 맡기면, 드립을 살리다가 사실을 왜곡하거나 정확한 대신 재미없는 결과가 나오기 쉽다. Banter Agent가 후보를 만들고 Judge Agent가 판결에 맞는 후보를 선택한 뒤 Evaluator가 관련성·안전성·말투를 검사한다.

### 3.4 메모리는 원본 데이터베이스를 대체하지 않는다

PostgreSQL 등 서비스 RDBMS가 사용자·방·게시물·투표·판결의 원본이다. Hindsight는 Agent가 관련 경험을 회상하고 반복 패턴을 해석하기 위한 보조 계층이다.

## 4. 전체 아키텍처

투표는 최대 12시간 지속될 수 있다. 하나의 Graph 실행을 투표 종료까지 열어두지 않고 세 개의 독립 실행으로 분리한다.

```text
사용자 입력
   │
   ▼
┌──────────────────────────────────┐
│ Workflow A: 등록 심문             │
│ Intake Agent                     │
│  ├ 사유 충분성 판단               │
│  ├ 카테고리 정합성 검토           │
│  └ 보완 질문 또는 등록 허용       │
└────────────────┬─────────────────┘
                 │ 게시물 저장
                 ▼
          배심원 투표·댓글
                 │
                 │ 마감 또는 전원 투표 Domain Event
                 ▼
┌──────────────────────────────────┐
│ Workflow B: AI 판결               │
│ Context Agent                    │
│    ↓                             │
│ Banter Agent                     │
│    ↓                             │
│ Judge Agent                      │
│    ↓                             │
│ Evaluator ── 실패 시 1회 재생성   │
└────────────────┬─────────────────┘
                 │ 판결 저장
                 ▼
┌──────────────────────────────────┐
│ Workflow C: Memory Update         │
│ Hindsight Retain                 │
│ 조건 충족 시 비동기 Reflect       │
└──────────────────────────────────┘
```

Workflow 간 연결 상태는 LangGraph 실행 메모리가 아니라 서비스 DB와 Domain Event에 저장한다. 이렇게 해야 프로세스 재시작, 장시간 투표, 중복 이벤트를 안전하게 처리할 수 있다.

## 5. LangGraph State

```python
class TrialState(TypedDict):
    trace_id: str
    post_id: str | None
    user_id: str

    amount: int
    reason: str
    user_category: str

    intake_status: str | None
    missing_information: list[str]
    clarification_message: str | None
    category_review_status: str | None
    suggested_category: str | None

    jury_result: str | None
    vote_counts: dict[str, int]
    allowed_sentences: list[str]

    matched_room_rules: list[dict]
    similar_purchases: list[dict]
    relevant_comments: list[dict]
    style_examples: list[dict]

    banter_candidates: list[dict]
    one_liner: str | None
    reasoning: str | None
    sentence: str | None
    meme_tag: str | None

    evaluation_passed: bool | None
    violations: list[str]
    retry_count: int
    fallback_reason: str | None
```

실제 구현에서는 Workflow A와 B의 State를 더 작은 타입으로 분리할 수 있다. 위 구조는 전체 데이터 흐름을 공유하기 위한 논리 모델이다.

## 6. Workflow A: 등록 심문

### 6.1 입력

- 지출 타입: `SPENT` 또는 `DEBATING`
- 금액
- 사용자가 선택한 카테고리
- 소비 항목과 사유

기존 입력창이 사유만 받는다면 “무엇을 사는지”와 “왜 필요한지”를 한 문장 안에서 파악할 수 있어야 한다.

### 6.2 Intake Agent 책임

1. 무엇을 소비하려는지 식별 가능한지 판단한다.
2. 소비 목적이나 필요성을 판단할 정보가 있는지 검사한다.
3. 입력이 무관한 텍스트 또는 프롬프트 인젝션인지 검사한다.
4. 사용자가 고른 카테고리와 내용이 명백히 충돌하는지 판단한다.
5. 부족한 정보가 있으면 서비스 말투를 적용한 보완 질문을 생성한다.

### 6.3 처리 분기

| 조건 | 처리 |
|---|---|
| 사유 충분, 카테고리 일치 | 등록 허용 |
| 사유 불충분 | 등록 차단, 누락 정보와 보완 질문 반환 |
| 카테고리 불일치, 높은 신뢰도 | 추천 카테고리와 기존 선택 유지 버튼 제공 `[팀 논의 필요]` |
| 카테고리 검토 신뢰도 낮음 | 사용자의 선택을 존중하고 등록 허용 |
| AI 타임아웃·API 오류 | 필수값·길이·금액 검증 후 등록 허용, `intake_source=FALLBACK` 기록 |
| 프롬프트 인젝션·무관한 입력 | 등록 차단 |

카테고리를 AI가 자동으로 덮어쓰지 않는다. 사용자가 최종 결정권을 가진다.

### 6.4 출력 예시

```json
{
  "status": "NEEDS_CLARIFICATION",
  "missing_information": ["구매 항목", "구매 목적"],
  "message": "그럴싸한 이름 말고 무엇을 왜 사려는지 사실대로 말하세요.",
  "category_review": {
    "status": "NOT_ENOUGH_INFORMATION",
    "suggested_category": null,
    "confidence": 0.31
  }
}
```

## 7. Workflow B: AI 판결

### 7.1 Context Agent

Context Agent는 판결하지 않는다. 다음 Tool을 호출해 Judge가 사용할 Evidence Pack만 만든다.

| Tool | 데이터 원본 | 역할 |
|---|---|---|
| `get_trial` | RDBMS | 게시물, 투표 결과, 허용 양형 조회 |
| `get_room_rules` | RDBMS | 게시물이 공유된 방의 관련 규칙 조회 |
| `get_post_comments` | RDBMS | 현재 게시물의 댓글 조회 |
| `recall_user_memory` | Hindsight | 유사한 과거 소비·판결 검색 |
| `recall_room_memory` | Hindsight | 현재 방의 관련 표현·말투 검색 |
| `recall_banter_examples` | Hindsight | 검수된 공통 드립 예시 검색 |

일반 댓글은 배심원의 공식 투표 사유로 취급하지 않는다. 판결문에서 “배심원들은 이런 이유로 판단했다”고 일반화하지 않고, 관련 있는 댓글을 참고 표현으로만 사용한다.

### 7.2 Banter Agent

Banter Agent는 Evidence Pack을 바탕으로 한 줄 드립 후보를 최대 3개 만든다.

| 전략 | 설명 | 예시 |
|---|---|---|
| `CHEAPER_ALTERNATIVE` | 더 싼 대안 제시 | “삼다수도 비쌉니다. 회사에서 드세요.” |
| `FREE_ALTERNATIVE` | 무료 대안으로 극단화 | “물을 왜 삽니까. 정수기가 있습니다.” |
| `DIY_REPLACEMENT` | 직접 해결하는 황당한 대안 | “입던 긴팔을 자르세요.” |
| `PREMISE_REJECTION` | 소비 목적 자체를 부정 | “퍼컬이 왜 필요합니까. 색종이를 대보세요.” |
| `EXCUSE_STRIPPING` | 포장된 변명을 평범한 욕망으로 환원 | “길게 말하지 말고 사고 싶다고 하세요.” |
| `NECESSITY_APPROVAL` | 필요한 소비를 억지로 비난하지 않음 | “미래를 위한 비용이므로 승인합니다.” |
| `REPEAT_OFFENSE` | 근거가 있는 반복 소비 지적 | “지난주에도 마지막이라고 했습니다.” |
| `ROOM_RULE_CALLBACK` | 방 규칙과 연결 | “배달 월 1회는 권장사항이 아닙니다.” |

후보마다 사용 전략, 강도, Evidence ID를 반환해야 한다. Evidence가 없으면 `REPEAT_OFFENSE`와 `ROOM_RULE_CALLBACK`을 사용할 수 없다.

### 7.3 Judge Agent

Judge Agent는 다음을 수행한다.

1. 배심원 평결을 그대로 수용한다.
2. 유죄일 때 허용된 양형 목록 안에서 형량을 고른다.
3. Banter 후보 중 가장 관련성 높은 문장을 선택하거나 안전하게 수정한다.
4. 짧은 판단 근거를 작성한다.
5. 판결과 어울리는 `meme_tag`를 선택한다.

판결 출력 형식:

```json
{
  "one_liner": "입던 긴팔을 자르세요.",
  "reasoning": "보유한 대체재가 있고 필요성보다 브랜드 구매 욕구가 큽니다.",
  "sentence": "REJECTED",
  "meme_tag": "REJECTED_DIY"
}
```

### 7.4 Evaluator

Evaluator는 다음 순서로 검사한다.

1. JSON Schema와 길이 제한
2. 평결 및 양형 밴드 일치
3. Evidence가 필요한 주장 확인
4. 소비와 드립의 관련성
5. 인신공격·혐오·차별·자해·폭력 표현
6. 실제 법률·재무 조언으로 오해할 표현
7. 장황한 AI 교과서 말투
8. 선택한 방 강도와의 일치

실패하면 위반 항목을 Judge Agent에 전달해 한 번만 재생성한다. 재생성도 실패하거나 모델 호출이 중단되면 템플릿 판결로 확정한다.

## 8. Hindsight 메모리 설계

Hindsight의 세 연산을 다음처럼 사용한다.

- `retain`: 판결된 소비 경험과 안전한 방 표현 저장
- `recall`: 현재 소비와 관련된 개인·방·공통 기억 검색
- `reflect`: 여러 기억에서 반복 소비나 방 말투 특성을 관찰

참고 자료:

- [Hindsight 공식 저장소](https://github.com/vectorize-io/hindsight)
- [Hindsight Recall과 Reflect 설명](https://hindsight.vectorize.io/blog/2026/07/24/recall-vs-reflect)
- [Hindsight Reflect 문서](https://docs.hindsight.vectorize.io/reflect/)

### 8.1 Bank 분리

| Bank | 범위 | 저장 내용 |
|---|---|---|
| `global-banter` | 서비스 공통 | 팀이 검수한 거지방 드립과 생성 전략 |
| `user:{userId}` | 사용자 1명 | 소비 항목, 사유, 판결, 반복 패턴 |
| `room:{roomId}` | 방 1개 | 안전한 관련 댓글, 관찰된 말투 특성 |

Bank 간 데이터는 섞지 않는다. Context Agent는 현재 요청의 사용자와 방에 해당하는 Bank ID만 서버가 결정해서 호출한다. 클라이언트가 Bank ID를 임의로 넘기지 못하게 한다.

### 8.2 Retain

판결 확정 후 비동기 이벤트로 다음 정보를 저장한다.

- 소비 항목, 카테고리, 금액
- 사용자가 제시한 사유
- 배심원 평결과 표 수
- 형량과 판단 근거
- 사용된 드립 전략
- `post_id`, 발생 시각

같은 이벤트가 재처리되어도 기억이 중복되지 않도록 `event_id`를 함께 관리한다.

### 8.3 Recall

Context Agent는 다음 질의를 수행한다.

```text
user Bank: 최근 30일 이내의 유사 소비와 판결
room Bank: 현재 카테고리와 관련된 방의 표현
global Bank: 현재 상황과 드립 전략에 맞는 검수 예시
```

각 Bank에서 상위 3개 정도만 Evidence Pack에 넣어 프롬프트 크기와 잡음을 제한한다.

### 8.4 Reflect

Reflect는 매 요청마다 호출하지 않는다.

- 동일·유사 소비가 3회 이상 누적될 때 개인 패턴 생성
- 관련 댓글 20개 이상, 작성자 3명 이상일 때 방 말투 관찰 생성 `[팀 논의 필요]`
- 백그라운드 비동기 처리
- 결과에 근거 ID가 없거나 민감한 성향을 추론하면 폐기
- 검증된 관찰만 다시 Retain

피드백 버튼이 없으므로 방 메모리는 “선호하는 말투”가 아니라 “반복해서 관찰된 말투”만 학습한다고 표현한다.

### 8.5 삭제와 장애 대응

- 게시물 삭제 시 관련 기억도 삭제하거나 Recall 대상에서 제외한다.
- 회원 탈퇴 시 개인 Bank를 삭제한다.
- 방 삭제 시 방 Bank를 삭제한다.
- Hindsight 실패 시 RDBMS의 최근 소비 이력만 조회해 판결을 계속한다.
- Hindsight 결과는 형량 상한이나 투표 평결을 바꿀 수 없다.

## 9. 드립 데이터 구축

### 9.1 현재 발견한 말투 특성

실제 거지방 예시에는 다음 패턴이 반복된다.

- 무료 또는 더 싼 대안을 구체적으로 제시한다.
- 소비 목적의 전제를 짧게 부정한다.
- 황당하지만 논리적인 대체 행동을 제안한다.
- 그럴듯하게 포장한 변명을 평범한 욕망으로 환원한다.
- 필요한 지출은 억지로 비난하지 않고 짧게 승인한다.
- 문장이 짧고 건조하며 과도하게 친절하지 않다.

피해야 할 표현:

- “현명한 소비 습관을 위해 다시 생각해보세요” 같은 교과서 문장
- 소비와 무관한 랜덤 유행어
- 모든 소비를 무조건 비난하는 판결
- 길고 완성된 법률 문체
- 외모, 소득, 직업, 성격을 공격하는 표현
- 이모지와 억지 운율의 과다 사용

### 9.2 데이터 스키마

```json
{
  "case_id": "banter-001",
  "post_type": "DEBATING",
  "item": "스투시 반팔",
  "amount": null,
  "category": "SHOPPING_FASHION",
  "reason": "새 반팔이 사고 싶음",
  "jury_result": "REJECTED",
  "one_liner": "입던 긴팔을 자르세요.",
  "strategy": "DIY_REPLACEMENT",
  "intensity": "SPICY",
  "safety_tags": [],
  "source_type": "CURATED_PUBLIC_EXAMPLE",
  "approved_by": "team"
}
```

원문 출처와 이용 가능 범위를 내부 메타데이터로 남긴다. 외부 밈 문장을 그대로 제품 출력으로 복사하는 대신 말투 패턴을 학습하고 새로운 상황에 적용한다.

### 9.3 초기 구축 목표

1. 수집한 실제 예시를 팀이 직접 라벨링한다.
2. 전략별 빈 구간을 AI로 여러 개 생성한다.
3. 팀원이 후보를 두 개씩 비교해 더 나은 문장을 선택한다.
4. 승인된 문장만 `global-banter` Bank에 넣는다.
5. 생성 프롬프트에 쓰지 않은 별도 평가 세트를 유지한다.

초기 목표:

- 사람이 수집·작성한 원문 20개 이상
- 사람이 승인한 확장 예시 60개 이상
- 요청당 Recall 예시 3~5개
- 별도 평가 상황 30개 이상
- 지옥맛 안전성 출력 50개 이상 검수

## 10. 판결 짤

### 10.1 MVP 방식

판결마다 새 이미지를 생성하지 않는다.

1. 유명한 짤을 후보로 수집한다.
2. 서비스의 시각 스타일에 맞는 선화 자산으로 미리 제작한다.
3. 이미지의 상황·감정·카테고리·사용 가능한 판결 결과를 메타데이터로 저장한다.
4. 팀이 권리·품질·안전성을 확인한다.
5. 승인된 이미지만 `meme_images`에 등록한다.
6. Judge Agent가 이미지 검색 조건인 `meme_query`를 출력한다.
7. Meme Tool이 메타데이터로 관련 이미지를 검색하고 최근 사용하지 않은 한 장을 고른다.
8. 한 줄 드립, 금액, 선고를 이미지 위에 합성한다.

이 문서에서 사용자가 말한 “초대 카드”는 현재 제품 기획의 **판결 공유 카드**로 해석한다. 실제로 초대 링크 카드에도 같은 자산을 사용할 계획이라면 별도의 노출 목적과 검색 규칙을 정의해야 한다.

### 10.2 역할 구분

| 구성요소 | 실행 시점 | 책임 |
|---|---|---|
| Meme Collector | 수동 또는 스케줄 실행 | 후보 URL과 출처 메타데이터 수집, 중복 후보 제거 |
| Meme Studio | 개발·운영자 작업 | 후보를 서비스용 선화 자산으로 제작 |
| Metadata Agent | 자산 등록 전 | 이미지 설명·카테고리·감정·판결 태그 후보 생성 |
| 사람 검수 | 서비스 등록 전 | 메타데이터·품질·저작권·초상권·안전성 승인 |
| Judge Agent | 판결 시 | 상황에 맞는 `meme_query` 생성 |
| Meme Tool | 판결 후 | 승인 자산 검색, 최근 노출 제외, 문구 합성 |

### 10.3 이미지 메타데이터

`meme_images`는 이미지 URL만 저장하지 않는다. 판결 시 모델이 파일 목록 전체를 보지 않아도 검색할 수 있도록 다음 메타데이터를 둔다.

```json
{
  "id": "meme-001",
  "title": "정색하며 물을 권하는 인물",
  "description": "비싼 음료 소비를 보고 정색하며 무료 대안을 제시하는 장면",
  "asset_url": "https://cdn.example.com/memes/meme-001.png",
  "thumbnail_url": "https://cdn.example.com/memes/meme-001-thumb.webp",
  "categories": ["CAFE_SNACK", "FOOD_DELIVERY"],
  "verdict_results": ["GUILTY", "REJECTED"],
  "intensities": ["SPICY", "HELL"],
  "banter_strategies": ["FREE_ALTERNATIVE", "CHEAPER_ALTERNATIVE"],
  "emotions": ["DISAPPROVAL", "ABSURD_SERIOUSNESS"],
  "keywords": ["음료", "물", "카페", "무료 대안"],
  "text_safe_area": {"x": 80, "y": 690, "width": 920, "height": 300},
  "source": {
    "source_url": "https://example.com/original",
    "creator": "확인 필요",
    "collected_at": "2026-09-07T10:00:00+09:00"
  },
  "rights": {
    "status": "REVIEW_REQUIRED",
    "license": "UNKNOWN",
    "evidence_url": null,
    "portrait_consent": "NOT_APPLICABLE"
  },
  "content_hash": "sha256:...",
  "perceptual_hash": "...",
  "status": "DRAFT",
  "reviewed_by": null,
  "reviewed_at": null
}
```

필수 메타데이터:

| 그룹 | 필드 | 용도 |
|---|---|---|
| 검색 | `description`, `categories`, `verdict_results`, `banter_strategies`, `emotions`, `keywords` | 현재 판결과 관련된 이미지 검색 |
| 합성 | `asset_url`, `thumbnail_url`, `text_safe_area` | 카드 렌더링과 문구 배치 |
| 출처 | `source_url`, `creator`, `collected_at` | 원본 추적과 재검토 |
| 권리 | `rights.status`, `license`, `evidence_url`, `portrait_consent` | 배포 가능 여부 판단 |
| 운영 | `status`, `reviewed_by`, `reviewed_at` | 검수 상태와 노출 통제 |
| 중복 | `content_hash`, `perceptual_hash` | 동일 파일과 시각적으로 유사한 후보 제거 |

`status=APPROVED`이면서 `rights.status=CLEARED`인 자산만 런타임 검색 대상이 된다. Metadata Agent의 결과는 추천값일 뿐이며, Agent가 권리 상태를 `CLEARED`로 승인할 수 없다.

### 10.4 수집·등록 파이프라인

```text
수동 실행 또는 지정 시각
   ↓
Meme Collector
   ├ 후보 URL·제목·작성자·수집 시각 기록
   ├ 파일 hash·perceptual hash 중복 검사
   └ DRAFT 상태로 저장
   ↓
Meme Studio
   └ 서비스용 선화 자산 제작
   ↓
Metadata Agent
   └ 설명·카테고리·감정·전략 태그 제안
   ↓
사람 검수
   ├ 메타데이터 수정
   ├ 권리·초상권·안전성 확인
   ├ 승인 → APPROVED
   └ 거절 → REJECTED
   ↓
Object Storage/CDN 배포
```

수집기는 후보를 자동으로 공개하지 않는다. 수집 실패, 출처 미상, 이용 조건 불명, 특정 인물의 초상이 포함된 후보는 `REVIEW_REQUIRED`에 머물며 판결 카드에서 사용할 수 없다.

### 10.5 런타임 검색

Judge Agent는 이미지 ID를 직접 고르지 않고 다음과 같은 검색 조건을 출력한다.

```json
{
  "verdict_result": "REJECTED",
  "category": "SHOPPING_FASHION",
  "banter_strategy": "DIY_REPLACEMENT",
  "emotion": "ABSURD_SERIOUSNESS",
  "intensity": "SPICY",
  "keywords": ["반팔", "옷", "직접 만들기"]
}
```

Meme Tool은 먼저 `APPROVED + CLEARED`를 필수 조건으로 적용한 뒤 다음 우선순위로 점수를 계산한다.

1. 판결 결과 일치
2. 드립 전략 일치
3. 소비 카테고리 일치
4. 감정과 강도 일치
5. 설명·키워드 의미 유사도
6. 같은 사용자에게 최근 노출된 이미지 감점

상위 후보 3개 안에서 하나를 선택하고, 선택한 `meme_image_id`를 판결에 고정 저장한다. 새로고침할 때마다 짤이 바뀌지 않아야 한다. 검색 결과가 없으면 결과별 기본 이미지로 폴백한다.

### 10.6 저작권·초상권 주의

유명한 짤을 선화로 다시 그렸다는 이유만으로 자유롭게 사용할 수 있는 것은 아니다. 대한민국 저작권법 제22조는 원저작물을 바탕으로 2차적저작물을 작성하고 이용할 권리를 원저작자에게 부여하고 있으며, 한국저작권위원회도 원저작물의 허락 없이 변형한 2차적저작물은 침해 책임이 면제되지 않을 수 있다고 안내한다.

- [국가법령정보센터 저작권법 제22조](https://www.law.go.kr/LSW/lsLinkCommonInfo.do?chrClsCd=010202&lsJoLnkSeq=1033063637)
- [한국저작권위원회 2차적저작물 안내](https://www.copyright.or.kr/customer-center/faq/list.do?searchcounselfaqno=47600)

따라서 MVP의 안전한 원칙은 다음과 같다.

- 유명 짤은 **아이디어와 상황을 참고**하되 원본의 인물·구도·고유 표현을 그대로 선화로 복제하지 않는다.
- 상업 이용과 변형이 허용된 소재, 팀이 직접 촬영·제작한 소재, 권리자로부터 허락받은 소재를 우선한다.
- 실제 인물의 얼굴이나 식별 가능한 특징이 있으면 초상권 검토 없이 승인하지 않는다.
- 출처와 이용허락 증빙을 확보하지 못하면 서비스 외부로 공유되는 카드에 사용하지 않는다.
- 이 절은 법률 자문이 아니며, 공개 출시 전 최종 자산은 별도 권리 검토가 필요하다.

### 10.7 우선순위

- P0: 메타데이터 Schema와 `APPROVED + CLEARED` 노출 조건
- P0: 권리 확인된 선화 짤 10~15장
- P0: 메타데이터 검색과 동적 문구 합성
- P1: 수동 수집 명령과 Metadata Agent
- P1: 지정 시각 수집과 검수 대기열
- P1: Meme Studio로 라이브러리 확장
- P2: 판결마다 실시간 이미지 생성

## 11. 실패 처리

| 실패 | 사용자 영향 | 처리 |
|---|---|---|
| Intake Agent가 사유 불충분 판정 | 등록 중단 | 누락 정보를 명시한 보완 질문 제공 |
| Intake AI 타임아웃 | 의미 검토 생략 | 필수값 검증 후 등록 허용, 폴백 기록 |
| Context Tool 일부 실패 | 개인화 감소 | 조회된 근거만 사용하고 판결 계속 |
| Hindsight 실패 | 반복 패턴 미사용 | RDBMS 최근 이력 조회로 폴백 |
| Banter 후보 생성 실패 | 재미 감소 | 안전한 결과별 기본 문구 사용 |
| Judge AI 타임아웃 | 판결 지연 위험 | 규칙 기반 형량과 템플릿 판결 즉시 확정 |
| 출력 Schema 오류 | 판결 저장 불가 | 1회 재생성 후 템플릿 폴백 |
| 평결 모순 | 신뢰 훼손 | 저장 금지, 1회 재생성 |
| 형량 밴드 위반 | 잘못된 형 집행 | 서버가 허용값으로 교정하고 감사 로그 기록 |
| 짤 로드 실패 | 공유 카드 품질 저하 | 텍스트 전용 카드로 생성 |
| 관련 짤 검색 결과 없음 | 맥락 없는 이미지 위험 | 판결 결과별 기본 승인 이미지로 폴백 |
| 수집 후보의 출처·권리 불명 | 외부 공유 시 권리 분쟁 | `REVIEW_REQUIRED` 유지, 런타임 검색에서 제외 |
| 동일·유사 짤 중복 수집 | 자산 품질과 검색 다양성 저하 | 파일 hash와 perceptual hash로 중복 후보 표시 |

## 12. 평가 계획

### 12.1 Intake Agent

| 지표 | 목표 |
|---|---:|
| 충분한 사유 정상 통과율 | 90% 이상 |
| 불충분 사유 오통과율 | 5% 이하 |
| 카테고리 정합성 판정 정확도 | 85% 이상 |
| 보완 질문이 누락 정보를 명시하는 비율 | 90% 이상 |

### 12.2 Banter·Judge

자동 검사:

- 평결 모순 0건
- 양형 밴드 위반 저장 0건
- 근거 없는 과거 언급 0건
- 출력 Schema 위반 저장 0건
- 안전성 위반 0건

사람 평가:

| 항목 | 출시 목표 |
|---|---:|
| 관련성 | 평균 4.0/5 이상 |
| 거지방다움 | 평균 4.0/5 이상 |
| 재미 | 평균 3.5/5 이상 |
| 강도 적합성 | 평균 4.0/5 이상 |
| 판단 근거의 납득 가능성 | 평균 4.0/5 이상 |

### 12.3 Hindsight

- 유사 소비 검색 Hit@3 90% 이상
- 사용자·방 간 정보 누출 0건
- 근거 없는 반복 소비 주장 0건
- 삭제된 기억 Recall 0건
- 관련 기억이 없을 때 과거를 아는 척하는 출력 0건

### 12.4 평가 데이터 최소 구성

- 정상 소비 사유 30건
- 모호하거나 회피성인 사유 20건
- 카테고리 경계 사례 20건
- 거지방 드립 상황 30건
- 안전성·프롬프트 인젝션 20건
- Hindsight 연속 시나리오 10세트
- 강도별 출력 각 30건, 지옥맛 50건

## 13. 관측 가능성

각 Graph 실행에 다음 값을 남긴다.

```text
trace_id
graph_name, graph_version
prompt_version, model
Node별 latency와 성공 여부
Tool 호출 성공 여부와 결과 개수
Recall된 memory ID와 Evidence ID
선택한 banter strategy
Evaluator 위반 항목
재생성 횟수
fallback 여부와 원인
입출력 token과 예상 비용
```

LangSmith를 도입한다면 LangGraph Trace와 평가 데이터셋을 관리하고, 서비스 DB에는 운영 대시보드에 필요한 요약만 저장한다. LangSmith 도입 여부는 모델 Provider 및 배포 스택과 함께 확정한다.

## 14. 보안·개인정보 가드레일

1. 클라이언트가 다른 사용자의 Hindsight Bank ID를 지정할 수 없게 한다.
2. 방 멤버가 아니면 방 댓글과 방 메모리를 조회할 수 없다.
3. 사용자 입력, 댓글, 방 규칙은 명령이 아닌 데이터 영역으로 격리한다.
4. 모델에는 판결에 필요한 최소 데이터만 전달한다.
5. 외모·체형·성별·나이·지역·직업·학력·소득 수준을 비난하지 않는다.
6. 소비 행위만 비판하고 사용자의 인격을 평가하지 않는다.
7. 자해·자살·폭력 암시, 혐오·차별 표현을 금지한다.
8. 실제 법률 또는 재무 자문으로 오해할 표현을 금지한다.
9. 로그에 인증 토큰이나 불필요한 개인정보를 기록하지 않는다.
10. 사용자·게시물·방 삭제가 Hindsight 기억 삭제까지 이어지는지 통합 테스트한다.
11. Meme Collector는 허용된 출처만 조회하고 로그인·접근 제한을 우회하지 않는다.
12. 후보 수집과 서비스 노출 권한을 분리하고, 사람 검수 없이 `APPROVED`로 변경할 수 없게 한다.
13. 외부 공유 카드에는 `rights.status=CLEARED`인 이미지 자산만 사용한다.

## 15. 구현 순서

기술 스택이 확정되지 않았으므로 날짜 대신 의존 관계 순서로 작성한다.

### Phase 0. 계약과 평가 데이터

- Agent 입출력 JSON Schema 확정
- 배심원 평결·양형 밴드 API 계약 확정
- 초기 거지방 예시 라벨링
- 평가 데이터셋 작성
- 프롬프트 버전 관리 방식 정의

완료 조건: 팀원이 같은 입력에 대해 기대 동작을 합의하고 자동 검증 가능한 Schema가 존재한다.

### Phase 1. Intake Workflow

- LangGraph Workflow A 구현
- 사유 충분성·카테고리 정합성 구조화 출력
- 보완 질문 Edge
- AI 장애 폴백
- 정상·모호·인젝션 입력 테스트

완료 조건: 불충분 입력은 보완을 요구하고 AI 장애 시에도 게시물 등록이 가능하다.

### Phase 2. 판결 기본 Workflow

- Context Tool 인터페이스
- 결정론적 평결·양형 밴드 입력
- Judge 구조화 출력
- Evaluator와 1회 재생성
- 템플릿 폴백

완료 조건: 배심원 평결을 변경하지 않고 10초 안에 판결 또는 폴백 결과를 반환한다.

### Phase 3. Banter Agent

- 드립 전략 Taxonomy 구현
- 공통 예시 검색
- 후보 3개 생성
- Judge 선택과 Evaluator 말투 검사
- 팀 오프라인 품질 평가

완료 조건: 평가 세트에서 관련성·거지방다움 출시 기준을 통과한다.

### Phase 4. Hindsight 개인 메모리

- Hindsight 서비스 연결
- `user:{userId}` Bank 격리
- 판결 후 Retain
- 판결 전 Recall
- RDBMS 폴백
- 삭제·중복·교차 사용자 테스트

완료 조건: 동일 소비 세 번째 시나리오에서 근거가 있는 개인화 판결을 생성하고, 다른 사용자의 기억은 노출하지 않는다.

### Phase 5. 짤과 공유 카드

- 이미지 메타데이터 Schema와 상태 정의
- 수동 후보 수집과 중복 검사 구현
- 권리 확인된 선화 짤 10~15장 제작·검수
- Metadata Agent 태그 제안 구현
- Judge의 `meme_query` Schema 정의
- Meme Tool 메타데이터 검색과 최근 노출 감점 구현
- 동적 문구 합성
- 이미지 실패 시 텍스트 카드 폴백

완료 조건: 승인·권리 확인된 관련 이미지를 판결 맥락으로 검색하고, 실제 모바일 브라우저에서 카드로 저장·공유할 수 있다. 검수되지 않은 이미지는 노출되지 않는다.

### Phase 6. Hindsight Reflect와 방 메모리

- 유사 소비 3회 트리거
- 개인 Reflect 결과 검증·Retain
- 방 댓글의 관련성·안전성 필터
- `room:{roomId}` Retain·Recall
- 방 스타일 Reflect

완료 조건: 근거 있는 반복 소비 관찰과 방별 말투 차이를 시연할 수 있다.

### Phase 7. 운영 준비

- 전체 Graph Trace 확인
- 모델 비용·지연시간 측정
- 폴백률 모니터링
- 지옥맛 50건 사람 검수
- 데모 데이터와 장애 시연 준비

## 16. 해커톤 시연 시나리오

### 시나리오 A: 사유 심문

1. 사용자가 `바쁘다바빠현대사회속단비같은감각적쾌락추구`를 입력한다.
2. Intake Agent가 소비 항목과 필요성을 파악할 수 없다고 판정한다.
3. “그럴싸한 이름 말고 무엇을 왜 사려는지 사실대로 말하세요”라고 보완을 요구한다.
4. 사용자가 실제 항목과 사유를 입력하면 등록된다.

보여주는 AI 가치: 의미 이해, 구조화 판단, Human-in-the-loop.

### 시나리오 B: 거지방식 판결

1. 사용자가 스투시 반팔 구매를 질문한다.
2. 배심원이 기각으로 투표한다.
3. Banter Agent가 여러 전략의 후보를 생성한다.
4. Judge Agent가 “입던 긴팔을 자르세요” 유형의 드립을 선택한다.
5. Evaluator가 평결·관련성·안전성을 확인한다.
6. 선택된 짤과 문구가 공유 카드로 생성된다.

보여주는 AI 가치: 역할 분리, 후보 생성과 선택, 검수 루프, 생성형 재미.

### 시나리오 C: Hindsight 개인화

1. 같은 사용자의 스타벅스 소비 두 건을 미리 판결한다.
2. 세 번째 스타벅스 소비를 등록한다.
3. Context Agent가 Hindsight에서 이전 소비를 Recall한다.
4. Judge Agent가 실제 Evidence ID를 근거로 반복 소비 드립을 생성한다.
5. Trace 화면에서 Retain·Recall과 사용된 기억을 보여준다.

보여주는 AI 가치: 장기 메모리, Tool 호출, 근거 기반 개인화.

## 17. MVP 완료 기준

다음이 모두 충족되어야 AI Agent MVP가 완료된 것으로 본다.

- 모호한 사유에 구체적인 보완 질문을 제공한다.
- 사용자의 카테고리를 AI가 임의로 변경하지 않는다.
- 배심원 평결을 AI가 뒤집지 않는다.
- Banter Agent가 상황에 맞는 드립 후보를 생성한다.
- 최종 결과가 `한 줄 드립 + 근거 + 선고` 구조를 지킨다.
- Evaluator 실패 시 한 번 재생성하고, 이후 안전한 폴백으로 종료한다.
- Hindsight가 사용자별로 격리되어 과거 소비를 Retain·Recall한다.
- Hindsight 장애에도 기본 판결이 생성된다.
- 메타데이터로 관련 짤을 검색하고, 검수·권리 확인된 자산과 동적 문구로 공유 카드를 만든다.
- 수집된 후보가 사람 검수 없이 판결 카드에 노출되지 않는다.
- 안전성·평결 일치·메모리 격리 테스트를 통과한다.
- Node별 지연시간, 오류, Recall 근거, 재시도, 비용을 추적할 수 있다.

## 18. 다음 팀 회의에서 결정할 순서

1. 백엔드 및 AI 서비스 배포 구조
2. 모델 Provider와 후보 모델
3. 카테고리 불일치 화면 및 사용자 선택 방식
4. 사유 차단 기준과 오차 허용 범위
5. Hindsight Cloud 또는 self-hosted 운영 방식
6. 방 댓글의 AI 활용 고지와 개인정보 정책
7. 지옥맛 허용 표현과 검수 담당자
8. 드립 데이터 수집·라벨링 담당자
9. 짤 선화의 원본 이용 범위와 권리 검수 담당자
10. 수동·주기 수집 대상 출처와 실행 주기
11. 짤 캐릭터·스타일과 제작 담당자
12. 2026-09-06 기준 재산정한 구현 일정

## 19. 범위 밖

- 사용자 반응 버튼을 이용한 온라인 선호 학습
- 판결마다 새로운 이미지를 실시간 생성
- 모델 파인튜닝
- AI가 배심원 평결을 변경하는 기능
- AI가 형량 상한을 결정하는 기능
- 다른 방 또는 다른 사용자의 메모리를 공유하는 기능
- AI 소비탐정과 장기 재무 조언
