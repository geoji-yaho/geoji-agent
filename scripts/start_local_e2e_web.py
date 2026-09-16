#!/usr/bin/env python3
"""--keep E2E의 로컬 JWT로 기존 Vite 개발 모드를 실행한다. 키는 출력하지 않는다."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--frontend", type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    state_path = directory / "state.json"
    state = json.loads(state_path.read_text())
    if directory != Path(state["directory"]).resolve() or not directory.name.startswith(
        "geoji-e2e-"
    ):
        raise ValueError("E2E 실행 디렉터리가 아닙니다.")
    backend = urlparse(state["backend"])
    if backend.scheme != "http" or backend.hostname != "127.0.0.1":
        raise ValueError("독립 로컬 백엔드만 허용합니다.")
    if "frontend" in state["pids"]:
        raise ValueError("이미 프론트 프로세스가 기록돼 있습니다. 이전 실행을 정리하세요.")
    pnpm = shutil.which("pnpm")
    if not pnpm:
        raise FileNotFoundError("Node 24와 pnpm을 PATH에 설정하세요.")
    env = {
        **os.environ,
        "VITE_API_BASE_URL": state["backend"],
        "VITE_SUPABASE_URL": "http://127.0.0.1:19090",
        "VITE_SUPABASE_ANON_KEY": "local-public-placeholder",
        "VITE_DEV_ACCESS_TOKEN": state["tokens"]["00000000-0000-4000-8000-000000000001"],
    }
    with (directory / "frontend.log").open("ab") as stream:
        proc = subprocess.Popen(
            [pnpm, "dev", "--host", "127.0.0.1", "--strictPort"],
            cwd=args.frontend,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    state["pids"]["frontend"] = proc.pid
    state["started"]["frontend"] = subprocess.check_output(
        ["ps", "-p", str(proc.pid), "-o", "lstart="], text=True
    ).strip()
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(state_path)
    print(f"프론트 시작: http://localhost:3800 — 로그: {directory / 'frontend.log'}")


if __name__ == "__main__":
    main()
