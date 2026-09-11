"""정본 JSON 이 미러에서 재생성한 것과 같은지(`tools/gen_contracts.py`).

정본을 손으로 고쳤거나 미러만 고치고 재생성을 잊으면 여기서 빨개진다.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT, SCHEMA_NAMES, schema_path

GEN = REPO_ROOT / "tools" / "gen_contracts.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_contracts", GEN)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_committed_schema_equals_regenerated(name: str) -> None:
    rendered = _load_generator().render_all()
    committed = Path(schema_path(name)).read_text(encoding="utf-8")
    assert committed == rendered[f"{name}-v1"], (
        f"{name}-v1.schema.json 이 미러와 다르다. "
        "`uv run python tools/gen_contracts.py` 로 재생성한다"
    )
