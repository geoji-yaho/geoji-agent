# 설치와 사용

Codex의 내장 이미지 생성 도구가 있는 환경에서 사용한다. 별도 API 키는 필요 없다. Python 검증 스크립트는 Python 3.9+ 표준 라이브러리만 사용한다.

저장소 루트에서 개인 스킬 폴더로 설치한다. 기존 설치를 직접 수정했다면 먼저 백업한다.

```bash
mkdir -p ~/.codex/skills/b-meme
cp -R skills/b-meme/. ~/.codex/skills/b-meme/
```

새 Codex 작업에서 이미지를 첨부하고 요청한다.

```text
$b-meme 이 이미지들을 각각 B급 손그림 밈으로 변환해줘.
원본 자막과 표정을 유지하고 카테고리도 저장해줘.
```

폴더도 지정할 수 있다. 결과는 기본적으로 `outputs/b-meme/` 아래 고유 실행 폴더에 저장된다.

```text
$b-meme /Users/hyun/dev/geoji/inputs 폴더의 이미지들을 각각 변환해줘.
```

이미 생성된 이미지에도 재생성 없이 분류를 붙일 수 있다.

```text
$b-meme 이 manifest의 이미지에 카테고리만 추가해줘.
```

manifest의 `metadata_v2_path`가 가리키는 `*.metadata.v2.json`에서 장면 설명, 사용 맥락, 자유 태그와 컷별 표정·자막을 실제 그림과 비교해 확인한다. 기존 v1 `*.metadata.json`은 보존한다. 실패·재개와 판결카드 연동은 [분류 규칙](classification.md)을 참고한다. 아직 DB 등록/검색 API는 포함하지 않는다.

이미지 생성/API 호출 없는 저장 테스트:

```bash
.venv/bin/python -m pytest tests/test_b_meme_metadata.py -q
```

스킬이 안 보이면 새 작업에서 확인하거나 `skills/b-meme/SKILL.md를 읽고 실행해줘`로 지정한다. 내장 도구를 쓸 수 없는 환경에서는 이미지 생성이 불가능하다. 저장만 실패했다면 이미지를 다시 생성하지 말고 annotations와 manifest로 저장 명령을 재실행한다.
