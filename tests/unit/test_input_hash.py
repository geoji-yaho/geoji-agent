"""`domain.input_hash` 정규화(05 §3.2)와 D-27 부분 키(9/14, 05 §3.3)."""

from __future__ import annotations

import hashlib
import json
import unicodedata

from geoji_ai.domain.input_hash import banter_key, canonical_input, dossier_key, input_hash
from geoji_ai.domain.intensity import Intensity
from tests.unit.test_build_evidence import load_snapshot


def test_sha256_hex_이고_결정적이다():
    snapshot = load_snapshot()
    value = input_hash(snapshot)
    assert value == input_hash(load_snapshot())
    assert len(value) == 64 and value == value.lower()
    assert value == hashlib.sha256(canonical_input(snapshot)).hexdigest()


def test_키_정렬_공백없음_비ASCII_그대로():
    body = canonical_input(load_snapshot()).decode("utf-8")
    assert ": " not in body and ", " not in body
    assert "늦잠 자서 택시 탐" in body
    data = json.loads(body)
    assert list(data) == sorted(data)


def test_dict_키_순서가_달라도_같다():
    snapshot = load_snapshot()
    data = snapshot.model_dump(mode="json")
    reordered = dict(reversed(list(data.items())))
    assert input_hash(reordered) == input_hash(snapshot)


def test_NFC_NFD_는_같다():
    nfc = load_snapshot(reason=unicodedata.normalize("NFC", "늦잠 자서 택시 탐"))
    nfd = load_snapshot(reason=unicodedata.normalize("NFD", "늦잠 자서 택시 탐"))
    assert nfc.reason != nfd.reason
    assert input_hash(nfc) == input_hash(nfd)


def test_jury_는_hash_에_들어가지_않는다():
    assert input_hash(load_snapshot(jury=None)) == input_hash(load_snapshot())


def test_post_audience_room_privacy_intake_가_바뀌면_달라진다():
    base = load_snapshot()
    data = base.model_dump(mode="json")
    changed = [
        load_snapshot(reason="다른 사유"),
        load_snapshot(post_version=2),
        load_snapshot(audience={**data["audience"], "audience_version": 2}),
        load_snapshot(room_snapshots=[{**data["room_snapshots"][0], "intensity": "hell"}]),
        load_snapshot(
            privacy_versions=[
                {**data["privacy_versions"][0], "epoch": 9},
                *data["privacy_versions"][1:],
            ]
        ),
    ]
    for snapshot in changed:
        assert input_hash(snapshot) != input_hash(base)


# --- D-27 조서 키 ---------------------------------------------------------------------


def test_조서_키는_방_강도_epoch_audience_jury_에_불변():
    base = load_snapshot()
    data = base.model_dump(mode="json")
    same = [
        load_snapshot(room_snapshots=[{**data["room_snapshots"][0], "intensity": "hell"}]),
        load_snapshot(
            privacy_versions=[
                {**data["privacy_versions"][0], "epoch": 9},
                *data["privacy_versions"][1:],
            ]
        ),
        load_snapshot(audience={**data["audience"], "audience_version": 2}),
        load_snapshot(jury=None),
    ]
    for snapshot in same:
        assert dossier_key(snapshot) == dossier_key(base)
    assert len(dossier_key(base)) == 64


def test_조서_키는_규칙_버전_방_목록_게시물_필드_심문_결과에_민감():
    base = load_snapshot()
    data = base.model_dump(mode="json")
    room = data["room_snapshots"][0]
    changed = [
        load_snapshot(room_snapshots=[{**room, "rule_version": room["rule_version"] + 1}]),
        load_snapshot(
            room_snapshots=[room, {"room_id": "room-other", "intensity": "mild", "rule_version": 1}]
        ),
        load_snapshot(reason="다른 사유"),
        load_snapshot(item="버스"),
        load_snapshot(post_version=2),
        load_snapshot(amount_krw=13_000),
    ]
    for snapshot in changed:
        assert dossier_key(snapshot) != dossier_key(base)
    # 심문 결과는 dict 모양으로 바꿔 본다(계약 모델 검증과 무관하게 키만 본다).
    assert dossier_key({**data, "intake_result": {"kind": "opinion"}}) != dossier_key(data)


def test_조서_키는_방_순서에_불변이고_dict_와_모델이_같다():
    data = load_snapshot().model_dump(mode="json")
    rooms = [
        {"room_id": "room-a", "intensity": "spicy", "rule_version": 1},
        {"room_id": "room-b", "intensity": "hell", "rule_version": 3},
    ]
    forward = {**data, "room_snapshots": rooms}
    backward = {**data, "room_snapshots": list(reversed(rooms))}
    assert dossier_key(forward) == dossier_key(backward)
    assert dossier_key(load_snapshot()) == dossier_key(data)


# --- D-27 드립 키 ---------------------------------------------------------------------


def test_드립_키는_조서_강도_프롬프트_버전마다_다르고_결정적():
    base = banter_key("dossier-1", Intensity.spicy, "bundle-v1")
    assert base == banter_key("dossier-1", "spicy", "bundle-v1")
    assert len(base) == 64 and base == base.lower()
    assert base != banter_key("dossier-2", Intensity.spicy, "bundle-v1")
    assert base != banter_key("dossier-1", Intensity.hell, "bundle-v1")
    assert base != banter_key("dossier-1", Intensity.spicy, "bundle-v2")
