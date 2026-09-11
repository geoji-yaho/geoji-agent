# .claude

`geoji-agent`에서 Claude Code 가 읽는 스킬셋. 본문을 이 디렉터리에 직접 둔다. 심볼릭 링크를 쓰지 않는다.

```
CLAUDE.md              늘 지켜야 하는 것. 세션마다 로드
.mcp.json              MCP 서버. 저장소 단위, 서버 하나가 키 하나
.claude/
  README.md            이 파일. 매니페스트와 목록
  settings.json        권한·훅 등록. 팀 공용
  settings.local.json  개인 설정. git 에 안 들어간다
  rules/               룰. paths 없으면 늘, 있으면 그 경로를 읽을 때 로드
  skills/              스킬. 요청이 description 과 맞을 때만 붙는다
  agents/              서브에이전트. Agent 호출의 subagent_type
  commands/            슬래시 커맨드. 파일 이름이 /명령
  hooks/               훅 스크립트. settings.json 에서 가리킨다
  templates/           새 항목을 만들 때 복사하는 틀. Claude Code 는 읽지 않는다
```

## 매니페스트

| 항목        | 값                                                     |
| ----------- | ------------------------------------------------------ |
| name        | geojibang-skillset                                     |
| version     | 0.1.0                                                  |
| 대상 도구   | Claude Code                                            |
| requiredEnv | 없음. 이 스킬셋은 Claude Code 구독만 있으면 돈다. `XAI_API_KEY`·`OPENAI_API_KEY` 는 서비스가 쓰는 키라 `.env.example` 에 둔다 |
| 의존 플러그인 | 없음. `ecc` 는 9/7 켰다가 9/9 뺐다(커밋 a69296c) |
| 병렬 실행   | orca(데스크톱 앱, `orca` CLI). 전역 스킬 `orca-cli`·`orchestration` 은 orca 가 설치한다. 워크트리는 `~/orca/workspaces/geoji-agent/` 아래. 코디네이터는 `/orca-plan`, 워커는 `geoji-harness` 의 "orca 워커 모드"와 `orca-worker` 룰 |
| 환경변수    | `GEOJIBANG_ROOT`. 형제 저장소 루트. 사용자 `~/.claude/settings.json` 의 `env` 에 둔다. 없으면 `..` |

## 룰

| 파일                   | 다루는 것                                                | `paths`                                              |
| ---------------------- | -------------------------------------------------------- | ---------------------------------------------------- |
| `git-workflow.md`      | 브랜치, 커밋 메시지 형식, git add 하지 않는 것            | 없음. 늘 로드                                        |
| `plans-format.md`      | 계획서 골격과 표기, 결정 대장·카드 갱신 절차              | `docs/plans/**`                                      |
| `domain-vocabulary.md` | 서비스 용어, enum 표준, 대문자 식별자, ID 체계, 확정 수치 | `docs/**`, `src/**`, `contracts/**`, `tests/**`, `scripts/**` |
| `code-layout.md`       | uv·Python 3.12, 저장소 배치, 의존 방향                    | `src/**`, `tests/**`, `contracts/**`, `database/**`, `scripts/**`, `pyproject.toml` |
| `testing.md`           | 테스트 디렉터리, fake provider 원칙, 프롬프트 관문, 실측  | `src/**`, `tests/**`, `scripts/**`, `prompts/**`     |
| `orca-worker.md`       | orca 워커 세션의 불변 규칙. 질문은 `ask`, 공유 자원, 통합 테스트, 편집 범위 | 없음. 늘 로드. preamble 없는 세션은 무시 |
| `backend-handoff.md`   | 백엔드가 알아야 할 것(환경변수·엔드포인트·복사 파일·계약 변경·회신 대기)을 `docs/backend-handoff.md` 에 같은 커밋으로 기록 | 없음. 늘 로드 |

## 스킬

| 스킬            | 붙는 자리                                                        |
| --------------- | ---------------------------------------------------------------- |
| `geoji-harness` | 계획서의 카드나 절을 코드로 만들거나 고쳐 달라는 요청. 검증 재실행도 |

## 서브에이전트

| 에이전트               | 맡는 것                                            | 산출                           |
| ---------------------- | -------------------------------------------------- | ------------------------------ |
| `geoji-spec-auditor`   | 계획서에서 카드·파일·계약·테스트 케이스 확인, 미결정 판정 | `_workspace/01_spec.md`        |
| `geoji-implementer`    | 코드와 테스트 작성                                 | 소스·테스트, `_workspace/02_impl.md` |
| `geoji-code-reviewer`  | 의존 방향, 계약 미러, enum, 프롬프트 위치, 단일 정의 | `_workspace/03_code_review.md` |
| `geoji-test-verifier`  | §4.2 케이스 대조, ruff·pytest 게이트, 벤더·DB 격리  | `_workspace/03_test.md`        |

넷을 언제 어떤 순서로 부르는지는 `geoji-harness` 스킬이 정한다. 에이전트를 더하거나 역할을 바꾸면 그 스킬의 표와 Phase도 함께 고친다.

## 슬래시 커맨드

| 명령         | 하는 것 |
| ------------ | ------- |
| `/orca-plan` | 계획서의 작업·카드를 orca 워커 스펙(`templates/orca-task.md`)과 웨이브로 나누고, 확인 뒤 워크트리마다 워커를 띄워 감독한다. 감독 루프 자체는 전역 `orchestration` 스킬 |

## 훅

실행은 `uv run --quiet --no-project .claude/hooks/guard.py`. PEP 723 단독 스크립트라 `.venv` 없이도 돈다. 막을 때 exit 2.

| 이벤트     | 스크립트   | 하는 것 |
| ---------- | ---------- | ------- |
| PreToolUse `Bash` | `guard.py` | 늘: `git add .env`, `git push --force` 차단. 워커 모드: main 커밋·체크아웃, 머지 차단 |
| PreToolUse `Edit·Write·MultiEdit·NotebookEdit` | `guard.py` | 워커 모드: `_workspace/orca/spec.md` 의 Ownership JSON(`owned`·`forbidden`) 밖 편집 차단 |
| PreToolUse `AskUserQuestion` | `guard.py` | 워커 모드: 차단. preamble 의 `orca orchestration ask` 로 |
| Stop       | `guard.py` | 워커 모드: `_workspace/orca/done` 이 없으면 끝내지 못하게 한다(`worker_done` 강제) |

워커 모드 = `_workspace/orca/spec.md` 가 있을 때. 사람 세션에는 늘 규칙 두 개만 걸린다.

## MCP

| 서버 | 쓰는 곳 |
| ---- | ------- |
|      |         |

## 추가하는 법

- **룰**: `templates/rule.md` 를 `rules/{이름}.md` 로 복사. `description` 필수. 늘 적용할 것이면 `paths` 를 빼고 `CLAUDE.md` 규칙 절에 한 줄 더한다
- **스킬**: `templates/SKILL.md` 를 `skills/{이름}/SKILL.md` 로 복사. `name` 은 디렉터리 이름과 같게. 500줄을 넘으면 `references/` 로 나누고 본문에는 언제 읽는지만 남긴다
- **서브에이전트**: `templates/agent.md` 를 `agents/{이름}.md` 로 복사. 호출하는 스킬의 표와 이 파일의 표를 같이 고친다
- **슬래시 커맨드**: `templates/command.md` 를 `commands/{이름}.md` 로 복사. 하위 디렉터리를 두면 `/git:commit` 처럼 네임스페이스가 된다
- **훅**: 스크립트는 `hooks/` 에, 등록은 `settings.json` 의 `hooks` 키에. 이벤트는 PreToolUse·PostToolUse·Stop·SessionStart 등. 훅을 고치면 실행 중인 세션에는 안 먹고 새 세션(또는 `/hooks` 재검토)부터 적용된다
- **orca 워커 스펙**: `templates/orca-task.md` 를 `_workspace/orca/tasks/{feat-NN-topic}.md` 로. `/orca-plan` 이 채운다. 커밋되지 않는다
- **MCP**: 루트 `.mcp.json` 의 `mcpServers` 에 서버 하나를 키 하나로

항목을 더하거나 지우면 위 표를 같이 고친다. 표에 없는 항목은 없는 것으로 친다.
