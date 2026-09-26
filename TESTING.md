# 테스트 실행 매뉴얼

테스트를 **돌리는** 절차다. `test-runner` 에이전트(haiku)가 이 파일만 읽고 실행한다. 테스트를 **쓰는** 규칙은 `.claude/rules/testing.md`에 있다.

모든 실행은 `scripts/test.sh`로 한다. pytest나 ruff를 직접 조합하지 않는다.

## 스위트

| 명령 | 범위 | 필요한 것 | 소요 |
| --- | --- | --- | --- |
| `scripts/test.sh lint` | `ruff check .`, `ruff format --check .` | 없음 | 수 초 |
| `scripts/test.sh fast` | `tests/` 전체에서 `tests/integration` 제외 (약 1550개) | 없음. fake provider만 쓴다 | 약 25초 |
| `scripts/test.sh integration` | `tests/integration` | Docker. 스크립트가 `docker-compose.dev.yml`의 Postgres를 띄운다 | 약 2분 |
| `scripts/test.sh all` | lint, fast, integration 순서. 하나가 실패해도 끝까지 돈다 | Docker | 약 2분 30초 |
| `scripts/test.sh path <대상...>` | 지정한 파일이나 노드 ID. 예: `tests/unit/test_budget.py::test_x` | 대상이 `tests/integration`이면 Docker | 대상에 따라 |

`fast`와 `integration` 뒤에는 pytest 인자를 더 붙일 수 있다. 예: `scripts/test.sh fast -k budget`, `scripts/test.sh fast --lf`

## 무엇을 돌릴지

요청에 스위트가 정해져 있으면 그것을 돌린다. 정해져 있지 않으면 아래 표를 따른다.

| 상황 | 돌릴 것 |
| --- | --- |
| "테스트 돌려줘"처럼 범위가 없는 요청 | `lint`, `fast` |
| 특정 파일이나 테스트를 지목한 요청 | `path <대상>` |
| 변경이 `src/geoji_ai/adapters/`, `src/geoji_ai/workers/`, `database/`에 걸친 경우 | `lint`, `fast`, `integration` |
| PR 전, 또는 "전부 돌려줘" | `all` |
| 직전 실패만 다시 확인 | `fast --lf` |

## 종료 코드

| 코드 | 뜻 | 보고 |
| --- | --- | --- |
| 0 | 통과 | 통과 |
| 1 | 테스트나 린트 실패 | 실패 |
| 2 | 실행 불가. `SETUP-FAIL:` 줄이 원인이다 | 실행 불가. 테스트 결과로 치지 않는다 |

## 하지 않는 것

- 실제 벤더(xAI, OpenAI)를 부르는 것은 돌리지 않는다: `tests/evaluations/run_*.py`, `scripts/probe_*.py`, `scripts/run_local_live_e2e.py`. 돈이 나간다
- 코드를 고치지 않는다. `ruff format .`, `ruff check --fix`도 돌리지 않는다. 린트가 실패하면 보고만 한다
- skip, `--deselect`, `-k`로 실패를 빼서 통과를 만들지 않는다
- `.env`의 `DATABASE_URL`을 `TEST_DATABASE_URL`로 쓰지 않는다. 공유 DB의 `ai` 스키마가 재생성된다
- 5432 포트를 다른 컨테이너가 잡고 있어도 그 컨테이너를 멈추지 않는다. 실행 불가로 보고한다
- 실패한 테스트를 여러 번 다시 돌려 통과시키지 않는다. 아래 "알려진 것"에 flaky로 적힌 테스트만 한 번 재시도할 수 있고, 재시도했다는 사실을 보고에 적는다

## 알려진 것

- starlette의 `DeprecationWarning`(`anyio.abc.BlockingPortal`) 경고 1건은 정상이다. 보고하지 않는다
- 알려진 flaky 테스트: 없음

## 보고 형식

"결과" 칸은 직접 세지 않고 출력의 요약 줄을 그대로 옮긴다. lint는 `LINT-SUMMARY:` 줄, pytest는 마지막 줄(`N passed, M failed in …`)이다.

```
## 테스트 결과: 통과 | 실패 | 실행 불가

| 스위트 | 종료 코드 | 결과 |
| --- | --- | --- |
| lint | 1 | ruff_check_errors=17 unformatted_files=2 |
| fast | 1 | 3 failed, 1550 passed in 21.14s |

### 실패 (최대 5건, 나머지는 이름만)
#### tests/unit/test_x.py::test_y
(pytest 출력의 해당 블록을 요약하지 말고 그대로 붙인다. 한 건당 40줄까지)

### 린트 위반
(ruff 출력 그대로. 30줄까지)

### 실행 불가 원인
(SETUP-FAIL 줄 그대로)

### 돌리지 않은 것
(요청이나 표에 있었지만 돌리지 않은 스위트와 그 이유)
```

실패 원인을 추측하거나 고치는 방법을 제안하지 않는다. 판단은 호출한 쪽이 한다.
