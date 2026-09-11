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

- `.env`. 키 이름만 `.env.example`에 둔다. 훅이 `git add .env`를 막는다
- `docs/` 중 추적하는 것은 `docs/plans/`뿐이다(`.gitignore`)
- `git push --force`는 어디서든 쓰지 않는다. 훅이 막는다

## orca 워커(무인)

orca 코디네이터가 워크트리에 띄운 워커 세션에만 적용된다. 사람이 보는 세션은 위 절만 따른다.

- 브랜치는 orca 가 워크트리 이름으로 만든다. 이름은 `feat-{NN}-{topic}`. orca 가 `/`를 `-`로 바꾸므로 처음부터 대시로 짓는다. 워커는 브랜치를 새로 만들거나 옮기지 않는다
- main 에 커밋하지 않는다. 훅이 막는다
- 마무리 순서. 게이트 통과 → `git add <경로>`(경로를 지정한다. `-A` 금지) → 커밋(형식은 위와 같다. 제목에 문서와 카드 번호. `feat(02): CT-01·CT-02 큐 claim·lease`) → `git fetch origin main && git rebase origin/main` → 충돌이면 풀지 말고 preamble 의 `ask` → `git push -u origin HEAD` → `gh pr create --base main --title "<커밋 제목>" --body-file _workspace/04_report.md`
- PR 본문 끝에 `🤖 Generated with [Claude Code](https://claude.com/claude-code)`와 세션 URL 을 붙인다
- 머지하지 않는다. PR URL 을 `worker_done` body 에 넣는다. 머지는 사용자가 한다
