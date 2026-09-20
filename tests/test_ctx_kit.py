"""Tests for ctx-kit local codebase context builder."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from tools.ctx_kit import (
    build_bm25,
    cmd_ask,
    cmd_index,
    cmd_stats,
    extract_python,
    extract_text,
    load_config,
    search,
    should_ignore_file,
    tokenize,
)


def test_tokenize() -> None:
    text = "def calculate_verdict(case_id: int): return 42  # 판결문 계산"
    tokens = tokenize(text)
    assert "def" in tokens
    assert "calculate_verdict" in tokens
    assert "판결문" in tokens
    assert "계산" in tokens
    assert "42" not in tokens  # tokens start with letter/char


def test_extract_python(tmp_path: Path) -> None:
    py_file = tmp_path / "sample.py"
    py_file.write_text(
        """class VerdictJudge:
    \"\"\"Judge that issues verdicts.\"\"\"

    async def decide_penalty(self, amount: int) -> str:
        \"\"\"Decide penalty based on amount.\"\"\"
        return "GUILTY"

def helper() -> None:
    pass
""",
        encoding="utf-8",
    )
    extracted = extract_python(py_file)
    assert 'class VerdictJudge: """Judge that issues verdicts."""' in extracted
    assert 'def decide_penalty: """Decide penalty based on amount."""' in extracted
    assert "def helper" in extracted


def test_extract_python_syntax_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def unclosed_func(:\n", encoding="utf-8")
    extracted = extract_python(broken)
    assert "def unclosed_func(:" in extracted


def test_extract_text(tmp_path: Path) -> None:
    txt_file = tmp_path / "doc.md"
    txt_file.write_text("A" * 5000, encoding="utf-8")
    assert len(extract_text(txt_file, max_chars=100)) == 100


def test_should_ignore_file() -> None:
    patterns = [".env", ".env.*", ".DS_Store", "*.log"]
    assert should_ignore_file(Path(".env"), patterns) is True
    assert should_ignore_file(Path(".env.local"), patterns) is True
    assert should_ignore_file(Path("debug.log"), patterns) is True
    assert should_ignore_file(Path("main.py"), patterns) is False
    assert should_ignore_file(Path("README.md"), patterns) is False


def test_bm25_scoring_and_search() -> None:
    docs = {
        "auth.py": "def authenticate_user(username, password): pass",
        "billing.py": "def process_payment(amount, card): pass",
        "verdict.py": "def make_verdict(case, penalty): pass",
    }
    index = build_bm25(docs)
    assert index["N"] == 3
    assert "authenticate_user" in index["df"]

    results = search(index, "authenticate user password", top_k=2)
    assert len(results) >= 1
    assert results[0][0] == "auth.py"
    assert results[0][1] > 0.0

    unrelated = search(index, "completely_non_existent_symbol", top_k=2)
    assert unrelated == []


def test_load_config_fallback(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg["index_file"] == ".ctx-index.json"
    assert ".py" in cfg["supported_exts"]
    assert ".git" in cfg["ignore_dirs"]


def test_load_config_custom(tmp_path: Path) -> None:
    cfg_file = tmp_path / "ctx-kit.config.json"
    cfg_file.write_text(json.dumps({"top_k": 10, "supported_exts": [".py"]}), encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg["top_k"] == 10
    assert cfg["supported_exts"] == [".py"]


def test_cmd_index_and_ask_and_stats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    # Setup files in tmp_path
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "service.py").write_text(
        "def evaluate_sentencing():\n    '''Determine jail or fine.'''\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("SECRET_KEY=leak", encoding="utf-8")

    # 1. Index
    args_index = argparse.Namespace(
        directory=str(tmp_path),
        output=None,
        config=None,
    )
    ret_index = cmd_index(args_index)
    assert ret_index == 0

    idx_file = tmp_path / ".ctx-index.json"
    assert idx_file.is_file()

    idx_data = json.loads(idx_file.read_text(encoding="utf-8"))
    assert ".env" not in idx_data["content"]
    assert "src/service.py" in idx_data["content"]

    # 2. Ask
    args_ask = argparse.Namespace(
        question="sentencing evaluation",
        index=str(idx_file),
        top=3,
        copy=False,
        config=None,
    )
    ret_ask = cmd_ask(args_ask)
    assert ret_ask == 0

    # 3. Stats
    args_stats = argparse.Namespace(
        index=str(idx_file),
        config=None,
    )
    ret_stats = cmd_stats(args_stats)
    assert ret_stats == 0


def test_cmd_ask_missing_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    args_ask = argparse.Namespace(
        question="anything",
        index=str(tmp_path / "non_existent.json"),
        top=5,
        copy=False,
        config=None,
    )
    ret_ask = cmd_ask(args_ask)
    assert ret_ask == 1
