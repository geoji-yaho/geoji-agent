"""`geoji-ai ledger-sweep --older-than` 파서(08 §3.2·§4.2). DB 를 부르지 않는다."""

from __future__ import annotations

import argparse
from datetime import timedelta

import pytest

from geoji_ai.cli import _parser, parse_duration


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("24h", timedelta(hours=24)),
        ("30m", timedelta(minutes=30)),
        ("1h", timedelta(hours=1)),
        ("90m", timedelta(minutes=90)),
    ],
)
def test_h_m_단위를_timedelta_로_읽는다(value: str, expected: timedelta):
    assert parse_duration(value) == expected


@pytest.mark.parametrize("value", ["0h", "0m", "-1h", "24", "1d", "24H", "1.5h", "h", "", "24 h"])
def test_양의_정수_h_m_밖은_거부한다(value: str):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_duration(value)


def test_ledger_sweep_서브커맨드가_older_than_을_받는다():
    args = _parser().parse_args(["ledger-sweep", "--older-than", "24h"])
    assert args.command == "ledger-sweep"
    assert args.older_than == timedelta(hours=24)


@pytest.mark.parametrize(
    "argv",
    [
        ["ledger-sweep"],
        ["ledger-sweep", "--older-than", "0h"],
        ["ledger-sweep", "--older-than", "1d"],
    ],
)
def test_older_than_이_없거나_틀리면_argparse_가_거부한다(argv: list[str]):
    # 계획서 예시는 24h 뿐이라 기본값을 만들지 않고 필수로 둔다.
    with pytest.raises(SystemExit) as exc:
        _parser().parse_args(argv)
    assert exc.value.code == 2
