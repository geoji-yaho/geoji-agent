---
description: 계획서의 작업 하나 또는 카드 묶음을 orca 워커 스펙 여러 장과 웨이브 순서로 나누고, 확인 뒤 워크트리마다 워커를 띄워 감독한다
argument-hint: "[작업 N | 02 CT-01,CT-02 | 02·04·07]"
---

# orca-plan

코디네이터 쪽 절차다. 이 세션은 사람이 보고 있는 main 워크트리에서 돈다. 워커 쪽 규칙은 `geoji-harness` 스킬의 "orca 워커 모드"와 `orca-worker` 룰, 마무리는 `git-workflow` 룰 "orca 워커" 절에 있다.

감독 루프 자체(`run-create`, `worker-start`, `check --wait`, `reply`, `worker-release`)는 전역 `orchestration` 스킬이 정본이다. 먼저 `orca skills get orchestration`으로 읽는다. 이 커맨드는 스펙을 어떻게 만들고 어느 순서로 띄우는지만 정한다.

## 절차

1. **대상 확정.** `$ARGUMENTS`를 `00 §4` 문서 맵으로 문서·카드로 바꾼다. "작업 N"이면 그 문서 §5 카드 전부
2. **선행 확인.** `00 §6` 사슬에서 선행 문서가 main 에 머지돼 있는지 `git log origin/main`으로 본다. 안 돼 있으면 멈추고 무엇이 먼저인지 알린다. 01 은 전부의 선행이므로 코드가 0줄이면 01 하나만 워커 하나로 띄운다
3. **분할.** 파일 단위로 나눈다. 기준은 `00 §8.2` 소유표와 해당 문서 §3.1. 같은 파일을 두 스펙에 넣지 않는다. `orca-worker` 룰의 공유 자원을 바꾸는 카드는 웨이브 맨 앞에 혼자 둔다. 워커 하나에 파일이 두 자리 수를 넘으면 더 나눈다
4. **스펙 작성.** `.claude/templates/orca-task.md`를 `_workspace/orca/tasks/{feat-NN-topic}.md`로. 이름은 `feat-NN-topic`(orca 가 `/`를 `-`로 바꾸므로 처음부터 대시). 미결정(`00 §8.4`의 `- [ ]`, `10 §14`, `10 §15.1` 백엔드 미제공)에 걸리는 카드는 지금 `AskUserQuestion`으로 사용자에게 묻고 답을 "미리 답한 결정"에 적는다. 워커가 나중에 `ask`로 올리면 한 워커가 한참 논다
5. **웨이브 표.** `_workspace/orca/plan.md`에 웨이브별 스펙·파일·선행을 표로 쓰고 사용자에게 보여 준다. 확인 전에는 띄우지 않는다
6. **띄운다.** 한 웨이브를 한 번에.
   ```bash
   orca status --json
   orca orchestration run-create --objective "{작업 N 웨이브 1}" --json
   orca orchestration worker-start --spec "$(cat _workspace/orca/tasks/feat-NN-topic.md)" \
     --worktree new-top-level --name feat-NN-topic --agent claude --setup run --json
   ```
   PowerShell 이면 `--spec (Get-Content -Raw 파일)`. `--worktree new-top-level`. 워커끼리 부모 관계를 두지 않는다. 워크트리는 `~/orca/workspaces/geoji-agent/{이름}`에 생긴다
7. **감독.** `orchestration` 스킬대로 `check --wait --types "worker_done,escalation,question"`. `question`은 계획서에서 답할 수 있으면 답하고, 아니면 사용자에게 묻고 `reply`. `worker_done`은 `--outcome`과 body 의 PR URL 을 확인한다. 성공이면 `worker-release`, 실패면 body 의 이유를 사용자에게 옮기고 재시도 여부를 묻는다
8. **웨이브 종료.** PR 목록과 각 워커의 "확인하지 못한 것"을 사용자에게. 워커는 `tests/integration`을 뺐으므로, 그것이 완료 기준인 PR(02 등)은 DB 를 띄우고 직접 돌려야 한다고 함께 알린다. **머지는 사용자가 한다.** 다음 웨이브는 머지 뒤 main 에서 다시 2부터
9. **정리.** 워커 워크트리는 PR 머지 뒤 `orca worktree rm`. 사용자가 시킬 때만

## 하지 않는 것

- 코디네이터가 워커의 파일을 직접 고치지 않는다. 지적은 `send --to dispatch:<id>`로 되돌린다
- 워커 대신 커밋·PR 을 만들지 않는다
- `docs/plans/` 카드 체크와 결정 기록은 사용자가 시킬 때만(plans-format 룰)
- 워커 하나가 `ask`로 막혀 있는데 다른 워커를 더 띄워 덮지 않는다. 먼저 답한다
