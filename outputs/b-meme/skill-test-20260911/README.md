# b-meme 실제 이미지 테스트

2026-09-11 내장 `image_gen`으로 입력 3장을 각각 1회 변환했다. 추가 수정 호출은 없었다. 모델 버전은 도구가 제공하지 않아 기록하지 않았다.

| 입력 | 생성 결과 | 확인 결과 |
|---|---|---|
| [인물·짧은 자막](inputs/images-3.jpg) | [결과](001-images-3.png) | 머리·의상·계단 구도와 `거지왕 강림` 자막 유지 |
| [인물·긴 자막](inputs/images-8.jpg) | [결과](002-images-8.png) | 얼굴 기울기·귀걸이·표정과 긴 한글 자막 유지 |
| [만화 캐릭터·소품](inputs/images-9.jpg) | [결과](003-images-9.png) | 외눈·더듬이·큰 지갑·좌우 배치와 두 줄 자막 유지 |

세 결과를 Codex가 육안 검수했다. 거친 선과 채색은 적용됐고, 인물 결과는 승인 참고보다 얼굴·배경 묘사가 조금 더 세밀하다. 이번 표본은 모두 한 컷이며 다중 컷, 생성 도구 실패와 중단 후 재개는 실제 호출로 검증하지 않았다. 생성 결과는 실행마다 달라질 수 있다.

`manifest.json`에는 실제 프롬프트, 호출 횟수와 검수 기록이 있다. 경로는 manifest가 있는 폴더 기준이다. `annotations.json`은 결과 이미지를 보고 작성한 분류이며, 이미지별 `*.metadata.json`에는 분류와 SHA-256이 저장돼 있다. 운영 등록은 승인하지 않았다(`is_active=false`).

저장소 루트에서 분류 저장을 재검증할 수 있다. Python 3.9 이상이면 실행되며 이미지 생성 API는 호출하지 않는다. 실행 시 JSON의 작성 시각이 갱신된다.

```bash
python3 skills/b-meme/scripts/save_metadata.py \
  --manifest outputs/b-meme/skill-test-20260911/manifest.json \
  --annotations outputs/b-meme/skill-test-20260911/annotations.json
```

메타데이터 테스트 12개, 스킬 구조 검증, 분류와 manifest의 일치 및 실제 이미지 SHA-256 검증을 통과했다. 테스트는 Python 3.11에서 실행했으며 `pytest-asyncio` 미설치에 따른 설정 경고 1개가 있었다. 서버 전체 테스트는 프로젝트의 Python 3.12 개발 환경이 없어 실행하지 못했다.
