"""검수된 b-meme 자산을 로컬에서 검증하고 백엔드 등록 JSON으로 내보낸다.

네트워크, AWS, DB, 백엔드 API를 호출하지 않는다. S3 연결 전 카탈로그는
``image_url=null``, ``is_active=false``인 안전한 등록 전 상태만 허용한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import tempfile
from pathlib import Path
from typing import Any

ALLOWED_TAGS = {"GUILTY_HEAVY", "GUILTY_LIGHT", "NOT_GUILTY", "APPROVED", "REJECTED"}
ALLOWED_EMOTIONS = {
    "DISAPPROVAL",
    "ABSURD_SERIOUSNESS",
    "SMUG",
    "PITY",
    "CELEBRATION",
    "RESIGNATION",
}
CATALOG_FIELDS = {
    "asset_key",
    "asset_path",
    "metadata_path",
    "tag",
    "strategies",
    "suggested_object_key",
    "image_url",
    "is_active",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _nonempty_strings(value: object, field: str) -> list[str]:
    require(isinstance(value, list), f"{field}: 문자열 배열이어야 합니다")
    require(
        all(isinstance(item, str) and item.strip() for item in value),
        f"{field}: 빈 문자열이나 잘못된 항목",
    )
    require(len(value) == len(set(value)), f"{field}: 중복 항목")
    return value


def _resolve(root: Path, value: object, field: str) -> Path:
    require(isinstance(value, str) and value.strip(), f"{field}: 경로 문자열 필요")
    relative = Path(value)
    require(
        not relative.is_absolute() and ".." not in relative.parts,
        f"{field}: 저장소 상대 경로 필요",
    )
    require(
        len(relative.parts) >= 3 and relative.parts[:2] == ("outputs", "b-meme"),
        f"{field}: outputs/b-meme 아래 경로 필요",
    )
    resolved = (root / relative).resolve()
    require(resolved.is_relative_to(root.resolve()), f"{field}: 저장소 밖 경로")
    return resolved


def _png_dimensions(data: bytes) -> tuple[int, int]:
    require(
        len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR",
        "PNG 형식이 아닙니다",
    )
    width, height = struct.unpack(">II", data[16:24])
    require(width > 0 and height > 0, "PNG 크기가 잘못되었습니다")
    return width, height


def _metadata_name_matches(asset: Path, metadata_path: Path) -> bool:
    return metadata_path.name in {
        asset.with_suffix(".metadata.json").name,
        asset.with_suffix(".metadata.v2.json").name,
    }


def build(catalog_path: Path, root: Path) -> dict[str, Any]:
    """카탈로그와 실제 파일을 검증하고 결정적인 등록 레코드를 반환한다."""

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    require(
        isinstance(catalog, dict) and set(catalog) == {"schema_version", "assets"},
        "카탈로그 최상위 필드가 잘못되었습니다",
    )
    require(catalog["schema_version"] == 1, "catalog schema_version은 1이어야 합니다")
    require(isinstance(catalog["assets"], list) and catalog["assets"], "assets 배열이 필요합니다")

    records: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    seen_hashes: set[str] = set()
    seen_objects: set[str] = set()

    for item in catalog["assets"]:
        require(
            isinstance(item, dict) and set(item) == CATALOG_FIELDS,
            "카탈로그 항목 필드가 잘못되었습니다",
        )
        asset_key = item["asset_key"]
        require(isinstance(asset_key, str) and asset_key.strip(), "asset_key: 문자열 필요")
        require(asset_key not in seen_keys, f"중복 asset_key: {asset_key}")
        require(item["tag"] in ALLOWED_TAGS, f"허용하지 않은 tag: {item['tag']}")
        strategies = _nonempty_strings(item["strategies"], "strategies")
        require(item["image_url"] is None, "S3 연결 전 image_url은 null이어야 합니다")
        require(item["is_active"] is False, "등록 전 is_active는 false여야 합니다")
        require(
            isinstance(item["suggested_object_key"], str)
            and item["suggested_object_key"].strip(),
            "suggested_object_key: 문자열 필요",
        )
        require(
            item["suggested_object_key"] not in seen_objects,
            f"중복 S3 객체 키: {item['suggested_object_key']}",
        )

        asset_path_value = item["asset_path"]
        require(isinstance(asset_path_value, str), "asset_path: 경로 문자열 필요")
        require(
            "-v1" not in Path(asset_path_value).stem,
            "수정 전 중간본은 등록할 수 없습니다",
        )
        asset = _resolve(root, asset_path_value, "asset_path")
        metadata_path = _resolve(root, item["metadata_path"], "metadata_path")
        require(asset.suffix.lower() == ".png", "현재 등록 형식은 PNG만 지원합니다")
        require(_metadata_name_matches(asset, metadata_path), "이미지와 metadata stem이 다릅니다")

        data = asset.read_bytes()
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        require(isinstance(metadata, dict), "metadata는 JSON 객체여야 합니다")
        require(metadata.get("asset_path") == asset.name, "metadata asset_path 불일치")
        require(metadata.get("needs_review") is False, "검토 필요 이미지는 등록할 수 없습니다")
        require(metadata.get("tag") == item["tag"], "metadata tag 불일치")

        digest = hashlib.sha256(data).hexdigest()
        require(metadata.get("asset_sha256") == digest, f"{asset}: SHA-256 불일치")
        require(digest not in seen_hashes, f"중복 SHA-256: {digest}")
        width, height = _png_dimensions(data)

        classification = metadata.get("classification")
        require(isinstance(classification, dict), "metadata classification 객체 필요")
        emotions = _nonempty_strings(classification.get("emotions"), "emotions")
        require(set(emotions) <= ALLOWED_EMOTIONS, "허용하지 않은 감정")
        keywords = _nonempty_strings(classification.get("keywords"), "keywords")
        require(len(keywords) <= 5, "keywords: 최대 5개")

        records.append(
            {
                "asset_key": asset_key,
                "asset_path": item["asset_path"],
                "metadata_path": item["metadata_path"],
                "asset_sha256": digest,
                "byte_size": len(data),
                "mime_type": "image/png",
                "width": width,
                "height": height,
                "tag": item["tag"],
                "strategies": strategies,
                "emotions": emotions,
                "keywords": keywords,
                "suggested_object_key": item["suggested_object_key"],
                "image_url": item["image_url"],
                "is_active": item["is_active"],
            }
        )
        seen_keys.add(asset_key)
        seen_hashes.add(digest)
        seen_objects.add(item["suggested_object_key"])

    return {"schema_version": 1, "assets": records}


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    """중단 시 기존 JSON이 잘리지 않도록 같은 디렉터리에서 원자적으로 교체한다."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="b-meme 등록 카탈로그 검증")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = build(args.catalog.resolve(), args.root.resolve())
    if args.output is not None:
        atomic_json(args.output.resolve(), result)
    print(f"밈 등록 카탈로그 검증 완료: {len(result['assets'])}장")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
