"""승인한 로컬 1건 실측 전용. 프로세스 공통 예산 예약 후 실제 모델을 호출한다."""

import asyncio
import json
import math
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    import msvcrt

    def _lock(stream):
        # 파일 첫 바이트 하나를 잠근다. 같은 파일을 잠그는 다른 프로세스는 기다린다.
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        stream.seek(0)

    def _unlock(stream):
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(stream):
        fcntl.flock(stream, fcntl.LOCK_EX)

    def _unlock(stream):
        fcntl.flock(stream, fcntl.LOCK_UN)


from geoji_ai.adapters.backend_http import BackendHttp
from geoji_ai.adapters.llm_router import build_llm, price_for
from geoji_ai.adapters.postgres_call_ledger import PostgresCallLedger
from geoji_ai.adapters.postgres_jobs import PostgresJobs, make_engine
from geoji_ai.adapters.postgres_memory import PostgresMemory
from geoji_ai.adapters.postgres_preparation import PostgresPreparation
from geoji_ai.api.app import SUBMISSION_CAP_MICRO_USD, create_app
from geoji_ai.application.llm_gateway import LLMGateway
from geoji_ai.core.config import Settings, secret_value
from geoji_ai.domain.vendor_health import VendorHealth
from geoji_ai.ports.llm import LLMError
from geoji_ai.workers.main import Worker, _install_sigterm
from tests.local_e2e.support import localhost_url


def reserve_call(path: Path, *, vendor, model, role, messages, schema, output_tokens):
    if type(output_tokens) is not int or output_tokens <= 0:
        raise LLMError("BUDGET", message="출력 토큰 제한은 양의 정수여야 합니다.")
    price = price_for(model)
    if price is None or any(not math.isfinite(value) or value < 0 for value in price):
        raise LLMError("BUDGET", message="공식 가격을 확인하지 않은 모델입니다.")
    # UTF-8 바이트 수 + framing 여유로 입력을 보수적으로 예약한다. 실제 토큰은 원장에 별도 기록한다.
    input_bound = len(json.dumps([messages, schema], ensure_ascii=False).encode()) + 1024
    micro_usd = math.ceil(input_bound * price[0] + output_tokens * price[1])
    with path.open("r+") as stream:
        _lock(stream)
        try:
            _reserve_locked(stream, vendor, model, role, micro_usd)
        finally:
            _unlock(stream)


def _reserve_locked(stream, vendor, model, role, micro_usd):
    budget = json.load(stream)
    if (
        type(budget.get("max_calls")) is not int
        or budget["max_calls"] <= 0
        or type(budget.get("cap_micro_usd")) is not int
        or budget["cap_micro_usd"] <= 0
        or type(budget.get("reserved_micro_usd")) is not int
        or budget["reserved_micro_usd"] < 0
        or not isinstance(budget.get("calls"), list)
    ):
        raise LLMError("BUDGET", message="실측 예약 예산 상태가 올바르지 않습니다.")
    if (
        len(budget["calls"]) >= budget["max_calls"]
        or budget["reserved_micro_usd"] + micro_usd > budget["cap_micro_usd"]
    ):
        raise LLMError("BUDGET", message="승인된 호출 수 또는 보수적 예약 예산에 도달했습니다.")
    budget["reserved_micro_usd"] += micro_usd
    budget["calls"].append(
        {"vendor": vendor, "model": model, "role": role, "reserved_micro_usd": micro_usd}
    )
    stream.seek(0)
    json.dump(budget, stream, ensure_ascii=False)
    stream.truncate()
    stream.flush()
    os.fsync(stream.fileno())


class BoundedRouter:
    def __init__(self, settings):
        self.inner = build_llm(settings)
        if self.inner is None:
            raise ValueError("실측용 모델 키가 없습니다.")

    def route(self, role, model_override=None):
        return self.inner.route(role, model_override)

    async def structured_call(
        self, *, role, model_override=None, messages, schema, max_output_tokens, timeout_s
    ):
        vendor, model = self.route(role, model_override)
        reserve_call(
            Path(os.environ["GEOJI_LIVE_BUDGET"]),
            vendor=vendor,
            model=model,
            role=role,
            messages=messages,
            schema=schema,
            output_tokens=max_output_tokens,
        )
        return await self.inner.structured_call(
            role=role,
            model_override=model_override,
            messages=messages,
            schema=schema,
            max_output_tokens=max_output_tokens,
            timeout_s=timeout_s,
        )


def settings():
    value = Settings(_env_file=None)
    localhost_url(value.BACKEND_INTERNAL_URL)
    if not all(secret_value(value, key) for key in ("OPENAI_API_KEY", "XAI_API_KEY")):
        raise ValueError("OPENAI_API_KEY/XAI_API_KEY를 로컬 환경변수로 설정해야 합니다.")
    return value


def api():
    value = settings()
    app = create_app(value, intake_llm=None)
    engine = make_engine(secret_value(value, "DATABASE_URL"))
    app.state.intake_engine = engine
    app.state.intake_llm = LLMGateway(
        BoundedRouter(value),
        PostgresCallLedger(engine, cap_micro_usd=SUBMISSION_CAP_MICRO_USD),
        VendorHealth(),
        price_for,
    )
    return app


async def worker():
    value = settings()
    engine = make_engine(secret_value(value, "DATABASE_URL"))
    backend = BackendHttp(value.BACKEND_INTERNAL_URL, secret_value(value, "SERVICE_AUTH_TOKEN"))
    preparation = PostgresPreparation(engine)
    llm = LLMGateway(
        BoundedRouter(value),
        PostgresCallLedger(engine),
        VendorHealth(),
        price_for,
        stale_scopes=preparation.stale_scopes,
    )
    runtime = Worker(
        PostgresJobs(engine, lease_s=value.JOB_LEASE_SECONDS),
        value,
        engine=engine,
        backend=backend,
        reaper=True,
        memory=PostgresMemory(engine, value),
        preparation=preparation,
        llm=llm,
    )
    _install_sigterm(runtime)
    try:
        await runtime.run()
    finally:
        await backend.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(worker())
