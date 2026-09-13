"""`domain.input_hash` 정규화(05 §3.2)."""

from __future__ import annotations

import hashlib
import json
import unicodedata

from geoji_ai.domain.input_hash import canonical_input, input_hash
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
