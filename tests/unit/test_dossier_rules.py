"""방 규칙 적중(05 §3.6, 스펙 케이스 ⑪⑫⑬)."""

from __future__ import annotations

from geoji_ai.domain.dossier_rules import match_rule, rule_matcher
from geoji_ai.ports.backend import RoomRule


def test_taxi_category_matches_taxi_rule() -> None:
    """⑪ `교통/택시` 가 "택시 금지" 규칙에 매칭."""
    assert match_rule("교통/택시", "택시 금지") is True


def test_cafe_category_does_not_match_taxi_rule() -> None:
    """⑫ `카페/간식` 이 "택시 금지" 에 미매칭."""
    assert match_rule("카페/간식", "택시 금지") is False


def test_other_category_never_matches() -> None:
    """⑬ `기타` 미매칭."""
    assert match_rule("기타", "기타 지출 금지") is False


def test_any_token_matches() -> None:
    assert match_rule("카페/간식", "평일 간식은 하루 한 번") is True


def test_rule_matcher_adapter_reads_rule_text() -> None:
    rule = RoomRule(room_id="r1", rule_id="rule-1", version=1, text="택시 금지")
    assert rule_matcher("교통/택시", rule) is True
    assert rule_matcher("카페/간식", rule) is False
