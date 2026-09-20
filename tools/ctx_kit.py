"""ctx-kit — local codebase context builder for AI queries. Zero upload.

Index codebase with BM25, query relevant files, and format context blocks for LLMs.
Configured via ctx-kit.config.json or built-in defaults.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import math
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_FILE = "ctx-kit.config.json"
DEFAULT_INDEX_FILE = ".ctx-index.json"
DEFAULT_SUPPORTED_EXTS = {
    ".py",
    ".md",
    ".json",
    ".sql",
    ".toml",
    ".yaml",
    ".yml",
    ".ts",
    ".js",
    ".txt",
}
DEFAULT_IGNORE_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "coverage",
    "_workspace",
    "_workspace_prev",
    "outputs",
    "inputs",
    ".idea",
}
DEFAULT_IGNORE_FILES = {
    ".env",
    ".env.*",
    ".DS_Store",
    "uv.lock",
    ".ctx-index.json",
}


def load_config(root: Path, config_file: str | None = None) -> dict[str, Any]:
    """Load configuration from file or return defaults."""
    cfg_path = root / (config_file or DEFAULT_CONFIG_FILE)
    if cfg_path.is_file():
        try:
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as err:
            print(f"[ctx-kit] Warning: failed to parse {cfg_path.name}: {err}", file=sys.stderr)

    return {
        "index_file": DEFAULT_INDEX_FILE,
        "supported_exts": sorted(DEFAULT_SUPPORTED_EXTS),
        "ignore_dirs": sorted(DEFAULT_IGNORE_DIRS),
        "ignore_files": sorted(DEFAULT_IGNORE_FILES),
        "top_k": 5,
        "max_chars_per_file": 3000,
    }


def should_ignore_file(rel_path: Path, ignore_files: list[str]) -> bool:
    """Check if file matches any ignore pattern."""
    filename = rel_path.name
    for pattern in ignore_files:
        if fnmatch.fnmatch(filename, pattern):
            return True
    return False


# ── Extraction ────────────────────────────────────────────────────────────────


def extract_python(path: Path) -> str:
    """Extract function/class signatures and docstrings from Python source."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content)
    except (SyntaxError, UnicodeDecodeError):
        return path.read_text(encoding="utf-8", errors="replace")[:2000]

    snippets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "def"
            sig = f"{kind} {node.name}"
            doc = ast.get_docstring(node)
            if doc:
                first_line = doc.strip().split("\n")[0][:200]
                sig += f': """{first_line}"""'
            snippets.append(sig)

    return "\n".join(snippets) if snippets else content[:2000]


def extract_text(path: Path, max_chars: int = 3000) -> str:
    """Extract initial content for non-python files."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except Exception:
        return ""


def extract(path: Path, max_chars: int = 3000) -> str:
    """Extract file representation for indexing and prompt context."""
    if path.suffix == ".py":
        return extract_python(path)
    return extract_text(path, max_chars=max_chars)


# ── BM25 ──────────────────────────────────────────────────────────────────────


def tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase alphanumeric words, splitting snake_case and camelCase."""
    # Split camelCase: insert space before capital letters preceded by lowercase
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    raw_tokens = re.findall(r"[a-z_가-힣][a-z0-9_가-힣]*", expanded.lower())
    tokens: list[str] = []
    for tok in raw_tokens:
        tokens.append(tok)
        if "_" in tok:
            subparts = [p for p in tok.split("_") if p]
            tokens.extend(subparts)
            if len(subparts) > 1:
                tokens.append("".join(subparts))
    return tokens


def build_bm25(docs: dict[str, str]) -> dict[str, Any]:
    """Build BM25 index from {filepath: content} dictionary."""
    tokenized = {p: tokenize(c) for p, c in docs.items()}
    df: dict[str, int] = defaultdict(int)
    for tokens in tokenized.values():
        for t in set(tokens):
            df[t] += 1
    total_docs = len(docs)
    return {"tokenized": tokenized, "df": dict(df), "N": total_docs}


def bm25_score(
    query_tokens: list[str],
    doc_tokens: list[str],
    df: dict[str, int],
    total_docs: int,
    k1: float = 1.5,
    b: float = 0.75,
    avg_dl: float = 100.0,
) -> float:
    """Calculate BM25 relevance score between query and a document."""
    dl = len(doc_tokens)
    tf_map: dict[str, int] = defaultdict(int)
    for t in doc_tokens:
        tf_map[t] += 1

    score = 0.0
    for t in query_tokens:
        if t not in tf_map:
            continue
        tf = tf_map[t]
        doc_freq = df.get(t, 0)
        idf = math.log((total_docs - doc_freq + 0.5) / (doc_freq + 0.5) + 1.0)
        numerator = tf * (k1 + 1.0)
        denominator = tf + k1 * (1.0 - b + b * (dl / avg_dl))
        score += idf * (numerator / denominator)

    return score


def search(index: dict[str, Any], query: str, top_k: int = 5) -> list[tuple[str, float]]:
    """Search BM25 index and return top matching files and their scores."""
    q_tokens = tokenize(query)
    if not q_tokens:
        return []

    tokenized = index.get("tokenized", {})
    df = index.get("df", {})
    total_docs = index.get("N", len(tokenized))
    if total_docs == 0:
        return []

    avg_dl = sum(len(t) for t in tokenized.values()) / max(total_docs, 1)
    scores: list[tuple[str, float]] = []

    for path, tokens in tokenized.items():
        s = bm25_score(q_tokens, tokens, df, total_docs, avg_dl=avg_dl)
        if s > 0:
            scores.append((path, s))

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_k]


# ── Commands ──────────────────────────────────────────────────────────────────


def cmd_index(args: argparse.Namespace) -> int:
    """Index project directory and write .ctx-index.json."""
    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 1

    config = load_config(root, getattr(args, "config", None))
    out_file = args.output or config.get("index_file", DEFAULT_INDEX_FILE)
    out_path = root / out_file if not Path(out_file).is_absolute() else Path(out_file)

    supported_exts = set(config.get("supported_exts", DEFAULT_SUPPORTED_EXTS))
    ignore_dirs = set(config.get("ignore_dirs", DEFAULT_IGNORE_DIRS))
    ignore_files = config.get("ignore_files", list(DEFAULT_IGNORE_FILES))
    max_chars = config.get("max_chars_per_file", 3000)

    docs: dict[str, str] = {}
    candidates = [
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix in supported_exts
        and not any(part in ignore_dirs for part in p.parts)
        and not should_ignore_file(p.relative_to(root), ignore_files)
    ]

    print(f"Indexing {len(candidates)} files in {root}...")
    for f in candidates:
        rel = str(f.relative_to(root))
        docs[rel] = extract(f, max_chars=max_chars)

    index_data = build_bm25(docs)
    index_data["content"] = docs

    out_path.write_text(json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Index saved to {out_path} ({len(docs)} files)")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    """Search relevant context for a question and print/copy it."""
    root = Path.cwd().resolve()
    config = load_config(root, getattr(args, "config", None))

    idx_file = args.index or config.get("index_file", DEFAULT_INDEX_FILE)
    idx_path = Path(idx_file) if Path(idx_file).is_absolute() else root / idx_file

    if not idx_path.is_file():
        print(f"No index found at {idx_path}. Run: ctx index .", file=sys.stderr)
        return 1

    try:
        data = json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception as err:
        print(f"Failed to read index: {err}", file=sys.stderr)
        return 1

    top_k = args.top if args.top is not None else config.get("top_k", 5)
    results = search(data, args.question, top_k=top_k)

    if not results:
        print("No relevant files found for that query.")
        return 1

    lines: list[str] = [f"# Context for: {args.question}\n"]
    total_words = 0
    content_map = data.get("content", {})

    for path, score in results:
        content = content_map.get(path, "")
        lines.append(f"## {path} (relevance: {score:.2f})\n```\n{content[:1500]}\n```\n")
        total_words += len(content.split())

    output = "\n".join(lines)

    if args.copy:
        try:
            subprocess.run(["pbcopy"], input=output.encode("utf-8"), check=True)
            print(f"Copied context ({total_words} words, {len(results)} files) to clipboard.")
        except Exception:
            print(output)
    else:
        print(output)
        print(f"\n--- {len(results)} files, ~{total_words} words ---")

    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    """Show statistics of existing index."""
    root = Path.cwd().resolve()
    config = load_config(root, getattr(args, "config", None))

    idx_file = args.index or config.get("index_file", DEFAULT_INDEX_FILE)
    idx_path = Path(idx_file) if Path(idx_file).is_absolute() else root / idx_file

    if not idx_path.is_file():
        print(f"No index found at {idx_path}.", file=sys.stderr)
        return 1

    try:
        data = json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception as err:
        print(f"Failed to read index: {err}", file=sys.stderr)
        return 1

    n_files = len(data.get("content", {}))
    n_terms = len(data.get("df", {}))
    print(f"Files indexed : {n_files}")
    print(f"Unique terms  : {n_terms}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="ctx",
        description="Build local codebase context for AI queries. Nothing leaves your machine.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # index
    p_index = subparsers.add_parser("index", help="Index codebase directory")
    p_index.add_argument("directory", nargs="?", default=".", help="Directory to index")
    p_index.add_argument("-o", "--output", help="Index output path (default: .ctx-index.json)")
    p_index.add_argument("--config", help="Custom config path")
    p_index.set_defaults(func=cmd_index)

    # ask
    p_ask = subparsers.add_parser("ask", help="Ask a question and get relevant context")
    p_ask.add_argument("question", help="Question about the codebase")
    p_ask.add_argument("-i", "--index", help="Path to index file")
    p_ask.add_argument("-n", "--top", type=int, default=None, help="Top N relevant files")
    p_ask.add_argument("-c", "--copy", action="store_true", help="Copy context to clipboard")
    p_ask.add_argument("--config", help="Custom config path")
    p_ask.set_defaults(func=cmd_ask)

    # stats
    p_stats = subparsers.add_parser("stats", help="Show index statistics")
    p_stats.add_argument("-i", "--index", help="Path to index file")
    p_stats.add_argument("--config", help="Custom config path")
    p_stats.set_defaults(func=cmd_stats)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    exit_code = args.func(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
