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
    "JUROR_VERSION",
    "PROMPTS_DIR",
    "WRITER_VERSION",
    "BANTER_PROMPT",
    "build_banter_system",
    "build_evaluator_system",
    "build_juror_system",
    "SENTENCING_PROMPT",
    "build_writer_system",
    "load_prompt",
    "prompt_bundle_version",
]

#: 저장소 루트의 `prompts/`(src/geoji_ai/prompts.py 에서 두 단계 위).
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

WRITER_VERSION = "v6.2"
BANTER_PROMPT = "banter-v3.md"
SENTENCING_PROMPT = "sentencing-v2.md"

#: 배심원 프롬프트 버전(18 §3.3).
JUROR_VERSION = "v1"

#: 강도 섹션을 끼울 자리. 공통 파일에서 이 제목 바로 앞이다.
_WRITER_OUTPUT_HEADER = "## 출력\n"

#: 배심원 프롬프트에서 강도 섹션을 붙일 자리. 이 제목 **아래**다(18 §3.3).
_JUROR_INTENSITY_HEADER = "## 강도\n"


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


def build_juror_system(intensity: Intensity | str, *, root: Path | None = None) -> str:
    """배심원 시스템 프롬프트 = `juror-v1.md` 본문 + 그 강도의 서기 강도 섹션(18 §3.3).

    강도 정의는 단일 정의(06 §3.3)라 복사하지 않고 `writer/{intensity}-{WRITER_VERSION}.md` 파일을
    그대로 재사용한다. 붙이는 자리는 `## 강도` 제목 **아래**다(서기 조립과 삽입 방식이 다르다).
    """
    key = parse_intensity(intensity)
    body = load_prompt(f"juror-{JUROR_VERSION}.md", root=root)
    section = load_prompt(f"writer/{key.value}-{WRITER_VERSION}.md", root=root)
    head, sep, tail = body.partition(_JUROR_INTENSITY_HEADER)
    if not sep:
        raise ValueError(f"juror-{JUROR_VERSION}.md 에 {_JUROR_INTENSITY_HEADER.strip()!r} 가 없다")
    return head + sep + section + tail


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


def build_banter_system(intensity: Intensity | str, *, root: Path | None = None) -> str:
    """드립 후보도 현재 서기와 같은 강도 정의·예시를 본다."""
    key = parse_intensity(intensity)
    return (
        load_prompt(BANTER_PROMPT, root=root)
        + "\n"
        + load_prompt(f"writer/{key.value}-{WRITER_VERSION}.md", root=root)
    )


def build_evaluator_system(policy: str, *, root: Path | None = None) -> str:
    """백엔드 정책 enum 을 유지하며 v2의 프롬프트 리비전을 별도 파일로 보존한다."""
    path = "evaluator/guardrail-v2.3.md" if policy == "guardrail-v2" else f"evaluator/{policy}.md"
    prompt = load_prompt(path, root=root)
    if policy == "guardrail-v2":
        prompt += "\n## 문체 기준 (예시는 다른 사건이며 그대로 베끼는지 검사하지 않는다)\n"
        prompt += "\n".join(
            load_prompt(f"writer/{key}-{WRITER_VERSION}.md", root=root)
            for key in ("mild", "spicy", "hell")
        )
    return prompt
