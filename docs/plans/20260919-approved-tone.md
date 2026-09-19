# 승인 문체 최소 개선 — 2026-09-19

## 목적과 범위

배심원의 `agree`를 유지하면서 사유에 맞게 인정하거나 가볍게 되묻는다.
시작 HEAD는 `ac4e1c6835c831e40bbd3d79922292463d544ec3`이다.
현재 서기는 모든 평결에 roast 지침과 공격 각도를 전달한다. 필요성 있는 승인에도
잔고 파산 예언을 유도할 수 있다.

승인 전용 시스템 프롬프트를 요청 조립에서 선택한다. 비승인 v5.7, 양형·검수·모델·
재시도 흐름은 유지한다. 공통 프롬프트 수정은 비승인 회귀 범위가 크고, 새 문체 enum은
계약·백엔드 변경이 필요하므로 이번에는 도입하지 않는다.

## 판단 기준

| 입력 근거 | 문체 | 기존 메타데이터 |
|---|---|---|
| 목적·필요성·상황이 구체적이고 이를 뒤집는 근거가 없음 | 칭찬·인정 | `NECESSITY_APPROVAL`, `CELEBRATION`, 인정·수긍 |
| 사유 자체가 필요성·실행 계획을 유보하거나 구체적 모순이 제공됨 | 승인하면서 현재 계획만 가볍게 되묻기 | `NECESSITY_APPROVAL`, `SMUG`, 진행시켜·능청 |
| 사유가 짧거나 정보가 부족함 | 담백한 승인, 의지 부족 단정 금지 | `NECESSITY_APPROVAL`, `CELEBRATION`, 수긍 |

품목 이름으로 분류하지 않는다. 책·강의도 구체적인 목적이 있으면 인정한다.
정보 부재는 게으름의 증거가 아니다. 과거 미사용·미수강은 실제 조서 근거나 명시적 사용자
진술이 있을 때만 인용한다. 질문·의견 형식으로 과거 사실을 암시하는 것도 금지한다.
강도는 기존 어미 기준(mild 존댓말, spicy 반말/존댓말·욕 없음, hell 반말)을 유지한다.
법정체/단톡체의 전체 방향은 이번에 새로 확정하지 않는다.

## 구현·검증 순서

- [x] `tests/unit/test_approved_writer.py`: 승인 요청·repair에서 공격 지시가 없고
  비승인은 기존 요청과 같은지 먼저 실패시킨다.
- [x] `prompts/writer/approved-v1.md`: 승인 판단, 근거·안전, 길이·메타데이터 규칙을 작성한다.
- [x] `src/geoji_ai/graphs/sentencing.py`: `agree`에만 새 프롬프트를 선택하고
  공격 각도의 지시·라벨은 보내지 않는다. 기존 각도 code는 계약 호환용으로 유지한다.
- [x] fake 그래프에서 `APPROVED`, 감정·키워드·전략, F0 라벨이 finalize까지 유지되는지 확인한다.
- [x] 단위·계약 테스트, lint, fake 골든셋을 실행하고 결과와 미검증 범위를 기록한다.

API·DB 스키마, enum, 의존성 추가 없음. 추가 모델 호출 없음. 분류는 기존 서기 1호출
안에서 수행하므로 의미적 정확성은 별도 실측·사람 검수가 필요하다. 실패 시 기존 repair와
템플릿 폴백을 그대로 사용한다. 커밋·푸시·등록·배포는 수행하지 않는다.

## 사람이 검수할 합성 사례

아래 문구는 목표를 설명하는 수동 예시이며 실제 모델 출력이 아니다.

| 사례와 실제 제공된 사유 | 기대 | 본문 예시 |
|---|---|---|
| 건강검진: 의사가 권한 정기 검사, 예약일 확정 | 인정 | 필요한 검사 챙기는 건 잘했네. |
| 자격증 시험: 지원 요건이며 응시 일정 확정 | 인정 | 필요한 자격이면 응시료 인정. |
| 책: 이번 업무에 필요한 내용을 바로 참고 | 인정 | 바로 쓸 책이면 살 이유 있네. |
| 강의: 언젠가 쓸 것 같지만 수강 시점은 미정 | 가벼운 질문 | 사는 건 승인, 시작은 언제? |
| 강의: 사유·이력 없음 | 담백한 승인 | 좋아, 이번 구매는 승인. |
| 강의: 본인이 이전 강의 미수강을 명시 | 그 진술 범위에서 질문 | 구매는 승인, 이번엔 들을 거지? |
| 고장 난 업무용 키보드 교체 | 인정, 파산 예언 금지 | 일할 도구 바꾸는 건 인정. |

본문 30자·제목 20자 이내, 승인 유지, 사유 적합성, 허구의 과거 없음,
사실을 인용한 문장의 F0/Fn 라벨 보존을 각각 검수한다.

## 밈 카탈로그 확인

새 worktree의 추적 카탈로그는 10장, `APPROVED` 항목은 없다.
원래 체크아웃(`/Users/hyun/dev/geoji`)의 로컬 카탈로그는 14장이며 아래 3장이
`APPROVED`다. 사용자 변경은 복사·수정하지 않았다. PNG와 metadata를 직접 확인했다.

| 파일 (`outputs/b-meme/run-20260916-new-inputs/`) | 감정·키워드 | 판단 |
|---|---|---|
| `002-Mnet-necklace.png` | CELEBRATION / 합격·인정 | 칭찬 후보. 시험 합격 사실로 오해할 수 있는 맥락은 검수 |
| `014-images-jpeg.png` | CELEBRATION / 인정·수긍 | 인정 후보. metadata에 축하보다 수긍에 가깝다는 불확실성 있음 |
| `012-images-8.png` | SMUG / 진행시켜·능청 | 가벼운 능청 승인 후보. 못마땅함·의심을 정확히 표현하는 이미지는 아님 |

3장 모두 카탈로그상 `is_active=false`, `image_url=null`, `strategies=[]`다.
운영 S3·DB 등록 상태는 확인하지 않았다. 현재 승인 카탈로그에 `DISAPPROVAL` 후보는 없다.
기존 백엔드 `MemeScorer`는 활성·동일 tag만 남긴 후 전략 +3, 감정 +2,
키워드 교집합 +1/개, 최근 사용 -5로 선택한다. 감정은 강제 필터가 아니므로
힌트가 맞아도 원하는 이미지 선택을 보장하지 않는다. 이미지 등록·메타데이터 정비·
선택 정책 변경은 후속 단위다.

## 저장과 공유 카드의 구분

로컬 최신 백엔드 `ShareCardAssembler`는 인용 근거가 공개 불가이면 문장을 템플릿으로
바꾸고 제목도 템플릿으로 바꾼다. F0가 ROOMS인 승인 문구는 저장에 성공해도 공유 카드에
그대로 나오지 않을 수 있다. 이번에는 필터·라벨을 변경하지 않는다. fake finalize는
DB 저장·공유 PNG 검증이 아니다.

## 검증 결과

승인 요청 테스트 9건이 기존 코드에서 공격 지시 때문에 실패하는 것을 확인한 뒤 수정했다.
승인 전용 테스트는 20건이며, 전체 비용 없는 테스트 1,358건이 통과했다.
lint 통과. 의존 라이브러리 Starlette/anyio의 기존 deprecation warning 1건이 있다.

fake 골든셋 50사건은 변경 전 HEAD와 변경 후 모두 FAIL이다. 자동 검사 53건
(expect 51, 제목 중복 1, 각도 다양성 1)과 fake judge 고정 점수 3.00이 동일하다.
스키마·근거·길이·평결 모순·감정 검사 위반은 양쪽 모두 0건이다.
기존 HEAD를 git archive로 별도 임시 경로에 풀어 비교했고 자동 검사부터 위반 목록까지
리포트가 동일함을 확인했다. fake는 프롬프트의 의미를 따라 생성하지 않으므로
문구 개선 효과나 실제 모델 회귀 통과의 근거로 쓰지 않는다.

이번 세션은 uv 실행 파일이 없어 기존 Python 3.12 가상환경의 의존성을 읽기 전용으로
사용했다. `PYTHONPATH=src`로 실행 소스와 프롬프트는 새 worktree를 사용했다.

```bash
# 실행 위치: 새 worktree 루트
PYTHONPATH=src /Users/hyun/dev/geoji/.venv/bin/python -m pytest --ignore=tests/integration -q
/Users/hyun/dev/geoji/.venv/bin/ruff check .
/Users/hyun/dev/geoji/.venv/bin/ruff format --check src/geoji_ai/graphs/sentencing.py tests/unit/test_approved_writer.py
GEOJI_EVAL=1 PYTHONPATH=src /Users/hyun/dev/geoji/.venv/bin/python -m tests.evaluations.run_regression --dry-run --out /tmp/geoji-approved-tone-regression.md
```

일반 개발 환경에서는 `uv sync` 후 `uv run pytest --ignore=tests/integration -q`,
`uv run ruff check .`로 재실행한다. DB 통합 테스트는 이번에 전용 DB를 띄우지 않아
제외했다. 필요하면 전용 폐기 가능한 DB를 준비하고
`TEST_DATABASE_URL=<전용 DB URL> uv run pytest tests/integration -q`로 실행한다.

유료 호출 0회·추가 비용 $0. 이전 예산 파일과 누적 사용량은 변경하지 않았다.
실제 모델 골든셋·사람 검수, 운영 이미지 매칭, 실제 공유 카드 반영은 미검증으로 남긴다.
실제 모델 검증은 위 합성 사례를 대상으로 기존 누적 예산 안에서 다음 단위로 수행한다.
심문관 폴백, 택시 환산 오류, 기각 검수 schema 문제는 별도 후속 범위다.
