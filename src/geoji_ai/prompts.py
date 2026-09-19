"""프롬프트 파일 로더(05 §3.6, code-layout).

프롬프트는 저장소 루트 `prompts/` 의 파일이다. 코드 문자열에 넣지 않는다.

- `build_writer_system(intensity)` — 서기 공통 + **그 강도 섹션만**. 강도 섹션은 공통 파일의
  `## 출력` 제목 바로 앞에 끼운다. 과거 버전도 명시적으로 불러올 수 있다.
- `prompt_bundle_version()` — `prompts/` 아래 모든 파일을 상대 경로(POSIX) 순으로 정렬해
  `경로 UTF-8 · NUL · 파일 바이트 · NUL` 을 이어 sha256 한 값의 앞 12자에 `bundle-` 접두
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from geoji_ai.domain.intensity import Intensity, parse_intensity

__all__ = [
    "PROMPTS_DIR",
    "WRITER_VERSION",
    "build_writer_system",
    "load_prompt",
    "prompt_bundle_version",
]

#: 저장소 루트의 `prompts/`(src/geoji_ai/prompts.py 에서 두 단계 위).
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

WRITER_VERSION = "v5.8"

#: 강도 섹션을 끼울 자리. 공통 파일에서 이 제목 바로 앞이다.
_WRITER_OUTPUT_HEADER = "## 출력\n"


def load_prompt(path: str | Path, *, root: Path | None = None) -> str:
    """`root`(기본 `prompts/`) 기준 상대 경로의 프롬프트 파일을 UTF-8 로 읽는다."""
    target = Path(path)
    if not target.is_absolute():
        target = (root or PROMPTS_DIR) / target
    return target.read_text(encoding="utf-8")


def build_writer_system(
    intensity: Intensity | str, version: str = WRITER_VERSION, *, root: Path | None = None
) -> str:
    """서기 시스템 프롬프트 = 공통 + 그 강도 섹션만."""
    key = parse_intensity(intensity)
    common = load_prompt(f"writer/common-{version}.md", root=root)
    section = load_prompt(f"writer/{key.value}-{version}.md", root=root)
    head, sep, tail = common.partition(_WRITER_OUTPUT_HEADER)
    if not sep:
        raise ValueError(f"writer/common-{version}.md 에 {_WRITER_OUTPUT_HEADER.strip()!r} 가 없다")
    return head + section + sep + tail


def prompt_bundle_version(root: Path | None = None) -> str:
    """`prompts/` 전체 파일 해시로 만든 번들 버전. 1바이트만 바뀌어도 달라진다."""
    base = root or PROMPTS_DIR
    files = sorted(
        (path for path in base.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(base).as_posix(),
    )
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(base).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"bundle-{digest.hexdigest()[:12]}"
