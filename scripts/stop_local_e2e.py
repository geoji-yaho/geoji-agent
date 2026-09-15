#!/usr/bin/env python3
"""--keep으로 남긴 이번 실행의 프로세스와 Docker DB만 종료한다."""

import argparse
import json
import os
import signal
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    state = json.loads((directory / "state.json").read_text())
    if directory != Path(state["directory"]).resolve() or not directory.name.startswith(
        "geoji-e2e-"
    ):
        raise ValueError("E2E 실행 디렉터리가 아닙니다.")
    for name, pid in reversed(list(state["pids"].items())):
        current = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True
        )
        if current.returncode != 0:
            continue
        if current.stdout.strip() != state["started"].get(name):
            raise ValueError(f"{name} PID가 다른 프로세스에 재사용되어 종료하지 않습니다.")
        os.killpg(pid, signal.SIGTERM)
    label = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            '{{index .Config.Labels "geoji.local-e2e"}}',
            state["container"],
        ],
        capture_output=True,
        text=True,
    )
    if label.returncode == 0:
        if label.stdout.strip() != "true":
            raise ValueError("이번 E2E가 소유한 Docker 컨테이너가 아닙니다.")
        subprocess.run(["docker", "stop", state["container"]], check=True)
    print("E2E 프로세스에 종료 신호를 보냈고 전용 DB를 정리했습니다. 증거 파일은 유지합니다.")


if __name__ == "__main__":
    main()
