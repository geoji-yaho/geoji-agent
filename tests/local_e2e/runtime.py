"""테스트 전용 조립: 실제 API·Worker·Postgres와 명시적 고정 모델.

운영 CLI·환경변수에는 fake 스위치를 추가하지 않는다. 네트워크 벤더는 생성하지 않는다.
"""

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from geoji_ai.adapters.backend_http import BackendHttp
from geoji_ai.adapters.fake_llm import FakeLLM, FakeScenario
from geoji_ai.adapters.postgres_call_ledger import PostgresCallLedger
from geoji_ai.adapters.postgres_jobs import PostgresJobs, make_engine
from geoji_ai.adapters.postgres_memory import PostgresMemory
from geoji_ai.adapters.postgres_preparation import PostgresPreparation
from geoji_ai.api.app import create_app
from geoji_ai.application.llm_gateway import LLMGateway
from geoji_ai.core.config import Settings, secret_value
from geoji_ai.domain.vendor_health import VendorHealth
from geoji_ai.ports.llm import Cost
from geoji_ai.workers.main import Worker, _install_sigterm
from tests.local_e2e.support import LocalIssuer, localhost_url

ROOT = Path(__file__).resolve().parents[2]


class LocalFixtureLLM(FakeLLM):
    """근거가 없는 사실을 만들지 않는 E2E 의견 문구. 품질 평가용이 아니다."""

    def __init__(self):
        super().__init__(
            outputs={
                "context": {
                    "facts": [],
                    "reason_analysis": {
                        "has_mitigation": False,
                        "mitigation_kind": None,
                        "injection_suspected": False,
                    },
                },
                "banter": {"candidates": []},
            }
        )

    def route(self, role, model_override=None):
        return "fake", "fake-local-e2e"

    async def structured_call(self, *, model_override=None, **kwargs):
        control = json.loads(Path(os.environ["GEOJI_E2E_CONTROL"]).read_text())
        self.scenario = FakeScenario.TIMEOUT if control.get("timeout") else FakeScenario.OK
        result = await super().structured_call(**kwargs)
        return replace(
            result, vendor="fake", model_id="fake-local-e2e", cost=Cost(micro_usd=0, source="usage")
        )

    def _output_for(self, role, schema):
        output = super()._output_for(role, schema)
        if role == "sentencing":
            output.update(
                sentencing_reason="배심원의 투표 결과에 따른 판결입니다.",
                evidence_labels=[],
                aggravating=[],
                mitigating=[],
            )
        if role == "writer":
            control = json.loads(Path(os.environ["GEOJI_E2E_CONTROL"]).read_text())
            output.update(
                headline="지갑이 잠시 쉬어 갈 시간",
                statement=[
                    {
                        "text": "다음 결제 전에는 한 번 더 생각해 봐요.",
                        "kind": "opinion",
                        "evidence_labels": [],
                    },
                ],
                banter_strategy="PREMISE_REJECTION",
                selected_candidate_id=None,
                meme_tag=control.get("tag", "GUILTY_LIGHT"),
                meme_hints={"emotion": "RESIGNATION", "keywords": ["실망", "체념"]},
            )
        return output


def settings():
    value = Settings(_env_file=None, OPENAI_API_KEY="", XAI_API_KEY="")
    localhost_url(value.BACKEND_INTERNAL_URL)
    return value


def api():
    value = settings()
    app = create_app(value, intake_llm=None)
    engine = make_engine(secret_value(value, "DATABASE_URL"))
    app.state.intake_engine = engine
    app.state.intake_llm = LLMGateway(
        LocalFixtureLLM(), PostgresCallLedger(engine), VendorHealth(), lambda _: (0.0, 0.0)
    )
    return app


def helper():
    directory = Path(os.environ["GEOJI_E2E_STATE"])
    issuer = LocalIssuer(directory, os.environ["GEOJI_E2E_ISSUER"])
    app = FastAPI()
    lost = set()

    @app.api_route("/internal/v1/{path:path}", methods=["GET", "POST"])
    async def backend_proxy(path: str, request: Request):
        body = await request.body()
        base = localhost_url(os.environ["GEOJI_E2E_REAL_BACKEND"])
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in {"host", "content-length", "connection"}
        }
        async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
            response = await client.request(
                request.method,
                base + "/internal/v1/" + path,
                content=body,
                headers=headers,
                params=request.query_params,
            )
        if path.endswith("/finalize"):
            verdict_id = path.split("/")[1]
            record = {
                "request": json.loads(body),
                "status": response.status_code,
                "response": response.json(),
            }
            (directory / f"finalize-{verdict_id}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2)
            )
            control = json.loads(Path(os.environ["GEOJI_E2E_CONTROL"]).read_text())
            if (
                control.get("lose_finalize_response")
                and verdict_id not in lost
                and response.status_code == 200
            ):
                lost.add(verdict_id)
                (directory / f"response-lost-{verdict_id}").touch()
                return Response(
                    '{"code":"BACKEND_UNAVAILABLE"}', status_code=503, media_type="application/json"
                )
        return Response(
            response.content,
            status_code=response.status_code,
            media_type=response.headers.get("content-type"),
        )

    @app.get("/.well-known/openid-configuration")
    def configuration():
        return {
            "issuer": issuer.issuer,
            "jwks_uri": issuer.issuer + "/jwks",
            "id_token_signing_alg_values_supported": ["RS256"],
            "subject_types_supported": ["public"],
            "response_types_supported": ["id_token"],
        }

    @app.get("/jwks")
    def jwks():
        return {"keys": [issuer.jwk]}

    catalog = json.loads((ROOT / "outputs/b-meme/catalog-v1.json").read_text())
    assets = {a["asset_key"]: ROOT / a["asset_path"] for a in catalog["assets"]}

    @app.get("/assets/{key}")
    def asset(key: str):
        if key not in assets:
            raise HTTPException(404)
        return FileResponse(
            assets[key],
            media_type="image/png",
            headers={"Access-Control-Allow-Origin": "http://localhost:3800"},
        )

    return app


async def worker():
    value = settings()
    engine = make_engine(secret_value(value, "DATABASE_URL"))
    backend = BackendHttp(value.BACKEND_INTERNAL_URL, secret_value(value, "SERVICE_AUTH_TOKEN"))
    preparation = PostgresPreparation(engine)
    llm = LLMGateway(
        LocalFixtureLLM(),
        PostgresCallLedger(engine),
        VendorHealth(),
        lambda _: (0.0, 0.0),
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
