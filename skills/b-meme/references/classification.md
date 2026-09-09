# 판결카드 검색용 분류

## 목적과 계약

Codex가 생성 결과를 직접 보고 분류한다. `save_metadata.py`는 분류 모델이 아니라 검증·저장 도구다. 파일명 기반 추정, 자막 키워드만의 자동 분류는 사용하지 않는다. 인물의 실제 성격·재산·신원에 관한 추론은 하지 않는다.

이 저장소의 `docs/plans/10-backend-contract.md` §11(이미지 선택)과 `01-contracts-fake-provider.md`의 감정 6종을 따른다. 허용값 정본은 번들 [taxonomy.json](taxonomy.json)이다. 다른 프로젝트에 설치해도 이 파일이 함께 전달된다. 예전 분류기의 `SELF_MOCKERY`, `MOCKERY` 등은 판결카드 감정 enum에 그대로 넣지 않는다.

| 감정 | 선택 근거 |
|---|---|
| `DISAPPROVAL` | 못마땅함, 한심하게 보는 표정 |
| `ABSURD_SERIOUSNESS` | 우스운 상황과 과도한 진지함의 대비 |
| `SMUG` | 득의양양, 능청스러운 윙크나 미소 |
| `PITY` | 측은함, 도움을 바라는 안타까운 분위기 |
| `CELEBRATION` | 기쁨, 축하, 인정 |
| `RESIGNATION` | 체념, 포기, 담담하게 받아들이는 분위기 |

여러 컷은 전체 상황과 반전까지 고려하며, 대표 감정을 먼저 적는다. 대응하는 감정이 없으면 `[]`와 `uncertainties`에 이유를 남긴다. `keywords`에는 상황·표정·소재를 한국어의 짧은 표현으로 최대 5개 적고 기존 같은 의미의 단어를 재사용한다(예: `돈없음`, `자조`, `체념`, `윙크`, `원숭이`). 사람 이름, 파일명, 무관한 배경 디테일은 검색 키워드로 쓰지 않는다.

`expense_categories`는 11개 한국어 라벨을 사용한다. 음식 구매·택시 이용 등 지출 맥락이 명확할 때만 넣는다. **돈이 없다는 자막은 지출 종류의 증거가 아니므로 `[]`**다. `기타`를 미분류 대용으로 넣지 않는다. 머플러/귀걸이를 착용했다고 쇼핑으로 분류하지 않는다.

## 작성과 저장

manifest와 같은 디렉터리에 `annotations.json`을 만든다. 키는 manifest의 출력 이미지 파일명이다. 다음은 실제 승인 이미지의 분류 예시다.

```json
{
  "001-images-8.png": {
    "emotions": ["RESIGNATION"],
    "keywords": ["돈없음", "체념", "자조"],
    "expense_categories": [],
    "subject": "human",
    "panels": [{
      "expression": "입을 다문 담담한 표정",
      "pose": "얼굴을 살짝 기울인 클로즈업",
      "captions": ["손에 구겨진 지폐 한장조차 없어요"]
    }],
    "evidence": "담담한 표정과 지폐 한 장도 없다는 자막이 체념을 나타낸다.",
    "uncertainties": []
  }
}
```

`subject`는 `human|animal|object|mixed|unknown`. `panels`는 위→아래, 왼쪽→오른쪽 순서이며 최소 1개다. 자막 없는 컷의 `captions`는 `[]`. 자막 변경/삭제 요청이 있으면 원문이 아닌 **실제 출력**의 자막과 분위기를 기준으로 분류한다. 오류나 자막 판독 불가가 남으면 `uncertainties`에 적는다. 근거는 관찰 사실과 해석의 관계를 짧게 설명한다. 숫자 신뢰도는 보정된 측정값이 없으므로 만들지 않는다.

스킬 위치에 맞춰 아래 명령을 실행한다. Python 3.9+ 표준 라이브러리만 필요하다.

```bash
python3 ~/.codex/skills/b-meme/scripts/save_metadata.py \
  --manifest /절대/결과폴더/manifest.json \
  --annotations /절대/결과폴더/annotations.json
```

저장소 안에서는 `python3 skills/b-meme/scripts/save_metadata.py`로 실행한다. 스크립트는 모든 분류를 먼저 검증하고 이미지 옆 `001-images-8.metadata.json` 및 manifest에 분류를 기록한다. 이미지 파일명과 SHA-256으로 연결하고 schema/taxonomy 버전, 분류 방식, 작성 시각도 남긴다. 자동 분류가 명확해도 운영 등록을 자동 승인하지 않는다(`tag=null`, `strategies=[]`, `is_active=false`). 실행은 기존 자동 분류 sidecar를 갱신하므로 수동 운영 승인 데이터는 별도 관리한다.

## 검증·문제 해결

- 출력 JSON의 `classification`과 실제 이미지의 표정/자막이 일치하는지 확인한다.
- 빈 감정, 불확실성, 이미지 `needs_review` 상태는 메타데이터에도 `needs_review=true`로 남는다.
- 분류값/키/출력 경로가 틀리면 스크립트가 실패한다. 이미지 재생성 없이 annotations만 수정한다.
- JSON은 파일별 원자적 교체다. 여러 파일 전체의 트랜잭션은 아니므로 디스크/권한 오류 이후에는 같은 명령을 다시 실행해 manifest까지 맞춘다. 같은 manifest의 동시 작성은 하지 않는다.
- 완료된 이미지에 카테고리가 없거나 결과가 수정되어 hash가 달라졌다면 출력 이미지를 다시 확인해 재분류한다.

## 나중에 판결카드와 연결할 때

현재 구현 범위는 분류와 로컬 저장이다. DB 등록/검색 API는 변경하지 않는다. 등록 시 담당자가 `tag`와 `strategies`를 확인한 뒤 `classification.emotions`, `classification.keywords`를 기존 `meme_images` 컬럼으로 옮기고 활성화한다. 현재 계약의 선택은 tag 필터 후 전략 +3, 감정 +2, 키워드 겹침 +1, 최근 노출 −5이며 후보가 없으면 기본 이미지다. 지출 카테고리는 후속 검색 확장에 사용할 보조 메타데이터이며 현재 선택 점수에 임의로 추가하지 않는다.
