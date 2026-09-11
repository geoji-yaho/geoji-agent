"""공용 테스트 헬퍼. fixture·스키마 경로와 로더.

테스트는 fake provider 만 쓴다. 실제 벤더·DB 를 부르는 테스트를 여기 두지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "contracts"
FIXTURES_DIR = CONTRACTS_DIR / "fixtures"

SCHEMA_NAMES = (
    "intake",
    "case-snapshot",
    "sentencing",
    "writer-draft",
    "evaluation",
    "finalize",
    "verdict-view",
)


def schema_path(name: str) -> Path:
    """`intake` → `contracts/intake-v1.schema.json`."""
    return CONTRACTS_DIR / f"{name}-v1.schema.json"


def load_schema(name: str) -> dict:
    return json.loads(schema_path(name).read_text(encoding="utf-8"))


def fixture_path(name: str) -> Path:
    """`taxi-hell-input` → `contracts/fixtures/taxi-hell-input.json`."""
    return FIXTURES_DIR / f"{name}.json"


def load_fixture(name: str) -> dict:
    return json.loads(fixture_path(name).read_text(encoding="utf-8"))


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def contracts_dir() -> Path:
    return CONTRACTS_DIR
