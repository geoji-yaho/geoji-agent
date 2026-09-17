"""b-meme 등록 카탈로그의 로컬 검증과 내보내기 테스트."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "build_meme_release.py"


def _load_script() -> ModuleType:
    name = "build_meme_release"
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


release = _load_script()


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def _png(width: int = 2, height: int = 3) -> bytes:
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    return (
        header + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict]:
    root = tmp_path
    asset = root / "outputs/b-meme/run/001-good.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(_png())
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    metadata = asset.with_suffix(".metadata.json")
    metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "taxonomy_version": "verdict-meme-v1",
                "asset_path": asset.name,
                "asset_sha256": digest,
                "classification": {
                    "emotions": ["DISAPPROVAL"],
                    "keywords": ["황당"],
                },
                "needs_review": False,
                "tag": "REJECTED",
            }
        ),
        encoding="utf-8",
    )
    catalog = {
        "schema_version": 1,
        "assets": [
            {
                "asset_key": "good",
                "asset_path": "outputs/b-meme/run/001-good.png",
                "metadata_path": "outputs/b-meme/run/001-good.metadata.json",
                "tag": "REJECTED",
                "strategies": [],
                "suggested_object_key": "memes/seed/good/v1/image.png",
                "image_url": None,
                "is_active": False,
            }
        ],
    }
    catalog_path = root / "outputs/b-meme/catalog-v1.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    return root, catalog_path, catalog


def _save_catalog(catalog_path: Path, catalog: dict) -> None:
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")


def test_build_validates_asset_and_derives_registration_record(tmp_path: Path) -> None:
    root, catalog_path, _ = _fixture(tmp_path)

    result = release.build(catalog_path, root)

    asset = root / "outputs/b-meme/run/001-good.png"
    assert result == {
        "schema_version": 1,
        "assets": [
            {
                "asset_key": "good",
                "asset_path": "outputs/b-meme/run/001-good.png",
                "metadata_path": "outputs/b-meme/run/001-good.metadata.json",
                "asset_sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
                "byte_size": len(_png()),
                "mime_type": "image/png",
                "width": 2,
                "height": 3,
                "tag": "REJECTED",
                "strategies": [],
                "emotions": ["DISAPPROVAL"],
                "keywords": ["황당"],
                "suggested_object_key": "memes/seed/good/v1/image.png",
                "image_url": None,
                "is_active": False,
            }
        ],
    }


def test_missing_asset_fails(tmp_path: Path) -> None:
    root, catalog_path, catalog = _fixture(tmp_path)
    (root / catalog["assets"][0]["asset_path"]).unlink()

    with pytest.raises(FileNotFoundError):
        release.build(catalog_path, root)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("tag", "UNKNOWN", "허용하지 않은 tag"),
        ("image_url", "https://example.test/not-uploaded.png", "image_url은 null"),
        ("is_active", True, "is_active는 false"),
    ],
)
def test_unapproved_registration_values_fail(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    root, catalog_path, catalog = _fixture(tmp_path)
    catalog["assets"][0][field] = value
    _save_catalog(catalog_path, catalog)

    with pytest.raises(ValueError, match=message):
        release.build(catalog_path, root)


def test_intermediate_v1_asset_fails_before_reading_file(tmp_path: Path) -> None:
    root, catalog_path, catalog = _fixture(tmp_path)
    catalog["assets"][0]["asset_path"] = "outputs/b-meme/run/005-result-v1.png"
    _save_catalog(catalog_path, catalog)

    with pytest.raises(ValueError, match="중간본"):
        release.build(catalog_path, root)


def test_duplicate_hash_fails(tmp_path: Path) -> None:
    root, catalog_path, catalog = _fixture(tmp_path)
    original = catalog["assets"][0]
    second_asset = root / "outputs/b-meme/run/002-copy.png"
    second_asset.write_bytes((root / original["asset_path"]).read_bytes())
    second_metadata = second_asset.with_suffix(".metadata.json")
    copied_metadata = json.loads((root / original["metadata_path"]).read_text(encoding="utf-8"))
    copied_metadata["asset_path"] = second_asset.name
    second_metadata.write_text(json.dumps(copied_metadata), encoding="utf-8")
    catalog["assets"].append(
        dict(
            original,
            asset_key="copy",
            asset_path=str(second_asset.relative_to(root)),
            metadata_path=str(second_metadata.relative_to(root)),
            suggested_object_key="memes/seed/copy/v1/image.png",
        )
    )
    _save_catalog(catalog_path, catalog)

    with pytest.raises(ValueError, match="중복 SHA-256"):
        release.build(catalog_path, root)


def test_metadata_hash_mismatch_fails(tmp_path: Path) -> None:
    root, catalog_path, catalog = _fixture(tmp_path)
    metadata_path = root / catalog["assets"][0]["metadata_path"]
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["asset_sha256"] = "0" * 64
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256 불일치"):
        release.build(catalog_path, root)


def test_metadata_tag_must_match_catalog(tmp_path: Path) -> None:
    root, catalog_path, catalog = _fixture(tmp_path)
    metadata_path = root / catalog["assets"][0]["metadata_path"]
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["tag"] = "GUILTY_LIGHT"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError, match="metadata tag 불일치"):
        release.build(catalog_path, root)


def test_repository_relative_paths_are_required(tmp_path: Path) -> None:
    root, catalog_path, catalog = _fixture(tmp_path)
    catalog["assets"][0]["asset_path"] = "/tmp/image.png"
    _save_catalog(catalog_path, catalog)

    with pytest.raises(ValueError, match="저장소 상대 경로"):
        release.build(catalog_path, root)


def test_unreviewed_metadata_fails(tmp_path: Path) -> None:
    root, catalog_path, catalog = _fixture(tmp_path)
    metadata_path = root / catalog["assets"][0]["metadata_path"]
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["needs_review"] = True
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError, match="검토 필요"):
        release.build(catalog_path, root)


def test_atomic_json_writes_utf8_json_with_final_newline(tmp_path: Path) -> None:
    output = tmp_path / "nested/registration.json"

    release.atomic_json(output, {"message": "검증 완료"})

    assert output.read_text(encoding="utf-8") == '{\n  "message": "검증 완료"\n}\n'


def test_cli_validates_and_writes_output(tmp_path: Path) -> None:
    root, catalog_path, _ = _fixture(tmp_path)
    output = tmp_path / "release/backend-registration.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--catalog",
            str(catalog_path),
            "--root",
            str(root),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == "밈 등록 카탈로그 검증 완료: 1장\n"
    assert json.loads(output.read_text(encoding="utf-8"))["assets"][0]["asset_key"] == "good"
