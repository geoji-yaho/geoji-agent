# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code 훅. settings.json 이 PreToolUse(Edit·Write·MultiEdit·NotebookEdit·Bash·AskUserQuestion)와 Stop 에 건다.

막을 때는 exit 2 와 stderr 한 줄. 통과는 exit 0. 실행은 `uv run --quiet --no-project .claude/hooks/guard.py`.

두 층이다.
- 늘: `git add .env`, `git push --force`
- orca 워커 모드(`_workspace/orca/spec.md` 가 있을 때): 스펙 Ownership 밖 편집, main 커밋·체크아웃, 머지,
  AskUserQuestion, worker_done 없이 끝내기(`_workspace/orca/done` 없음)
"""

from __future__ import annotations

import fnmatch
import json
import os
import pathlib
import re
import subprocess
import sys

SPEC = pathlib.Path("_workspace/orca/spec.md")
DONE = pathlib.Path("_workspace/orca/done")
ALWAYS_WRITABLE = ("_workspace/",)


def deny(msg: str) -> None:
    # Windows 콘솔 코드페이지(cp949)로 나가면 Claude Code 가 UTF-8 로 읽어 깨진다
    sys.stderr.reconfigure(encoding="utf-8")
    print(f"[guard] {msg}", file=sys.stderr)
    sys.exit(2)


def load_ownership() -> dict | None:
    """워커 모드가 아니면 None. 워커 모드면 스펙의 첫 ```json 블록에서 owned·forbidden 을 읽는다."""
    if not SPEC.exists():
        return None
    text = SPEC.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL):
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if "owned" in d:
            return {"owned": list(d.get("owned") or []), "forbidden": list(d.get("forbidden") or [])}
    return {"owned": [], "forbidden": []}


def relpath(path: str, cwd: pathlib.Path) -> str | None:
    p = pathlib.Path(path)
    if not p.is_absolute():
        p = cwd / p
    try:
        return p.resolve().relative_to(cwd.resolve()).as_posix()
    except ValueError:
        return None


def matches(rel: str, globs: list[str]) -> bool:
    for g in globs:
        g = g.replace("\\", "/")
        if g.endswith("/"):
            if rel.startswith(g):
                return True
        elif fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(rel, g.rstrip("/") + "/*"):
            return True
    return False


def current_branch() -> str:
    try:
        # rev-parse 는 커밋이 없는 브랜치에서 실패한다. symbolic-ref 는 그때도 이름을 준다
        out = subprocess.run(
            ["git", "symbolic-ref", "--short", "-q", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def guard_edit(inp: dict, cwd: pathlib.Path, own: dict | None) -> None:
    if own is None:
        return
    fp = inp.get("file_path") or inp.get("notebook_path")
    if not fp:
        return
    rel = relpath(fp, cwd)
    if rel is None:
        deny(f"워커 모드: 저장소 밖 파일은 편집하지 않는다 — {fp}")
    if rel.startswith(ALWAYS_WRITABLE):
        return
    if matches(rel, own["forbidden"]):
        deny(f"워커 모드: 스펙 Ownership 의 forbidden 경로 — {rel}. 필요하면 orca ask 로 범위를 받는다")
    if own["owned"] and not matches(rel, own["owned"]):
        deny(f"워커 모드: 스펙 Ownership 의 owned 밖 — {rel}. 필요하면 orca ask 로 범위를 받는다")


def guard_bash(cmd: str, worker: bool) -> None:
    # 같은 명령 조각(줄·;·&&·| 로 끊기기 전) 안의 인자만 본다. 커밋 메시지나 heredoc 본문의 글자에는 걸리지 않게
    seg = r"[^\n;&|]*"
    if re.search(r"\bgit\s+add\b" + seg + r"[\s'\"/]\.env(?![.\w-])", cmd):
        deny(".env 는 git add 하지 않는다. 키 이름만 .env.example 에 둔다")
    if re.search(r"\bgit\s+push\b" + seg + r"\s(--force(?!-with-lease)\b|-f\b)", cmd):
        deny("git push --force 는 쓰지 않는다")
    if not worker:
        return
    if re.search(r"\bgit\s+(checkout|switch)\s+(main|master)\b", cmd):
        deny("워커 모드: main 으로 옮기지 않는다. 자기 브랜치에서만 작업한다")
    if re.search(r"\bgit\s+merge\b", cmd) or re.search(r"\bgh\s+pr\s+merge\b", cmd):
        deny("워커 모드: 머지하지 않는다. PR 을 열고 worker_done 에 URL 을 넣는다")
    if re.search(r"\bgit\s+commit\b", cmd) and current_branch() in ("main", "master"):
        deny("워커 모드: main 에 커밋하지 않는다. orca 가 만든 feat-* 브랜치인지 확인한다")


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    cwd = pathlib.Path(data.get("cwd") or os.getcwd())
    os.chdir(cwd)
    own = load_ownership()
    worker = own is not None
    event = data.get("hook_event_name", "")
    tool = data.get("tool_name", "")
    inp = data.get("tool_input") or {}

    if event == "Stop":
        if worker and not DONE.exists() and not data.get("stop_hook_active"):
            deny(
                "워커 모드: worker_done 을 보내기 전에는 끝내지 않는다. "
                "보고서를 쓰고 preamble 의 send --type worker_done 을 보낸 뒤 _workspace/orca/done 을 만든다"
            )
        return

    if event != "PreToolUse":
        return
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        guard_edit(inp, cwd, own)
    elif tool == "Bash":
        guard_bash(str(inp.get("command", "")), worker)
    elif tool == "AskUserQuestion" and worker:
        deny(
            "워커 모드: 사람이 이 터미널을 보지 않는다. preamble 의 orca orchestration ask 로 코디네이터에게 묻는다"
        )


if __name__ == "__main__":
    main()
