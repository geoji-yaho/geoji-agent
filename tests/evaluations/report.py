"""회귀 판정과 리포트(06 §3.4). 순수 함수.

FAIL 조건:

- 자동 검사 위반 > 0
- judge 를 돌렸으면 축 평균이 기준(`judge.AXES`) 미달
- 기준선이 있으면 축 평균이 기준선 대비 −0.3 이하, headline 중복률이 +5%p 이상

judge 기준을 넘어도 자동 위반이 있으면 FAIL 이다(judge 만으로 통과 없음).
기준선 JSON 은 `bundle_version`·축 평균·위반 수·중복률만 담는다. 커밋하지 않는다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from tests.evaluations.checks import CHECK_NAMES, HEADLINE_DUP_RATE_MAX, CheckViolation
from tests.evaluations.judge import AXES

__all__ = [
    "AXIS_DROP_FAIL",
    "DUP_RATE_RISE_FAIL",
    "PolicyCheck",
    "RegressionVerdict",
    "ReportRow",
    "RunSummary",
    "judge_regression",
    "render_report",
]

#: 기준선 대비 축 평균 하락 한계(06 §3.4 "축 −0.3").
AXIS_DROP_FAIL = 0.3
#: 기준선 대비 headline 중복률 상승 한계(06 §3.4 "+5%p").
DUP_RATE_RISE_FAIL = 0.05
#: 부동소수 경계 보정. −0.3 이 −0.30000000000000004 로 계산돼도 같은 값으로 본다.
_EPS = 1e-9


@dataclass(frozen=True)
class RunSummary:
    bundle_version: str
    axes: dict[str, float]
    violations: int
    headline_dup_rate: float

    def to_baseline(self) -> dict[str, Any]:
        return {
            "bundle_version": self.bundle_version,
            "axes": dict(self.axes),
            "violations": self.violations,
            "headline_dup_rate": self.headline_dup_rate,
        }

    @classmethod
    def from_baseline(cls, data: Mapping[str, Any]) -> RunSummary:
        return cls(
            bundle_version=str(data["bundle_version"]),
            axes={str(k): float(v) for k, v in dict(data["axes"]).items()},
            violations=int(data["violations"]),
            headline_dup_rate=float(data["headline_dup_rate"]),
        )


@dataclass
class RegressionVerdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PolicyCheck:
    """`--policy` fixture 기대값(01 부록 A) 대조 결과."""

    policy: str
    mismatches: list[str]
    open_questions: list[str]

    @property
    def matched(self) -> bool:
        return not self.mismatches


@dataclass(frozen=True)
class ReportRow:
    case_id: str
    intensity: str
    source: str
    headline: str
    attack_angle: str
    banter_strategy: str


def judge_regression(current: RunSummary, baseline: RunSummary | None = None) -> RegressionVerdict:
    verdict = RegressionVerdict(passed=True)
    if current.violations > 0:
        verdict.reasons.append(f"자동 검사 위반 {current.violations}건")
    if not current.axes:
        verdict.notes.append("judge 생략 — 자동 검사만으로 판정했다")
    for axis in AXES:
        mean = current.axes.get(axis.key)
        if mean is not None and mean < axis.threshold - _EPS:
            verdict.reasons.append(f"judge {axis.label} {mean:.2f} < 기준 {axis.threshold}")
    if baseline is not None:
        if baseline.bundle_version == current.bundle_version:
            verdict.notes.append("기준선과 bundle_version 이 같다")
        for axis in AXES:
            base = baseline.axes.get(axis.key)
            if base is None:
                continue
            mean = current.axes.get(axis.key)
            if mean is None:
                verdict.notes.append(
                    f"{axis.label}: 이번 실행에 judge 가 없어 기준선과 비교하지 않았다"
                )
                continue
            delta = mean - base
            if delta <= -AXIS_DROP_FAIL + _EPS:
                verdict.reasons.append(
                    f"judge {axis.label} 기준선 대비 {delta:+.2f}(한계 −{AXIS_DROP_FAIL})"
                )
        rise = current.headline_dup_rate - baseline.headline_dup_rate
        if rise >= DUP_RATE_RISE_FAIL - _EPS:
            limit = DUP_RATE_RISE_FAIL * 100
            verdict.reasons.append(
                f"headline 중복률 기준선 대비 {rise * 100:+.1f}%p(한계 +{limit:.0f}%p)"
            )
    verdict.passed = not verdict.reasons
    return verdict


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_report(
    *,
    meta: Mapping[str, Any],
    summary: RunSummary,
    verdict: RegressionVerdict,
    violations: Sequence[CheckViolation],
    rows: Sequence[ReportRow],
    failures: Mapping[str, str],
    policy_check: PolicyCheck | None,
    baseline: RunSummary | None = None,
) -> str:
    lines = ["# 골든셋 회귀 리포트", ""]
    lines += [f"판정: **{'PASS' if verdict.passed else 'FAIL'}**", ""]
    lines += [f"- {reason}" for reason in verdict.reasons]
    lines += [f"- (참고) {note}" for note in verdict.notes]
    lines += ["", "## 실행", "", "| 항목 | 값 |", "|---|---|"]
    lines += [f"| {_cell(k)} | {_cell(v)} |" for k, v in meta.items()]

    lines += ["", "## 자동 검사", "", "| 검사 | 위반 |", "|---|---|"]
    counts = Counter(v.check for v in violations)
    lines += [f"| {name} | {counts.get(name, 0)} |" for name in CHECK_NAMES]
    lines += [
        "",
        f"headline 중복률 {summary.headline_dup_rate:.1%} (상한 {HEADLINE_DUP_RATE_MAX:.0%})"
        + (f", 기준선 {baseline.headline_dup_rate:.1%}" if baseline is not None else ""),
    ]

    lines += ["", "## judge", ""]
    if summary.axes:
        lines += ["| 축 | 평균 | 기준 | 기준선 |", "|---|---|---|---|"]
        for axis in AXES:
            base = baseline.axes.get(axis.key) if baseline is not None else None
            lines.append(
                f"| {axis.label} | {summary.axes.get(axis.key, float('nan')):.2f} | "
                f"≥ {axis.threshold} | {'' if base is None else f'{base:.2f}'} |"
            )
    else:
        lines.append("생략")

    lines += ["", "## 정책 fixture(01 부록 A)", ""]
    if policy_check is None:
        lines.append("생략")
    else:
        state = "일치" if policy_check.matched else "불일치"
        lines.append(f"`{policy_check.policy}`: {state}")
        lines += [f"- {m}" for m in policy_check.mismatches]
        lines += [f"- (열린 질문) {q}" for q in policy_check.open_questions]

    if failures:
        lines += ["", "## 생성 실패", "", "| 사건 | 코드 |", "|---|---|"]
        lines += [f"| {_cell(k)} | {_cell(v)} |" for k, v in failures.items()]

    if violations:
        lines += ["", "## 위반 목록", "", "| 검사 | 사건 | 강도 | 내용 |", "|---|---|---|---|"]
        lines += [
            f"| {v.check} | {_cell(v.case_id or '-')} | {_cell(v.intensity or '-')} "
            f"| {_cell(v.detail)} |"
            for v in violations
        ]

    lines += [
        "",
        "## 판결문",
        "",
        "| 사건 | 강도 | 출처 | 각도 | 전략 | headline |",
        "|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {_cell(r.case_id)} | {r.intensity} | {r.source} | {r.attack_angle} | "
        f"{r.banter_strategy} | {_cell(r.headline)} |"
        for r in rows
    ]
    lines.append("")
    return "\n".join(lines)
