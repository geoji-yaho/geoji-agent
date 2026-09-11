<!-- orca 워커 작업 스펙 틀. 코디네이터가 /orca-plan 으로 채워 _workspace/orca/tasks/{이름}.md 에 두고
     `orca orchestration worker-start --spec "$(cat 그 파일)"` 로 통째로 넘긴다. Claude Code 는 이 틀을 읽지 않는다 -->

# 작업: {feat-NN-topic}

이 스펙은 orca 워커(Claude Code)에게 주는 전체 지시다. `geoji-harness` 스킬의 "orca 워커 모드"로 진행한다. 이 스펙을 그대로 `_workspace/orca/spec.md`에 저장하는 것이 첫 동작이다.

## Target (대상)

- 문서·카드: {02 CT-01·CT-02. `docs/plans/02-jobs-queue-lease.md` §3.2, §4.2}
- 브랜치: `{feat-NN-topic}`. orca 가 워크트리 이름으로 만든다. 바꾸지 않는다

## Change (만들 것)

계획서 §3.1 표에서 그대로 옮긴다. 표에 없는 파일은 넣지 않는다.

| 경로 | 무엇 | 계획서 |
| ---- | ---- | ------ |
|      |      |        |

테스트(§4.2). 파일별 케이스 수와 "먼저 실패시킬 케이스".

| 파일 | 케이스 |
| ---- | ------ |
|      |        |

## Constraints (제약)

- 계획서에 없는 값은 만들지 않는다. 막히면 preamble 의 `ask`
- "미리 답한 결정" 밖의 미결정·승인 항목은 `ask`. 임시값 금지
- `orca-worker` 룰의 공유 자원은 스펙에 없으면 건드리지 않는다
- 테스트는 fake provider 만. `tests/integration` 은 이 스펙이 `DATABASE_URL` 을 주었을 때만 돌린다
- {이 작업만의 제약. 예: `domain/lexicon.py` 는 가져다 쓰되 복사하지 않는다}

## Ownership (편집 범위)

훅이 이 블록으로 Write/Edit 를 막는다. 글롭은 저장소 루트 기준, `_workspace/` 는 늘 허용.

```json
{
  "owned": ["src/geoji_ai/ports/jobs.py", "tests/unit/test_jobs_*.py"],
  "forbidden": ["contracts/**", "pyproject.toml", "docs/plans/**"]
}
```

- 같은 웨이브의 다른 워커 담당: {브랜치 — 파일 목록. 없으면 "없음"}

## 미리 답한 결정

코디네이터가 계획서(00 §8.4, 10 §14)와 사용자에게서 미리 받은 답. 게이트 A·B 는 이 표로 먼저 푼다.

| 항목 | 답 | 출처 |
| ---- | -- | ---- |
| {없으면 "없음"} |    |      |

## Observable acceptance (완료 증거)

- `uv run ruff check .` · `uv run ruff format --check .` · `uv run pytest -q` 통과
- §4.2 케이스 {n}개가 테스트 파일에 있고 통과. 빠진 케이스 0
- `git-workflow` 룰 "orca 워커" 절대로 PR 이 열려 있고, `worker_done` body 에 PR URL 과 `--report-path`
