---
description: 브랜치 전략과 커밋 메시지 형식, git add 하지 않는 것
---

# Git 워크플로우

## 브랜치

- `main` 하나다. 작업은 `main`에서 `docs/{topic}` 또는 `feat/{topic}`으로 분기하고 PR로 `main`에 머지한다
- 한두 줄짜리 문서 갱신은 `main`에 직접 커밋해도 된다
- "커밋하고 푸시해줘"는 두 동작을 한 번에 한다

## 커밋 메시지

- 제목은 `type(scope): 한국어 제목`. type은 `docs`, `feat`, `fix`, `test`, `chore`, `refactor`. 계획서 갱신은 항상 `docs(plans)`
- 결정 기록 커밋은 날짜를 앞에 둔다. `docs(plans): 9/8 인터뷰 결정 기록 — 서버 코드 대조·D-20·D-21`. 나열은 가운뎃점(·), 구분은 em dash(—) 또는 `+`
- 본문은 `- ` 불릿으로 어느 문서의 어느 절이 왜 바뀌었는지 쓴다. 문서는 번호로만 부른다. `01`, `10 §15.2`
- 한 커밋은 한 결정의 전파다. 결정 하나가 여러 문서에 걸치면 한 커밋으로 묶는다
- Claude Code가 주는 `Co-Authored-By`와 `Claude-Session` 트레일러를 붙인다

## git add 하지 않는 것

- `geoji-server/`, `geoji-web/`. 형제 저장소의 로컬 clone이라 의도적으로 untracked
- `.env`. 키 이름만 `.env.example`에 둔다
- `docs/` 중 추적하는 것은 `docs/plans/`뿐이다(`.gitignore`)
