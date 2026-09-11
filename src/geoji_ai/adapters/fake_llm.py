"""가짜 LLM 어댑터 (01 §3.6).

역할별 고정 출력은 **`contracts/fixtures/` 파일에서 읽는다**(코드에서 합성하지 않는다).
테스트는 이 어댑터만 부른다. 네트워크·키를 쓰지 않는다.

정한 것(계획서가 둘 중 하나를 고르라고만 한 자리):

- `REFUSAL` 은 예외가 아니라 `LLMResult(output=None, stop_reason="refusal")` 로 돌려준다.
  `stop_reason` 에 `"refusal"` 이 있는 이상 정상 반환 경로가 있어야 하고, `LLMError(kind="REFUSAL")`
  는 실제 어댑터가 벤더 오류를 분류할 때 쓴다(작업 6).
- `SCHEMA_MISMATCH` 는 `LLMError(kind="SCHEMA")` 를 올린다. 스키마 강제는 어댑터 책임이라
  `PARSE_ERROR`(`kind="PARSE"`)와 같은 층에서 끝낸다.
- `INTENSITY_FAIL` 은 서기(`role="writer"`) 호출 중 요청 강도가 `fail_intensity`(기본 `hell`)일 때만
  `LLMError(kind="SCHEMA")` 를 올린다. 다른 강도·다른 역할은 정상 출력이다.
- `RATE_LIMIT` 의 `retry_after_s` 는 가짜 값 `RATE_LIMIT_RETRY_AFTER_S` 다(계획서에 값 없음).
- 비용은 모른다 — `Cost(ticks=None, micro_usd=None, source="unknown")`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from geoji_ai.ports.llm import Cost, LLMError, LLMResult, LLMRole, Usage

#: 저장소에서 `uv run` 할 때의 기본 fixture 위치. 설치본(휠·이미지)에는 `contracts/` 가
#: 들어가지 않으므로 그때는 `FakeLLM(fixtures_dir=...)` 로 넘긴다.
FIXTURES_DIR = Path(__file__).resolve().parents[3] / "contracts" / "fixtures"

#: 역할 → 고정 출력 fixture 파일 이름
FIXTURE_BY_ROLE: dict[str, str] = {
    "intake": "intake-taxi-pass",
    "context": "context-taxi",
    "banter": "banter-taxi",
    "sentencing": "sentencing-taxi",
    "writer": "writer-draft-taxi",
    "evaluator": "evaluation-taxi-pass",
}

RATE_LIMIT_RETRY_AFTER_S = 1.0
FAKE_VENDOR = "fake"
FAKE_MODEL_ID = "fake-model"


class FakeScenario(StrEnum):
    OK = "OK"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    REFUSAL = "REFUSAL"
    TRUNCATED = "TRUNCATED"
    PARSE_ERROR = "PARSE_ERROR"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    INTENSITY_FAIL = "INTENSITY_FAIL"


@dataclass
class FakeCall:
    """호출 기록. 테스트가 횟수·순서·인자를 단언한다."""

    role: str
    schema: dict
    messages: list[dict]
    timeout_s: float
    max_output_tokens: int
    scenario: FakeScenario


def load_role_fixture(role: str, fixtures_dir: Path | None = None) -> dict:
    """역할의 고정 출력 fixture 를 읽는다. 합성하지 않는다."""
    try:
        name = FIXTURE_BY_ROLE[role]
    except KeyError:
        raise ValueError(f"알 수 없는 역할: {role}") from None
    path = (fixtures_dir or FIXTURES_DIR) / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _properties(schema: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(schema, Mapping):
        return {}
    props = schema.get("properties")
    return dict(props) if isinstance(props, Mapping) else {}


def _enum_of(schema: Mapping[str, Any] | None, field_name: str) -> list[Any] | None:
    spec = _properties(schema).get(field_name)
    if isinstance(spec, Mapping) and isinstance(spec.get("enum"), list):
        return list(spec["enum"])
    return None


def _texts_intensity_enum(schema: Mapping[str, Any] | None) -> list[Any] | None:
    """검수관 strict 스키마의 `texts[].intensity` enum."""
    texts = _properties(schema).get("texts")
    if not isinstance(texts, Mapping):
        return None
    items = texts.get("items")
    if not isinstance(items, Mapping):
        return None
    return _enum_of(items, "intensity")


def _fits_enum(schema: Mapping[str, Any] | None) -> list[Any] | None:
    """드립 후보 strict 스키마의 `candidates[].fits[]` enum."""
    candidates = _properties(schema).get("candidates")
    if not isinstance(candidates, Mapping):
        return None
    items = candidates.get("items")
    if not isinstance(items, Mapping):
        return None
    fits = _properties(items).get("fits")
    if not isinstance(fits, Mapping):
        return None
    inner = fits.get("items")
    if isinstance(inner, Mapping) and isinstance(inner.get("enum"), list):
        return list(inner["enum"])
    return None


def _shape_to_schema(output: dict, schema: Mapping[str, Any] | None) -> dict:
    """strict 스키마(`additionalProperties: false`)면 선언 안 된 최상위 키를 떨어뜨리고,
    enum 이 박힌 최상위 스칼라는 그 값으로 맞춘다(서버가 고정하는 `mode`·`intensity`).
    """
    props = _properties(schema)
    shaped = dict(output)
    if props and isinstance(schema, Mapping) and schema.get("additionalProperties") is False:
        shaped = {key: value for key, value in shaped.items() if key in props}
    for key, value in list(shaped.items()):
        allowed = _enum_of(schema, key)
        if allowed and not isinstance(value, list | dict) and value not in allowed:
            shaped[key] = allowed[0]
    return shaped


def _rough_tokens(text: str) -> int:
    """가짜 토큰 수. 실제 토크나이저가 아니다."""
    return len(text) // 4


class FakeLLM:
    """`LLMPort` 구현. 고정 출력 + 시나리오 주입 + 호출 기록."""

    def __init__(
        self,
        scenario: FakeScenario = FakeScenario.OK,
        *,
        fail_intensity: str | None = "hell",
        violation_codes: Sequence[str] = (),
        latency_ms: int = 0,
        outputs: Mapping[str, dict] | None = None,
        fixtures_dir: Path | None = None,
    ) -> None:
        self.scenario = FakeScenario(scenario)
        self.fail_intensity = fail_intensity
        self.violation_codes = tuple(violation_codes)
        self.latency_ms = latency_ms
        self.outputs = dict(outputs) if outputs else {}
        self.fixtures_dir = fixtures_dir
        self.calls: list[FakeCall] = []

    # 호출 -------------------------------------------------------------

    async def structured_call(
        self,
        *,
        role: LLMRole,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        max_output_tokens: int,
    ) -> LLMResult:
        self.calls.append(
            FakeCall(
                role=role,
                schema=schema,
                messages=messages,
                timeout_s=timeout_s,
                max_output_tokens=max_output_tokens,
                scenario=self.scenario,
            )
        )
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000)

        match self.scenario:
            case FakeScenario.TIMEOUT:
                raise LLMError("TIMEOUT", message=f"fake timeout after {timeout_s}s")
            case FakeScenario.RATE_LIMIT:
                raise LLMError(
                    "RATE_LIMIT",
                    retry_after_s=RATE_LIMIT_RETRY_AFTER_S,
                    message="429 Too Many Requests",
                )
            case FakeScenario.PARSE_ERROR:
                raise LLMError("PARSE", message="fake invalid json")
            case FakeScenario.SCHEMA_MISMATCH:
                raise LLMError("SCHEMA", message="fake schema mismatch")
            case FakeScenario.REFUSAL:
                return self._result(None, "refusal", messages)
            case FakeScenario.TRUNCATED:
                return self._result(None, "max_tokens", messages)
            case FakeScenario.INTENSITY_FAIL:
                if role == "writer" and self._requested_intensity(schema) == self.fail_intensity:
                    raise LLMError("SCHEMA", message=f"fake {self.fail_intensity} failure")

        return self._result(self._output_for(role, schema), "stop", messages)

    # 출력 -------------------------------------------------------------

    def _output_for(self, role: str, schema: dict) -> dict:
        if role in self.outputs:
            return dict(self.outputs[role])
        fixture = load_role_fixture(role, self.fixtures_dir)
        match role:
            case "writer":
                output = self._writer_output(fixture, schema)
            case "banter":
                output = self._banter_output(fixture, schema)
            case "evaluator":
                output = self._evaluator_output(fixture, schema)
            case _:
                output = fixture
        return _shape_to_schema(output, schema)

    def _requested_intensity(self, schema: Mapping[str, Any] | None) -> str | None:
        allowed = _enum_of(schema, "intensity")
        return str(allowed[0]) if allowed else None

    def _writer_output(self, fixture: dict, schema: dict) -> dict:
        """`writer-draft-taxi.json`(WriterDraft) 에서 요청 강도의 `TextDraft` 하나를 꺼낸다."""
        texts = fixture.get("texts") or []
        wanted = self._requested_intensity(schema)
        chosen = None
        for text in texts:
            if wanted is None or text.get("intensity") == wanted:
                chosen = text
                break
        if chosen is None:
            raise ValueError(f"fixture 에 강도 {wanted} 의 TextDraft 가 없다")
        return {
            **chosen,
            "meme_tag": fixture.get("meme_tag"),
            "meme_hints": fixture.get("meme_hints"),
        }

    def _banter_output(self, fixture: dict, schema: dict) -> dict:
        """`banter-taxi.json` 후보를 strict 스키마의 `fits` 형태로 옮긴다."""
        allowed = _fits_enum(schema)
        candidates = [
            {
                "text": candidate.get("text"),
                "strategy": candidate.get("strategy"),
                "fits": [candidate["intensity"]] if candidate.get("intensity") else [],
                "evidence_labels": list(candidate.get("evidence_labels") or []),
            }
            for candidate in fixture.get("candidates") or []
            if not allowed or candidate.get("intensity") in allowed
        ]
        if not candidates:
            # 요청 강도에 맞는 후보가 fixture 에 없으면 전부 그 강도에 맞춘다.
            candidates = [
                {
                    "text": candidate.get("text"),
                    "strategy": candidate.get("strategy"),
                    "fits": list(allowed or []),
                    "evidence_labels": list(candidate.get("evidence_labels") or []),
                }
                for candidate in fixture.get("candidates") or []
            ]
        return {"candidates": candidates}

    def _evaluator_output(self, fixture: dict, schema: dict) -> dict:
        """`texts` 를 요청 강도 집합에 맞추고, `violation_codes` 를 주입한다."""
        report = dict(fixture)
        wanted = _texts_intensity_enum(schema)
        by_intensity = {text.get("intensity"): text for text in report.get("texts") or []}
        if wanted:
            texts = []
            for intensity in wanted:
                entry = by_intensity.get(intensity)
                if entry is None:
                    raise ValueError(f"fixture 에 강도 {intensity} 의 검수 결과가 없다")
                texts.append(dict(entry))
        else:
            texts = [dict(text) for text in report.get("texts") or []]

        if self.violation_codes:
            for index, entry in enumerate(texts):
                entry["pass"] = False
                entry["violations"] = [
                    {
                        "code": code,
                        "path": f"texts[{index}].statement[0].text",
                        "evidence_labels": [],
                        "explanation": "fake 위반 주입",
                    }
                    for code in self.violation_codes
                ]
        report["texts"] = texts
        return report

    # 결과 -------------------------------------------------------------

    def _result(self, output: dict | None, stop_reason: str, messages: list[dict]) -> LLMResult:
        prompt = "".join(str(message.get("content", "")) for message in messages)
        body = json.dumps(output, ensure_ascii=False) if output is not None else ""
        return LLMResult(
            output=output,
            stop_reason=stop_reason,  # type: ignore[arg-type]
            usage=Usage(
                prompt_tokens=_rough_tokens(prompt),
                completion_tokens=_rough_tokens(body),
            ),
            cost=Cost(),
            provider_request_id=f"fake-{len(self.calls)}",
            model_id=FAKE_MODEL_ID,
            vendor=FAKE_VENDOR,
            latency_ms=self.latency_ms,
        )
