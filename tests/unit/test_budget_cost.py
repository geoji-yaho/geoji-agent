"""돈 예산 계산(06 §3.2). 순수 함수만."""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import UTC, datetime, timedelta

import pytest

from geoji_ai.domain.budget import (
    CASE_CAP_MICRO_USD,
    KRW_PER_USD,
    NodeResultKey,
    budget_key_for_post,
    budget_key_for_submission,
    est_max_micro_usd,
    node_result_expires_at,
    request_hash,
    resolve_actual_micro_usd,
    ticks_to_micro_usd,
)


def test_cap_은_40원_상당_27586_micro_usd():
    assert KRW_PER_USD == 1450
    assert CASE_CAP_MICRO_USD == 27_586
    assert CASE_CAP_MICRO_USD == round(40 / 1450 * 1e6)


def test_est_max_는_올림한다():
    # 1000 × 0.2 = 200, 333 × 0.5 = 166.5 → 366.5 → 367
    assert est_max_micro_usd(1000, 333, 0.2, 0.5) == 367


def test_est_max_는_정수로_떨어지면_그대로():
    assert est_max_micro_usd(1000, 2000, 2, 8) == 2000 + 16_000


def test_est_max_는_float_오차로_한_칸_올리지_않는다():
    # 100 × 1.1 은 float 로 110.00000000000001 이다
    assert est_max_micro_usd(100, 0, 1.1, 0) == 110


def test_est_max_는_음수_token_을_거부한다():
    with pytest.raises(ValueError):
        est_max_micro_usd(-1, 0, 1, 1)


def test_ticks_는_내림한다():
    assert ticks_to_micro_usd(0) == 0
    assert ticks_to_micro_usd(9_999) == 0
    assert ticks_to_micro_usd(10_000) == 1
    assert ticks_to_micro_usd(19_999) == 1
    assert ticks_to_micro_usd(123_456_789) == 12_345


def test_actual_은_micro_usd_다음_ticks_다음_모름():
    assert resolve_actual_micro_usd(7, 999_999) == 7
    assert resolve_actual_micro_usd(None, 25_000) == 2
    assert resolve_actual_micro_usd(None, None) is None


def test_request_hash_는_키_순서와_무관하다():
    assert request_hash({"a": 1, "b": [1, 2]}) == request_hash({"b": [1, 2], "a": 1})


def test_request_hash_는_nfc_로_정규화한다():
    nfd = unicodedata.normalize("NFD", "택시비")
    nfc = unicodedata.normalize("NFC", "택시비")
    assert nfd != nfc
    assert request_hash({"item": nfd}) == request_hash({"item": nfc})


def test_request_hash_는_정해진_바이트의_sha256():
    expected = hashlib.sha256('{"a":"택시","b":1}'.encode()).hexdigest()
    assert request_hash({"b": 1, "a": "택시"}) == expected


def test_request_hash_는_배열_순서와_값에_민감하다():
    assert request_hash({"a": [1, 2]}) != request_hash({"a": [2, 1]})
    assert request_hash({"a": 1}) != request_hash({"a": 2})


def test_예산_키():
    assert budget_key_for_post("p-1") == "p-1"
    assert budget_key_for_submission("abc") == "submission:abc"


def test_node_result_key_versions_는_request_hash_를_뺀_4요소():
    key = NodeResultKey("h", "m", "pv", "guardrail-v2", [{"scope_key": "u", "epoch": 1}])
    assert key.versions() == {
        "model_id": "m",
        "prompt_version": "pv",
        "policy_version": "guardrail-v2",
        "privacy_versions": [{"scope_key": "u", "epoch": 1}],
    }


def test_node_result_보존은_24h():
    now = datetime(2026, 9, 14, tzinfo=UTC)
    assert node_result_expires_at(now) == now + timedelta(hours=24)
