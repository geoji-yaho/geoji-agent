"""프롬프트 파일·강도 분리·번들 버전(05 §3.6, 스펙 케이스 ⑦~⑩).

⑦ 은 실측 스크립트를 import 하지 않는다(openai 의존). `ast` 로 `SYSTEM_PROMPT` 리터럴을 꺼내
스크립트의 `build_system` 조립 로직을 그대로 재현해 비교한다.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest

from geoji_ai.prompts import (
    PROMPTS_DIR,
    build_writer_system,
    load_prompt,
    prompt_bundle_version,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE = REPO_ROOT / "scripts" / "probe_writer_latency.py"


def _probe_system_prompt() -> str:
    tree = ast.parse(PROBE.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "SYSTEM_PROMPT"
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("SYSTEM_PROMPT 를 찾지 못했다")


def _probe_build_system(intensities: list[str]) -> str:
    """`scripts/probe_writer_latency.py` `build_system` 과 같은 로직."""
    system_prompt = _probe_system_prompt()
    head, _, rest = system_prompt.partition("## 강도\n")
    sections, _, tail = rest.partition("## 출력\n")
    keep = [
        "### " + sec for sec in sections.split("### ")[1:] if sec.split(" ", 1)[0] in intensities
    ]
    return head + "## 강도\n" + "".join(keep) + "## 출력\n" + tail


@pytest.mark.parametrize("intensity", ["mild", "spicy", "hell"])
def test_writer_system_equals_probe_assembly(intensity: str) -> None:
    """⑦ `build_writer_system(i)` 가 실측 스크립트의 기존 조립 결과와 같다."""
    expected = _probe_build_system([intensity.upper()])
    assert build_writer_system(intensity).strip() == expected.strip()


def test_spicy_system_has_no_hell_section() -> None:
    """⑧ `spicy` 결과에 `hell` 섹션 문자열 없음."""
    spicy = build_writer_system("spicy")
    hell_section = load_prompt("writer/hell-v5.3.md").strip()
    assert hell_section not in spicy
    assert "### HELL" not in spicy
    for line in (ln for ln in hell_section.splitlines() if ln.strip()):
        assert line not in spicy
    assert "### SPICY" in spicy


@pytest.mark.parametrize(
    "relative",
    [
        "writer/common-v5.3.md",
        "writer/mild-v5.3.md",
        "writer/spicy-v5.3.md",
        "writer/hell-v5.3.md",
        "sentencing-v1.md",
        "context-v1.md",
        "banter-v1.md",
        "evaluator/guardrail-v2.md",
        "evaluator/guardrail-v1.md",
    ],
)
def test_prompt_files_exist(relative: str) -> None:
    """⑨ 5종 파일 존재."""
    path = PROMPTS_DIR / relative
    assert path.is_file()
    assert load_prompt(relative).strip()


def test_bundle_version_changes_on_one_byte(tmp_path: Path) -> None:
    """⑩ 파일 1바이트 바꾸면 bundle 버전이 바뀐다(tmp 복사본으로)."""
    copy_root = tmp_path / "prompts"
    shutil.copytree(PROMPTS_DIR, copy_root)
    original = prompt_bundle_version()
    assert original.startswith("bundle-") and len(original) == len("bundle-") + 12
    assert prompt_bundle_version(copy_root) == original

    target = copy_root / "sentencing-v1.md"
    data = bytearray(target.read_bytes())
    data[-1] = ord("!") if data[-1] != ord("!") else ord("?")
    target.write_bytes(bytes(data))
    assert prompt_bundle_version(copy_root) != original


def test_unknown_intensity_rejected() -> None:
    with pytest.raises(ValueError):
        build_writer_system("MILD")
