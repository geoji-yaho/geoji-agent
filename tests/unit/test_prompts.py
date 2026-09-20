"""프롬프트 파일·강도 분리·번들 버전(05 §3.6, 스펙 케이스 ⑦~⑩).

⑦ 은 실측 스크립트를 import 하지 않는다(openai 의존). `ast` 로 `SYSTEM_PROMPT` 리터럴을 꺼내
스크립트의 `build_system` 조립 로직을 그대로 재현해 비교한다.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path

import pytest

from geoji_ai.contracts.writer import CARD_STATEMENT_MAX
from geoji_ai.domain import lexicon
from geoji_ai.domain.validation import apply_text_rules
from geoji_ai.prompts import (
    JUROR_VERSION,
    PROMPTS_DIR,
    WRITER_VERSION,
    build_juror_system,
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
    """⑦ 보존한 v5.5 가 실측 스크립트의 기존 조립 결과와 같다."""
    expected = _probe_build_system([intensity.upper()])
    assert build_writer_system(intensity, version="v5.5").strip() == expected.strip()


@pytest.mark.parametrize("intensity", ["mild", "spicy", "hell"])
def test_current_writer_requests_one_short_card_with_metadata(intensity: str) -> None:
    system = build_writer_system(intensity)
    assert WRITER_VERSION == "v6.4"
    assert "headline: 1~20자" in system
    assert "statement: 정확히 1항목" in system
    assert "text는 1~100자" in system
    assert "공백과 문장부호도 글자 수에 포함" in system
    assert "줄바꿈과 공백뿐인 값은 금지" in system
    assert "JSON 객체 1개" in system
    for field in (
        "intensity",
        "evidence_labels",
        "kind",
        "banter_strategy",
        "selected_candidate_id",
        "attack_angle",
        "meme_tag",
        "meme_hints",
        "emotion",
        "keywords",
    ):
        assert field in system
    for obsolete in ("evidence_ids", "texts 항목", "2~3문장", "200자", "짧은 두 문장 허용"):
        assert obsolete not in system


@pytest.mark.parametrize("intensity", ["mild", "spicy", "hell"])
def test_current_writer_examples_fit_card_body(intensity: str) -> None:
    section = load_prompt(f"writer/{intensity}-{WRITER_VERSION}.md")
    examples = re.findall(r'^- 예시: "([^"]+)"$', section, re.MULTILINE)
    assert examples
    assert all(1 <= len(example) <= CARD_STATEMENT_MAX for example in examples)
    for example in examples:
        draft = {
            "texts": [
                {
                    "intensity": intensity,
                    "headline": "사건 제목",
                    "statement": [{"text": example, "kind": "claim", "evidence_labels": ["F0"]}],
                }
            ]
        }
        checked = apply_text_rules(
            draft, {"F0": "example"}, None, {"target_intensities": [intensity]}
        )
        assert not checked.issues, (example, checked.issues)


@pytest.mark.parametrize("intensity", ["mild", "spicy", "hell"])
def test_current_length_section_preserves_chat_fragments(intensity: str) -> None:
    """100자 카드 한 줄 안에서 감탄·되묻기·문장 조각을 살린다."""
    system = build_writer_system(intensity)
    assert system.index("## 길이") < system.index("## 강도")
    assert "**100자 이내**" in system
    assert "목표는 40~90자" in system
    assert "문장 조각은 여러 개여도 된다" in system
    assert "30자" not in system
    section = load_prompt(f"writer/{intensity}-{WRITER_VERSION}.md")
    per_intensity = {
        "mild": "짧은 당부",
        "spicy": "약 올리는",
        "hell": "욕은 필수가 아니다",
    }
    assert per_intensity[intensity] in section


def test_spicy_system_has_no_hell_section() -> None:
    """⑧ `spicy` 결과에 `hell` 섹션 문자열 없음."""
    spicy = build_writer_system("spicy")
    hell_section = load_prompt(f"writer/hell-{WRITER_VERSION}.md").strip()
    assert hell_section not in spicy
    assert "### HELL" not in spicy
    assert "### SPICY" in spicy


@pytest.mark.parametrize(
    "relative",
    [
        "writer/common-v5.3.md",
        "writer/mild-v5.3.md",
        "writer/spicy-v5.3.md",
        "writer/hell-v5.3.md",
        "writer/common-v5.4.md",
        "writer/mild-v5.4.md",
        "writer/spicy-v5.4.md",
        "writer/hell-v5.4.md",
        "writer/common-v5.5.md",
        "writer/mild-v5.5.md",
        "writer/spicy-v5.5.md",
        "writer/hell-v5.5.md",
        "writer/common-v5.6.md",
        "writer/mild-v5.6.md",
        "writer/spicy-v5.6.md",
        "writer/hell-v5.6.md",
        "writer/common-v5.7.md",
        "writer/mild-v5.7.md",
        "writer/spicy-v5.7.md",
        "writer/hell-v5.7.md",
        "writer/common-v5.8.md",
        "writer/mild-v5.8.md",
        "writer/spicy-v5.8.md",
        "writer/hell-v5.8.md",
        "sentencing-v1.md",
        "context-v1.md",
        "banter-v1.md",
        "banter-v2.md",
        "evaluator/guardrail-v2.md",
        "evaluator/guardrail-v1.md",
        "juror-v1.md",
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


# ---------------------------------------------------------------------------
# 배심원 프롬프트(18 §3.3·§4.2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("intensity", ["mild", "spicy", "hell"])
def test_juror_system_is_body_plus_that_intensity_section(intensity: str) -> None:
    """`juror-v1.md` 본문 + `writer/{intensity}-{WRITER_VERSION}.md` 를 `## 강도` 아래에."""
    system = build_juror_system(intensity)
    body = load_prompt(f"juror-{JUROR_VERSION}.md")
    section = load_prompt(f"writer/{intensity}-{WRITER_VERSION}.md")

    head, sep, tail = body.partition("## 강도\n")
    assert sep, "juror 프롬프트에 `## 강도` 제목이 있어야 한다"
    assert system == head + sep + section + tail
    assert system.index("## 강도") < system.index(section.splitlines()[0])
    # 본문의 고정 규칙이 그대로 남아 있다.
    assert "60자 이내" in system
    assert "allowed_verdicts" in system


def test_juror_system_differs_per_intensity() -> None:
    systems = [build_juror_system(i) for i in ("mild", "spicy", "hell")]
    assert len(set(systems)) == 3
    hell_section = load_prompt(f"writer/hell-{WRITER_VERSION}.md").strip()
    assert hell_section in build_juror_system("hell")
    assert hell_section not in build_juror_system("spicy")


def test_juror_system_rejects_unknown_intensity() -> None:
    with pytest.raises(ValueError):
        build_juror_system("HELL")


def test_unknown_intensity_rejected() -> None:
    with pytest.raises(ValueError):
        build_writer_system("MILD")


# ---------------------------------------------------------------------------
# 골든셋·어휘와 프롬프트(06 §4.2)
# ---------------------------------------------------------------------------

GOLDEN_DIR = REPO_ROOT / "tests" / "evaluations" / "golden"
#: 골든 사건 소재와 겹치면 안 되는 프롬프트(서기·검수관 예시가 들어 있는 파일).
EXAMPLE_PROMPTS = ("writer", "evaluator")


def _golden_items() -> list[str]:
    items: list[str] = []
    for path in sorted(GOLDEN_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                items.append(json.loads(line)["snapshot"]["item"])
    return items


def _example_prompt_text() -> str:
    files = sorted(p for folder in EXAMPLE_PROMPTS for p in (PROMPTS_DIR / folder).rglob("*.md"))
    assert files
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def test_golden_items_not_in_prompt_examples() -> None:
    """골든 사건 품목 전체를 예시로 복사하지 않는다. 교체·라지 같은 부분 문자열은 제외."""
    items = _golden_items()
    assert len(items) == 50
    prompts = _example_prompt_text()
    hits = [(item, word) for item in items for word in [item] if len(word) >= 2 and word in prompts]
    assert hits == []


#: `WORN_PHRASES` 를 금지어로 선언하는 줄(prompts 수정 금지라 허용 위치를 못박는다).
#: v6 에서 선언 줄이 없어지면 이 목록을 비워 단순 부재 검사로 좁힌다.
WORN_PHRASE_DECLARATIONS = {
    ("writer/common-v5.3.md", "금지어"),
    ("writer/common-v5.4.md", "금지어"),
    ("writer/common-v5.5.md", "금지어"),
    ("writer/common-v5.6.md", "금지어"),
    ("writer/common-v5.7.md", "금지어"),
    ("writer/common-v5.8.md", "금지어"),
    ("writer/common-v6.0.md", "금지어"),
    ("writer/common-v6.1.md", "금지어"),
    ("writer/common-v6.4.md", "금지어"),
    ("writer/common-v6.2.md", "금지어"),
    ("evaluator/guardrail-v2.4.md", "| 금지 | 금지 | 금지 |"),
    ("evaluator/guardrail-v2.2.md", "| 금지 | 금지 | 금지 |"),
    ("evaluator/guardrail-v2.1.md", "| 금지 | 금지 | 금지 |"),
    ("evaluator/guardrail-v2.md", "| 금지 | 금지 | 금지 |"),
}


def test_worn_phrases_only_as_ban() -> None:
    """`WORN_PHRASES` 가 프롬프트 예시 문장에 없다. 알려진 금지 선언 줄에만 나온다."""
    hits = {
        path.relative_to(PROMPTS_DIR).as_posix()
        for path in sorted(PROMPTS_DIR.rglob("*.md"))
        for line in path.read_text(encoding="utf-8").splitlines()
        for phrase in lexicon.WORN_PHRASES
        if phrase in line
        and not any(
            path.relative_to(PROMPTS_DIR).as_posix() == name and marker in line
            for name, marker in WORN_PHRASE_DECLARATIONS
        )
    }
    assert hits == set()
    for name, marker in WORN_PHRASE_DECLARATIONS:
        lines = (PROMPTS_DIR / name).read_text(encoding="utf-8").splitlines()
        declared = [line for line in lines if marker in line]
        assert sum(p in line for line in declared for p in lexicon.WORN_PHRASES) == 1, name


def _after(text: str, marker: str) -> str:
    start = text.index(marker) + len(marker)
    return text[start:].splitlines()[0]


def test_hell_profanity_list_equals_lexicon() -> None:
    """v5.4 서기 섹션의 hell 허용 목록 = `lexicon`(옛 버전 고정)."""
    hell = load_prompt("writer/hell-v5.4.md")
    listed = _after(hell, "**이 목록 안에서만**:").strip().rstrip(".")
    assert tuple(word.strip() for word in listed.split(",")) == lexicon.HELL_ALLOWED_PROFANITY


def test_현행_프롬프트에는_지옥맛_허용_목록이_없다() -> None:
    """9/16 결정: 지옥맛은 비속어를 제한하지 않는다(`lexicon._RULE_INTENSITIES`).

    서기 v5.5·검수관 guardrail-v2(9/16 개정)에 허용 목록이 남아 있으면 모델이 없는 규칙을 지킨다.
    """
    hell = load_prompt(f"writer/hell-{WRITER_VERSION}.md")
    assert "이 목록 안에서만" not in hell
    assert "허용 목록은 없다" in hell

    guardrail = load_prompt("evaluator/guardrail-v2.md")
    assert "hell 허용 목록(" not in guardrail
    assert "판결당 최대 1회" not in guardrail
    # 비속어 목록 자체는 mild·spicy 판정에 쓰이므로 그대로다.
    profanity = _after(guardrail, "| 비속어(").split(")")[0]
    assert tuple(profanity.split("·")) == lexicon.PROFANITY[:24]
    current = load_prompt("evaluator/guardrail-v2.2.md")
    for word in lexicon.SPICY_ALLOWED_PROFANITY:
        assert word in current
