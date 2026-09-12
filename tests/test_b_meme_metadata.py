"""이미지 생성 호출 없이 분류 저장 계약과 실패 복구를 검증한다."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/b-meme/scripts/save_metadata.py"


def annotation():
    return {
        "emotions": ["RESIGNATION"],
        "keywords": ["돈없음", "체념"],
        "expense_categories": [],
        "subject": "human",
        "panels": [
            {
                "expression": "담담한 표정",
                "pose": "얼굴 클로즈업",
                "captions": ["손에 구겨진 지폐 한장조차 없어요"],
            }
        ],
        "evidence": "담담한 얼굴과 돈이 없다는 자막",
        "uncertainties": [],
    }


def run_save(tmp_path, data, status="completed"):
    image = tmp_path / "001.png"
    image.write_bytes(b"image fixture")
    manifest = tmp_path / "manifest.json"
    if not manifest.exists():
        manifest.write_text(
            json.dumps(
                {"entries": [{"output_path": "001.png", "status": status, "prompt": "keep me"}]}
            )
        )
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps({"001.png": data}))
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(manifest),
            "--annotations",
            str(annotations),
        ],
        capture_output=True,
        text=True,
    )


def test_save_categories_and_repeat_without_losing_generation_record(tmp_path):
    result = run_save(tmp_path, annotation())
    assert result.returncode == 0, result.stderr
    sidecar = tmp_path / "001.metadata.json"
    first = json.loads(sidecar.read_text())
    assert first["classification"]["emotions"] == ["RESIGNATION"]
    assert first["classification"]["expense_categories"] == []
    assert first["asset_path"] == "001.png"
    assert first["is_active"] is False
    assert first["tag"] is None
    assert len(first["asset_sha256"]) == 64
    assert run_save(tmp_path, annotation()).returncode == 0
    saved = json.loads((tmp_path / "manifest.json").read_text())["entries"][0]
    assert saved["prompt"] == "keep me"
    assert saved["classification"] == first["classification"]
    assert saved["metadata_path"] == "001.metadata.json"


@pytest.mark.parametrize(
    "field,value",
    [
        ("emotions", ["SELF_MOCKERY"]),
        ("expense_categories", ["FOOD"]),
        ("keywords", ["a"] * 6),
        ("subject", "celebrity"),
        ("panels", []),
        ("evidence", ""),
        ("uncertainties", "none"),
    ],
)
def test_invalid_annotations_do_not_mutate_manifest(tmp_path, field, value):
    data = annotation()
    data[field] = value
    result = run_save(tmp_path, data)
    assert result.returncode == 1
    assert "메타데이터 저장 실패" in result.stderr
    assert not (tmp_path / "001.metadata.json").exists()
    saved = json.loads((tmp_path / "manifest.json").read_text())["entries"][0]
    assert "classification" not in saved


def test_uncertain_or_unclassified_results_require_review(tmp_path):
    data = annotation()
    data["emotions"] = []
    data["uncertainties"] = ["감정 판독 어려움"]
    assert run_save(tmp_path, data, status="needs_review").returncode == 0
    saved = json.loads((tmp_path / "001.metadata.json").read_text())
    assert saved["needs_review"] is True


def test_missing_image_rejected_before_writes(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"entries": [{"output_path": "missing/001.png", "status": "completed"}]})
    )
    result = run_save(tmp_path, annotation())
    assert result.returncode == 1
    assert "이미지 파일 없음" in result.stderr
    assert not (tmp_path / "001.metadata.json").exists()


def test_invalid_later_item_does_not_partially_save_valid_item(tmp_path):
    manifest = tmp_path / "manifest.json"
    original = json.dumps(
        {
            "entries": [
                {"output_path": "001.png", "status": "completed"},
                {"output_path": "002.png", "status": "completed"},
            ]
        }
    )
    manifest.write_text(original)
    for name in ["001.png", "002.png"]:
        (tmp_path / name).write_bytes(b"image fixture")
    invalid = annotation()
    invalid["emotions"] = ["WRONG"]
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps({"001.png": annotation(), "002.png": invalid}))
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(manifest),
            "--annotations",
            str(annotations),
        ],
        capture_output=True,
    )
    assert result.returncode == 1
    assert manifest.read_text() == original
    assert not list(tmp_path.glob("*.metadata.json"))


def test_same_stem_different_extensions_cannot_overwrite_sidecar(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "entries": [
                    {"output_path": "001.png", "status": "completed"},
                    {"output_path": "001.jpg", "status": "completed"},
                ]
            }
        )
    )
    for name in ["001.png", "001.jpg"]:
        (tmp_path / name).write_bytes(b"image fixture")
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps({"001.png": annotation(), "001.jpg": annotation()}))
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(manifest),
            "--annotations",
            str(annotations),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "메타데이터 경로 중복" in result.stderr
    assert not list(tmp_path.glob("*.metadata.json"))


def annotation_v2():
    return dict(
        annotation(),
        schema_version=2,
        description=" 담담한 얼굴이다. ",
        usage_context=" 돈이 없음을 체념할 때 ",
        tags=[" 체념 ", "체념", "돈 없음"],
    )


def test_v2_normalized_search_document_and_v1_preservation(tmp_path):
    import hashlib

    assert run_save(tmp_path, annotation()).returncode == 0
    original = (tmp_path / "001.metadata.json").read_bytes()
    old_entry = json.loads((tmp_path / "manifest.json").read_text())["entries"][0]
    result = run_save(tmp_path, annotation_v2())
    assert result.returncode == 0, result.stderr
    saved = json.loads((tmp_path / "001.metadata.v2.json").read_text())
    assert saved["description"] == "담담한 얼굴이다."
    assert saved["tags"] == ["체념", "돈 없음"]
    expected = (
        "description: 담담한 얼굴이다.\nusage_context: 돈이 없음을 체념할 때\n"
        "captions:\n손에 구겨진 지폐 한장조차 없어요\ntags: 체념, 돈 없음"
    )
    assert saved["search_text"] == expected
    assert saved["search_text_hash"] == hashlib.sha256(expected.encode()).hexdigest()
    assert saved["metadata_version"] == 1
    assert saved["search_text_version"] == 1
    assert saved["review_status"] == "pending"
    assert saved["publication_approved"] is False
    assert saved["allowed_verdict_tags"] == []
    assert saved["is_active"] is False
    assert (tmp_path / "001.metadata.json").read_bytes() == original
    entry = json.loads((tmp_path / "manifest.json").read_text())["entries"][0]
    assert all(entry[key] == value for key, value in old_entry.items())
    assert entry["metadata_v2_path"] == "001.metadata.v2.json"


@pytest.mark.parametrize(
    "field,value",
    [
        ("description", ""),
        ("description", " "),
        ("description", "가" * 1001),
        ("usage_context", "가" * 1001),
        ("usage_context", None),
        ("tags", ["x" * 41]),
        ("tags", [" "]),
        ("tags", list(map(str, range(21)))),
        ("tags", "tag"),
        ("tags", [1]),
        ("schema_version", True),
        ("schema_version", 3),
    ],
)
def test_v2_invalid_fields_leave_no_sidecar(tmp_path, field, value):
    data = annotation_v2()
    data[field] = value
    assert run_save(tmp_path, data).returncode == 1
    assert not (tmp_path / "001.metadata.v2.json").exists()


def test_v2_nfc_and_maximum_boundaries(tmp_path):
    import unicodedata

    data = annotation_v2()
    data.update(
        description="가" * 1000,
        usage_context="나" * 1000,
        tags=["한글", unicodedata.normalize("NFD", "한글"), "x" * 40] + list(map(str, range(18))),
    )
    assert run_save(tmp_path, data).returncode == 0
    saved = json.loads((tmp_path / "001.metadata.v2.json").read_text())
    assert len(saved["tags"]) == 20


def test_v2_versions_and_caption_order(tmp_path):
    data = annotation_v2()
    data["panels"][0]["captions"] = ["A", "A"]
    data["panels"].append(dict(expression="표정", pose="자세", captions=["B"]))
    assert run_save(tmp_path, data).returncode == 0
    path = tmp_path / "001.metadata.v2.json"
    first = json.loads(path.read_text())
    assert "captions:\nA\nA\nB\ntags:" in first["search_text"]
    assert run_save(tmp_path, data).returncode == 0
    assert json.loads(path.read_text()) == first
    data["evidence"] = "변경한 근거"
    assert run_save(tmp_path, data).returncode == 0
    second = json.loads(path.read_text())
    assert second["metadata_version"] == 2
    assert second["search_text_hash"] == first["search_text_hash"]
    data["description"] = "새 장면 설명"
    assert run_save(tmp_path, data).returncode == 0
    third = json.loads(path.read_text())
    assert third["metadata_version"] == 3
    assert third["search_text_hash"] != first["search_text_hash"]


def load_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location("save_metadata", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v2_manifest_io_failure_retry_keeps_version(tmp_path, monkeypatch):
    module = load_script()
    assert run_save(tmp_path, annotation_v2()).returncode == 0
    annotations = tmp_path / "annotations.json"
    data = annotation_v2()
    data["description"] = "새 설명"
    annotations.write_text(json.dumps({"001.png": data}))
    original = module.atomic_json
    manifest = tmp_path / "manifest.json"

    def fail_manifest(path, data):
        if path == manifest:
            raise OSError("simulated manifest failure")
        original(path, data)

    monkeypatch.setattr(module, "atomic_json", fail_manifest)
    with pytest.raises(OSError):
        module.save(manifest, annotations)
    sidecar = tmp_path / "001.metadata.v2.json"
    saved = sidecar.read_bytes()
    assert json.loads(saved)["metadata_version"] == 2
    monkeypatch.setattr(module, "atomic_json", original)
    module.save(manifest, annotations)
    assert sidecar.read_bytes() == saved


def test_v2_conversion_rejects_changed_image(tmp_path):
    module = load_script()
    assert run_save(tmp_path, annotation()).returncode == 0
    (tmp_path / "001.png").write_bytes(b"different image")
    (tmp_path / "annotations.json").write_text(json.dumps({"001.png": annotation_v2()}))
    with pytest.raises(ValueError, match="해시 불일치"):
        module.save(tmp_path / "manifest.json", tmp_path / "annotations.json")
    assert not (tmp_path / "001.metadata.v2.json").exists()


def test_v2_preserves_review_and_uncertainty(tmp_path):
    old = annotation()
    old["emotions"] = []
    old["uncertainties"] = ["불명확"]
    assert run_save(tmp_path, old).returncode == 0
    data = annotation_v2()
    assert run_save(tmp_path, data).returncode == 1
    data["uncertainties"] = ["불명확"]
    assert run_save(tmp_path, data).returncode == 0
    assert json.loads((tmp_path / "001.metadata.v2.json").read_text())["needs_review"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("metadata_version", True),
        ("search_text_version", 2),
        ("search_text", "corrupted"),
        ("asset_sha256", "wrong"),
        ("is_active", True),
        ("generated_at", None),
        ("generated_at", "invalid"),
        ("generated_at", "2026-09-11T12:00:00"),
        ("generated_by", "wrong"),
        ("tag", "APPROVED"),
        ("strategies", ["unknown"]),
        ("classification", []),
    ],
)
def test_corrupt_v2_is_not_overwritten(tmp_path, field, value):
    assert run_save(tmp_path, annotation_v2()).returncode == 0
    path = tmp_path / "001.metadata.v2.json"
    saved = json.loads(path.read_text())
    saved[field] = value
    path.write_text(json.dumps(saved))
    original = path.read_bytes()
    assert run_save(tmp_path, annotation_v2()).returncode == 1
    assert path.read_bytes() == original


def test_v2_later_invalid_item_does_not_write(tmp_path):
    module = load_script()
    manifest = tmp_path / "manifest.json"
    entries = [{"output_path": name, "status": "completed"} for name in ["001.png", "002.png"]]
    manifest.write_text(json.dumps({"entries": entries}))
    for entry in entries:
        (tmp_path / entry["output_path"]).write_bytes(b"image")
    invalid = annotation_v2()
    del invalid["description"]
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps({"001.png": annotation_v2(), "002.png": invalid}))
    with pytest.raises(ValueError):
        module.save(manifest, annotations)
    assert not list(tmp_path.glob("*.metadata.v2.json"))


def test_v2_sidecar_cannot_overwrite_annotations(tmp_path):
    module = load_script()
    run_save(tmp_path, annotation())
    annotations = tmp_path / "001.metadata.v2.json"
    annotations.write_text(json.dumps({"001.png": annotation_v2()}))
    original = annotations.read_bytes()
    with pytest.raises(ValueError, match="경로 충돌"):
        module.save(tmp_path / "manifest.json", annotations)
    assert annotations.read_bytes() == original
