# 거지방 밈짤 자산 파이프라인

밈짤 원본을 판결 화면에 쓸 투명 배경 PNG 자산으로 바꾼다.
`doc/ai-agent-design-plan.md`의 "판결 짤" 자산 제작 경로에 해당한다.

## 두 가지 스타일

| 스타일 | 결과 | 쓰는 곳 |
| --- | --- | --- |
| `lineart` (기본) | Canny 엣지로 원본 윤곽을 그대로 추적한 선화 | 원본 구도를 최대한 유지해야 할 때 |
| `doodle` | 실루엣을 몇 개의 굵고 삐뚤빼뚤한 선으로 다시 그린 손그림 | "대충 그린 짤" 톤, 원본과의 유사성을 낮춰야 할 때 |

두 스타일 모두 자막은 macOS Vision OCR로 읽어 깨끗한 폰트로 다시 그리므로,
그림이 아무리 거칠어져도 글자는 읽을 수 있다.

## 설치

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
```

## 사용

한 장:

```bash
./venv/bin/python cli.py -i inputs/images-3.jpg -o outputs/out.png --style doodle
```

폴더 전체 (`<이름>_doodle.png` 로 저장):

```bash
./venv/bin/python cli.py -i inputs -o outputs_doodle --style doodle
```

주요 옵션:

- `--detail N` — 내부 형태 개수. `0`이면 윤곽선만 (기본 4)
- `--wobble F` — 손떨림 정도. `0`이면 매끈한 곡선 (기본 1.0)
- `--simplify F` — 형태 단순화 정도. 클수록 더 대충 (기본 0.012)
- `--seed N` — 떨림 고정. 기본값은 입력 경로 해시라 같은 파일은 항상 같은 그림
- `--thickness N` — 선 굵기(px). doodle은 미지정 시 이미지 크기에 맞춰 자동
- `--text-mode` — `ocr` / `binarize` / `none` / `auto`
- `--no-bg-remove` — rembg 배경 제거 건너뛰기 (빠름, 대신 실루엣 품질 하락)
- `--no-categories` — 카테고리 JSON 사이드카 생략

## 구조

```text
cli.py                 CLI 진입점
run_pipeline.py        수집 → 변환 배치 실행
meme_line_extractor.py 스타일 분기 + lineart(Canny) 렌더러 + 파일/디렉터리 처리
doodle_renderer.py     doodle 렌더러 (실루엣 → 단순화 → 손떨림 획)
face_layer.py          Vision 얼굴 랜드마크 (눈·눈썹·코·입)
text_layer.py          Vision OCR + 자막 재렌더링, 형태학적 글자 채우기
background.py          rembg 배경 제거 (없으면 원본 유지로 폴백)
vision_input.py        Vision에 넘길 파일 경로 확보
meme_categorizer.py    짤별 컨셉/지출 카테고리 JSON 사이드카
fetch_memes.py         샘플 밈짤 수집
```

## 테스트

```bash
./venv/bin/python -m pytest -q
```

## 알려진 한계

- 얼굴 랜드마크와 OCR은 macOS Vision에 의존한다. 다른 플랫폼에서는 자동으로
  얼굴 없음 / `binarize` 자막 모드로 폴백하며, doodle 결과의 표정이 사라진다.
- Vision이 얼굴을 못 찾는 짤(그림체 캐릭터, 옆모습, 작은 얼굴)은 눈·입 없이
  실루엣 덩어리로만 나온다.
- 스크린샷·콜라주처럼 rembg가 배경을 분리하지 못하는 원본은 실루엣 품질이 낮다.
  화면 전체를 두르는 윤곽은 그리지 않고 버린다.
- 저작권: 선화·손그림 변환이 원저작물과의 실질적 유사성을 자동으로 해소하지
  않는다. 배포 전 검수는 설계 문서 D-09를 따른다.
