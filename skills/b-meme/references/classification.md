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

## 작성과 저장 (기존 v1)

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

## 하이브리드 검색용 v2 (신규 산출물 권장)

새 분류에는 기존 필드를 그대로 두고 다음 네 필드를 추가한다. 감정과 지출 카테고리는 보조 분류이며 자유 태그의 허용값을 제한하지 않는다.

```json
{
  "001-images-8.png": {
    "schema_version": 2,
    "description": "담담한 표정의 인물이 얼굴을 기울이고, 돈이 없다는 자막이 아래에 표시된다.",
    "usage_context": "돈을 다 쓴 뒤 체념하거나 자기 상황을 담담하게 자조할 때",
    "tags": ["돈없음", "체념", "자조", "빈 지갑"],
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

- `description`: 실제 보이는 장면. `usage_context`: 쓰일 상황·반응에 대한 해석. 각각 앞뒤 공백 제거·Unicode NFC 정규화 후 1~1,000자다. 관찰과 해석을 섞지 않는다.
- `tags`: 자유 문자열 배열. 항목 앞뒤 공백 제거·NFC·중복 제거 후 최대 20개, 항목당 1~40자. 내부 공백은 보존한다. 태그가 없으면 `[]`. 기존 `keywords`의 최대 5개 제약은 유지한다.
- 자막은 실제 출력의 컷 순서와 반복을 그대로 적는다. 사용 맥락에 전체 컷의 반전을 설명한다. 프롬프트·제외 조건·모델을 향한 명령을 검색 필드에 넣지 않는다.
- 지원하는 명시적 annotations 버전은 정수 `2`다. 버전 없는 기존 형식은 v1으로 처리한다. sidecar 전체를 annotations로 넘기지 않는다.

같은 저장 명령을 사용한다:

```bash
uv run python skills/b-meme/scripts/save_metadata.py \
  --manifest /절대/결과폴더/manifest.json \
  --annotations /절대/결과폴더/annotations-v2.json
```

v2는 이미지 옆 `*.metadata.v2.json`에 저장하고 manifest에 `metadata_v2_path`만 추가한다. 기존 v1 `*.metadata.json`, `classification`, `metadata_path`와 생성 기록은 보존한다. v2 소비자는 `metadata_v2_path`의 파일을 읽는다. 신규 v2만 저장한 항목에는 v1 classification을 따로 생성하지 않는다.

v2 파일에는 정규화된 description/usage_context/tags와 기존 classification이 들어간다. `search_text`는 description → usage_context → 컷별 captions → tags 순서의 라벨 있는 텍스트이고 `search_text_hash`는 그 UTF-8 SHA-256이다. `search_text_version=1`은 텍스트 조합 형식, `metadata_version`은 이미지·메타데이터 변경 번호다. 동일 입력 재실행은 번호를 유지하고 변경 시 증가한다. 검색 내용이 바뀌면 새 해시가 생기므로 후속 등록기는 이전 임베딩 재사용을 중단해야 한다.

`review_status=pending`, `publication_approved=false`, `allowed_verdict_tags=[]`, `is_active=false`로만 저장한다. `needs_review=false`도 사람의 운영 승인이나 공개 허가가 아니다. 서버 발급 식별자·접근 주체·임베딩 모델은 여기서 만들지 않는다. 실제 MIME/크기/유효 이미지 검증은 후속 등록 단계에서 수행한다.

### v1을 보존하며 변환하기

1. 기존 sidecar의 `classification`을 복사해 **새** `annotations-v2.json`의 이미지 파일명 아래에 넣는다. 원본 annotations와 sidecar를 덮어쓰지 않는다.
2. 실제 이미지를 다시 보고 schema_version·description·usage_context·tags를 보완한다. `evidence`를 description으로 자동 복사하지 않는다. 기존 uncertainties를 보존한다.
3. 위 명령을 실행한다. 기존 v1 SHA-256과 현재 이미지가 다르면 변환을 거부한다. 원본 이미지를 복원하거나 검수한 새 이미지를 고유 파일명과 새 manifest에 등록한다. 검증을 피하려고 해시만 고치지 않는다.
4. `metadata_v2_path`, 검색 문서/해시, v1 원본 보존을 확인한다. 기존 v1 검수 필요 상태도 v2에서 유지된다.

모든 항목을 검증한 뒤 파일별로 원자 저장한다. 여러 파일 전체는 트랜잭션이 아니므로 I/O 중단 후 같은 명령으로 다시 실행한다. sidecar 저장 뒤 manifest 저장이 실패해도 같은 입력 재실행으로 버전을 재증가시키지 않고 복구한다. 기존 v2의 형식/검색 해시/비활성 상태가 손상되면 자동 덮어쓰지 않는다. 이전 정상 백업을 확인해 복원하고 실행한다. 같은 manifest에 여러 프로세스가 동시에 쓰는 것은 지원하지 않는다.
