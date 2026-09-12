"""Codex의 시각 분류를 검증해 이미지 옆 JSON과 생성 manifest에 저장한다.

표준 라이브러리만 사용한다. 이미지 해석은 호출한 Codex가 수행한다.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

TAXONOMY = json.loads(
    (Path(__file__).resolve().parents[1] / "references/taxonomy.json").read_text()
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def strings(value, field, allowed=None, limit=None):
    require(isinstance(value, list), f"{field}: 문자열 배열이어야 합니다")
    require(
        all(isinstance(v, str) and v.strip() for v in value), f"{field}: 빈 문자열이나 잘못된 항목"
    )
    require(len(value) == len(set(value)), f"{field}: 중복 항목")
    if allowed is not None:
        require(all(v in allowed for v in value), f"{field}: 허용하지 않은 분류값")
    if limit is not None:
        require(len(value) <= limit, f"{field}: 최대 {limit}개")


def validate(data):
    fields = {
        "emotions",
        "keywords",
        "expense_categories",
        "subject",
        "panels",
        "evidence",
        "uncertainties",
    }
    require(isinstance(data, dict) and set(data) == fields, "분류 필드 누락 또는 알 수 없는 필드")
    strings(data["emotions"], "emotions", TAXONOMY["emotions"])
    strings(data["keywords"], "keywords", limit=5)
    strings(data["expense_categories"], "expense_categories", TAXONOMY["expense_categories"])
    strings(data["uncertainties"], "uncertainties")
    require(data["subject"] in TAXONOMY["subjects"], "subject: 허용하지 않은 대상")
    require(isinstance(data["evidence"], str) and data["evidence"].strip(), "evidence: 근거 필수")
    require(isinstance(data["panels"], list) and data["panels"], "panels: 최소 한 컷 필요")
    for panel in data["panels"]:
        require(
            isinstance(panel, dict) and set(panel) == {"expression", "pose", "captions"},
            "panels: expression, pose, captions 필요",
        )
        for field in ("expression", "pose"):
            require(isinstance(panel[field], str) and panel[field].strip(), f"panels: {field} 필수")
        # 같은 자막이 한 컷에서 반복되는 것은 허용한다.
        require(
            isinstance(panel["captions"], list)
            and all(isinstance(v, str) and v.strip() for v in panel["captions"]),
            "panels: captions는 비어 있거나 문자열 배열이어야 합니다",
        )


def normalized_text(value, field, maximum):
    require(isinstance(value, str), f"{field}: 문자열이어야 합니다")
    value = unicodedata.normalize("NFC", value.strip())
    require(bool(value) and len(value) <= maximum, f"{field}: 1~{maximum}자 필요")
    return value


def parse_annotation(data):
    require(isinstance(data, dict), "분류는 객체여야 합니다")
    if "schema_version" not in data:
        validate(data)
        return data, None
    require(
        type(data["schema_version"]) is int and data["schema_version"] == 2,
        "schema_version: 지원하는 명시적 버전은 2입니다",
    )
    extras = {"schema_version", "description", "usage_context", "tags"}
    require(extras <= data.keys(), "v2 필드 누락")
    classification = {key: value for key, value in data.items() if key not in extras}
    validate(classification)
    require(isinstance(data["tags"], list), "tags: 문자열 배열이어야 합니다")
    tags = list(dict.fromkeys(normalized_text(tag, "tags", 40) for tag in data["tags"]))
    require(len(tags) <= 20, "tags: 최대 20개")
    return classification, {
        "description": normalized_text(data["description"], "description", 1000),
        "usage_context": normalized_text(data["usage_context"], "usage_context", 1000),
        "tags": tags,
    }


def build_search_text(classification, details):
    captions = [caption for panel in classification["panels"] for caption in panel["captions"]]
    return (
        f"description: {details['description']}\n"
        f"usage_context: {details['usage_context']}\ncaptions:\n"
        + "\n".join(captions)
        + "\ntags: "
        + ", ".join(details["tags"])
    )


def preserved_review(asset, asset_hash, classification):
    """v1 관찰이 다른 이미지로 이동하거나 검수 근거가 사라지는 것을 막는다."""
    source = asset.with_suffix(".metadata.json")
    if not source.exists():
        return False
    old = json.loads(source.read_text(encoding="utf-8"))
    require(
        isinstance(old, dict)
        and type(old.get("schema_version")) is int
        and old["schema_version"] == 1,
        "기존 v1 메타데이터가 손상되었습니다",
    )
    require(
        old.get("asset_sha256") == asset_hash,
        "기존 v1 이미지 해시 불일치: 실제 이미지를 다시 관찰하세요",
    )
    validate(old.get("classification"))
    previous = old["classification"]
    require(
        set(previous["uncertainties"]) <= set(classification["uncertainties"]),
        "v1 uncertainties를 보존하세요",
    )
    return bool(old.get("needs_review") or previous["uncertainties"] or not previous["emotions"])


def finish_v2(metadata, details, sidecar):
    metadata.update(details)
    metadata.update(
        schema_version=2,
        generated_by="b-meme/codex-visual-v2",
        review_status="pending",
        publication_approved=False,
        allowed_verdict_tags=[],
        search_text_version=1,
    )
    metadata["search_text"] = build_search_text(metadata["classification"], details)
    metadata["search_text_hash"] = hashlib.sha256(
        metadata["search_text"].encode("utf-8")
    ).hexdigest()
    metadata["metadata_version"] = 1
    if not sidecar.exists():
        return
    previous = json.loads(sidecar.read_text(encoding="utf-8"))
    require(
        isinstance(previous, dict) and set(previous) == set(metadata),
        "기존 v2 메타데이터가 손상되었습니다",
    )
    require(
        type(previous["metadata_version"]) is int and previous["metadata_version"] >= 1,
        "기존 metadata_version이 잘못되었습니다",
    )
    require(
        type(previous["schema_version"]) is int and previous["schema_version"] == 2,
        "기존 schema_version이 잘못되었습니다",
    )
    require(
        type(previous["search_text_version"]) is int and previous["search_text_version"] == 1,
        "기존 search_text_version이 잘못되었습니다",
    )
    require(isinstance(previous["generated_at"], str), "기존 작성 시각이 잘못되었습니다")
    timestamp = datetime.fromisoformat(previous["generated_at"])
    require(timestamp.utcoffset() is not None, "기존 작성 시각의 시간대가 없습니다")
    require(
        previous["generated_by"] == metadata["generated_by"]
        and previous["taxonomy_version"] == TAXONOMY["version"]
        and previous["asset_path"] == metadata["asset_path"]
        and previous["tag"] is None
        and previous["strategies"] == [],
        "기존 로컬 생성 정보가 잘못되었습니다",
    )
    validate(previous["classification"])
    old_classification, old_details = parse_annotation(
        dict(
            previous["classification"],
            schema_version=2,
            description=previous["description"],
            usage_context=previous["usage_context"],
            tags=previous["tags"],
        )
    )
    expected = build_search_text(old_classification, old_details)
    require(
        previous["search_text"] == expected
        and previous["search_text_hash"] == hashlib.sha256(expected.encode("utf-8")).hexdigest(),
        "기존 검색 문서가 손상되었습니다",
    )
    require(
        all(
            isinstance(previous[key], str)
            and len(previous[key]) == 64
            and all(c in "0123456789abcdef" for c in previous[key])
            for key in ("asset_sha256", "search_text_hash")
        ),
        "기존 해시가 잘못되었습니다",
    )
    require(
        type(previous["needs_review"]) is bool
        and previous["review_status"] == "pending"
        and previous["publication_approved"] is False
        and previous["is_active"] is False
        and previous["allowed_verdict_tags"] == [],
        "기존 로컬 검수 상태가 잘못되었습니다",
    )
    ignored = {"generated_at", "metadata_version"}
    changed = any(previous[key] != value for key, value in metadata.items() if key not in ignored)
    metadata["metadata_version"] = previous["metadata_version"] + int(changed)
    if not changed:
        metadata["generated_at"] = previous["generated_at"]


def atomic_json(path, data):
    """중단으로 기존 JSON이 잘리지 않도록 같은 디렉터리에서 교체한다."""
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as stream:
            temp_path = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def save(manifest_path, annotations_path):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    require(
        isinstance(manifest, dict) and isinstance(manifest.get("entries"), list),
        "manifest.entries 배열 필요",
    )
    require(isinstance(annotations, dict) and annotations, "분류할 annotations 객체 필요")
    outputs = {}
    sidecar_paths = set()
    for entry in manifest["entries"]:
        require(isinstance(entry, dict), "manifest 항목은 객체여야 합니다")
        if not entry.get("output_path"):
            continue
        path = Path(entry["output_path"])
        if not path.is_absolute():
            path = manifest_path.parent / path
        require(path.name not in outputs, "중복 출력 파일명: 고유 이름으로 저장하세요")
        sidecar_path = path.with_suffix(".metadata.json").resolve()
        require(
            sidecar_path not in sidecar_paths,
            "메타데이터 경로 중복: 이미지 stem을 고유하게 지정하세요",
        )
        sidecar_paths.add(sidecar_path)
        outputs[path.name] = (entry, path)

    writes = []
    for name, classification in annotations.items():
        require(name in outputs, f"manifest에 없는 출력: {name}")
        entry, asset = outputs[name]
        require(asset.is_file(), f"이미지 파일 없음: {asset}")
        require(
            entry.get("status") in ("completed", "needs_review"), "생성 결과 검수 후 분류하세요"
        )
        classification, details = parse_annotation(classification)
        sidecar = asset.with_suffix(
            ".metadata.v2.json" if details is not None else ".metadata.json"
        )
        require(
            sidecar.resolve() not in (manifest_path.resolve(), annotations_path.resolve()),
            "메타데이터 출력과 입력 JSON 경로 충돌",
        )
        needs_review = (
            entry["status"] != "completed"
            or not classification["emotions"]
            or bool(classification["uncertainties"])
        )
        metadata = {
            "schema_version": 1,
            "taxonomy_version": TAXONOMY["version"],
            "asset_path": asset.name,
            "asset_sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
            "classification": classification,
            "generated_by": "b-meme/codex-visual-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),  # noqa: UP017 -- 스킬은 Python 3.9+ 지원
            "needs_review": needs_review,
            # 이미지 분위기로 유무죄/전략을 확정하지 않는다. 등록 단계의 별도 결정이다.
            "tag": None,
            "strategies": [],
            "is_active": False,
        }
        if details is not None:
            metadata["needs_review"] |= preserved_review(
                asset, metadata["asset_sha256"], classification
            )
            finish_v2(metadata, details, sidecar)
            writes.append((sidecar, metadata))
            entry["metadata_v2_path"] = os.path.relpath(sidecar, manifest_path.parent)
            continue
        writes.append((sidecar, metadata))
        entry["classification"] = classification
        entry["metadata_path"] = os.path.relpath(sidecar, manifest_path.parent)
        entry["classification_needs_review"] = needs_review

    # 모든 항목 검증 후 쓰기 시작. 중간 I/O 실패 시 같은 명령으로 재실행 가능하다.
    for path, metadata in writes:
        atomic_json(path, metadata)
    atomic_json(manifest_path, manifest)
    return len(writes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    args = parser.parse_args()
    try:
        count = save(args.manifest.resolve(), args.annotations.resolve())
    except (OSError, ValueError, TypeError) as error:
        print(f"메타데이터 저장 실패: {error}", file=sys.stderr)
        return 1
    print(f"분류 저장 완료: {count}장")
    return 0


if __name__ == "__main__":
    sys.exit(main())
