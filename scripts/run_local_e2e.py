#!/usr/bin/env python3
"""독립 Docker DB + 실제 Spring/AI API/worker 공개 API E2E.

PYTHONPATH=src .venv/bin/python scripts/run_local_e2e.py --backend /path/to/server
유료 모델 호출은 없다. --keep은 브라우저 검증용 프로세스/DB를 남긴다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import asyncpg
import httpx
import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.local_e2e.support import LocalIssuer, expected_meme  # noqa: E402

USERS = [f"00000000-0000-4000-8000-{i:012d}" for i in range(1, 6)]


def write_json(path, value, *, private=False):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
    temporary.chmod(0o600 if private else 0o644)
    temporary.replace(path)


def failure(exc, env):
    message = (
        str(exc)
        if isinstance(exc, (AssertionError, TimeoutError, FileNotFoundError, RuntimeError))
        else type(exc).__name__
    )
    for key in ("DB_PASSWORD", "SERVICE_AUTH_TOKEN", "OPENAI_API_KEY", "XAI_API_KEY"):
        value = env.get(key)
        if value:
            message = message.replace(value, "[REDACTED]")
    return {"result": "FAIL", "error_type": type(exc).__name__, "error": message}


class Stack:
    live = False

    def __init__(self, args):
        self.args = args
        self.directory = Path(tempfile.mkdtemp(prefix="geoji-e2e-"))
        self.name = self.directory.name
        self.processes = []
        self.commands = {}
        self.db = None
        self.client = None
        self.container_started = False
        self.password = secrets.token_hex(16)
        self.token = secrets.token_hex(24)
        self.base = f"http://127.0.0.1:{args.backend_port}"
        self.helper_url = f"http://127.0.0.1:{args.issuer_port}"
        self.issuer = LocalIssuer(self.directory, self.helper_url)
        self.control = self.directory / "model-control.json"
        write_json(self.control, {})
        self.env = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT),
            "DATABASE_URL": f"postgresql+asyncpg://postgres:{self.password}@127.0.0.1:{args.db_port}/geoji_stack",
            "BACKEND_INTERNAL_URL": self.helper_url,
            "GEOJI_E2E_REAL_BACKEND": self.base,
            "SERVICE_AUTH_TOKEN": self.token,
            "GUARDRAIL_POLICY_VERSION": "guardrail-v2",
            "APP_ENV": "development",
            "OPENAI_API_KEY": "",
            "XAI_API_KEY": "",
            "ALERT_DISCORD_WEBHOOK_URL": "",
            "GEOJI_E2E_STATE": str(self.directory),
            "GEOJI_E2E_ISSUER": self.helper_url,
            "GEOJI_E2E_CONTROL": str(self.control),
            "DB_URL": f"jdbc:postgresql://127.0.0.1:{args.db_port}/geoji_stack",
            "DB_USERNAME": "postgres",
            "DB_PASSWORD": self.password,
            "AI_API_BASE_URL": f"http://127.0.0.1:{args.ai_port}",
            "SUPABASE_JWT_ISSUER_URI": self.helper_url,
            "GEOJI_MEDIA_ADMIN_IDS": USERS[0],
            "GEOJI_MEDIA_ROOT": str(self.directory / "media"),
            "GEOJI_MEDIA_FONT_PATH": "/System/Library/Fonts/AppleSDGothicNeo.ttc",
            "GEOJI_MEDIA_SOURCE_ORIGIN": self.helper_url,
        }
        self.report = {
            "scope": "real Spring + JWT + AI API/worker + isolated DB; fixture model only",
            "paid_model_calls": 0,
            "checks": [],
            "cases": [],
            "commits": {},
            "working_tree_dirty": {},
        }

    def check(self, name, passed, detail=None):
        self.report["checks"].append({"name": name, "passed": bool(passed), "detail": detail})
        assert passed, f"검증 실패: {name}: {detail}"

    def start(self, name, command, cwd=ROOT):
        self.commands[name] = (command, cwd)
        stream = (self.directory / f"{name}.log").open("ab")
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=self.env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        stream.close()
        self.processes.append((name, proc))
        return proc

    def stop_process(self, name):
        for index, (label, proc) in enumerate(self.processes):
            if label == name:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait(timeout=5)
                self.processes.pop(index)
                return

    async def wait_http(self, url, seconds=90):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            for name, proc in self.processes:
                if proc.poll() is not None:
                    raise RuntimeError(f"{name} 종료: {self.directory / (name + '.log')}")
            try:
                if (await self.client.get(url)).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.3)
        raise TimeoutError(f"서비스 대기 시간 초과: {url}")

    async def boot(self):
        for key, path in [
            ("ai", ROOT),
            ("backend", self.args.backend),
            ("frontend", self.args.frontend),
        ]:
            if path and path.exists():
                self.report["commits"][key] = subprocess.check_output(
                    ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
                ).strip()
                self.report["working_tree_dirty"][key] = bool(
                    subprocess.check_output(
                        ["git", "-C", str(path), "status", "--porcelain"], text=True
                    ).strip()
                )
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                self.name,
                "--label",
                "geoji.local-e2e=true",
                "-p",
                f"127.0.0.1:{self.args.db_port}:5432",
                "-e",
                f"POSTGRES_PASSWORD={self.password}",
                "-e",
                "POSTGRES_DB=geoji_stack",
                "--tmpfs",
                "/var/lib/postgresql/data",
                "postgres:16",
            ],
            check=True,
            capture_output=True,
        )
        self.container_started = True
        for _ in range(100):
            try:
                self.db = await asyncpg.connect(
                    host="127.0.0.1",
                    port=self.args.db_port,
                    user="postgres",
                    password=self.password,
                    database="geoji_stack",
                )
                break
            except (OSError, asyncpg.PostgresError):
                await asyncio.sleep(0.2)
        if self.db is None:
            raise TimeoutError("전용 Postgres 기동 실패")
        files = sorted((self.args.backend / "src/test/resources/db").glob("*.sql"))
        self.check("test schema exists", bool(files))
        for file in files:
            await self.db.execute(file.read_text())
        self.report["schema_files"] = [f.name for f in files]
        self.client = httpx.AsyncClient(timeout=15, trust_env=False)
        python = str(ROOT / ".venv/bin/python")
        self.start(
            "issuer",
            [
                python,
                "-m",
                "uvicorn",
                "tests.local_e2e.runtime:helper",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.args.issuer_port),
            ],
        )
        await self.wait_http(self.helper_url + "/jwks")
        self.start(
            "api",
            [
                python,
                "-m",
                "uvicorn",
                "tests.local_e2e.runtime:api",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.args.ai_port),
                "--http",
                "h11" if self.args.baseline else "auto",
            ],
        )
        await self.wait_http(self.env["AI_API_BASE_URL"] + "/health/live")
        self.start("worker", [python, "-m", "tests.local_e2e.runtime"])
        jar = self.args.jar or self.args.backend / "build/libs/backend-0.0.1-SNAPSHOT.jar"
        if not jar.is_file():
            raise FileNotFoundError(f"먼저 JDK25 ./gradlew bootJar 실행: {jar}")
        with jar.open("rb") as stream:
            self.report["backend_jar_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        self.start(
            "backend",
            [
                str(self.args.java_home / "bin/java"),
                "-jar",
                str(jar),
                "--server.address=127.0.0.1",
                f"--server.port={self.args.backend_port}",
                "--logging.level.org.hibernate.SQL=warn",
            ],
            cwd=self.args.backend,
        )
        await self.wait_http(self.base + "/actuator/health")
        self.save_state()

    def save_state(self):
        write_json(
            self.directory / "state.json",
            {
                "container": self.name,
                "pids": {name: proc.pid for name, proc in self.processes},
                "started": {
                    name: subprocess.check_output(
                        ["ps", "-p", str(proc.pid), "-o", "lstart="], text=True
                    ).strip()
                    for name, proc in self.processes
                    if proc.poll() is None
                },
                "directory": str(self.directory),
                "backend": self.base,
                "db_port": self.args.db_port,
                "db_password": self.password,
                "service_token": self.token,
                "tokens": {user: self.issuer.token(user) for user in USERS},
            },
            private=True,
        )

    async def request(self, method, path, user=USERS[0], expected=200, **kwargs):
        headers = kwargs.pop("headers", {})
        if user:
            headers["Authorization"] = "Bearer " + self.issuer.token(user)
        response = await self.client.request(method, self.base + path, headers=headers, **kwargs)
        if response.status_code != expected:
            raise AssertionError(
                f"{method} {path}: {response.status_code} != {expected}: {response.text[:1000]}"
            )
        return response

    async def setup_users(self):
        await self.request("GET", "/api/rooms", user=None, expected=401)
        for token in [
            self.issuer.token(USERS[0], expires_in=-600),
            self.issuer.token(USERS[0], issuer="http://wrong-issuer.invalid"),
        ]:
            await self.request(
                "GET",
                "/api/rooms",
                user=None,
                expected=401,
                headers={"Authorization": "Bearer " + token},
            )
        header, payload, signature = self.issuer.token(USERS[0]).split(".")
        signature = ("B" if signature[0] == "A" else "A") + signature[1:]
        await self.request(
            "GET",
            "/api/rooms",
            user=None,
            expected=401,
            headers={"Authorization": f"Bearer {header}.{payload}.{signature}"},
        )
        self.check("JWT missing/bad signature/expired/wrong issuer rejected", True)
        for i, user in enumerate(USERS):
            await self.request(
                "POST",
                "/api/me",
                user,
                expected=201,
                json={"nickname": f"로컬배심원{i}", "monthlyBudget": 500000},
            )
        room = (
            await self.request(
                "POST",
                "/api/rooms",
                expected=201,
                json={
                    "name": "로컬 E2E 법정",
                    "spiceLevel": "mild",
                    "voteDeadlineMinutes": 60,
                    "rules": [],
                },
            )
        ).json()
        self.room_id = room["id"]
        for user in USERS[1:4]:
            await self.request("POST", "/api/rooms/join/" + room["inviteCode"], user)
        self.report["room_id"] = self.room_id

    async def seed_catalog(self):
        catalog = json.loads((ROOT / "outputs/b-meme/catalog-v1.json").read_text())
        for asset in catalog["assets"]:
            raw = (ROOT / asset["asset_path"]).read_bytes()
            metadata = json.loads((ROOT / asset["metadata_path"]).read_text())
            self.check(
                "asset hash " + asset["asset_key"],
                hashlib.sha256(raw).hexdigest() == metadata["asset_sha256"],
            )
            classification = metadata["classification"]
            if not self.args.baseline:
                form = {
                    "tag": asset["tag"],
                    "strategies": ",".join(asset["strategies"]),
                    "emotions": ",".join(classification["emotions"]),
                    "keywords": ",".join(classification["keywords"]),
                }
                uploaded = (
                    await self.request(
                        "POST",
                        "/api/admin/memes",
                        expected=201,
                        data=form,
                        files={"file": ("asset.png", raw, "image/png")},
                    )
                ).json()
                duplicate = (
                    await self.request(
                        "POST",
                        "/api/admin/memes",
                        expected=201,
                        data=form,
                        files={"file": ("asset.png", raw, "image/png")},
                    )
                ).json()
                self.check("catalog upload dedupe " + asset["asset_key"], uploaded == duplicate)
                path = "/api/admin/memes/" + uploaded["id"]
                await self.request("PUT", path + "/active", expected=409, json={"active": True})
                reviewed = (
                    await self.request("POST", path + "/review", json={"approved": True})
                ).json()
                self.check("review alone not active " + asset["asset_key"], not reviewed["active"])
                await self.request("PUT", path + "/active", json={"active": True})
                served = await self.request("GET", uploaded["imageUrl"])
                self.check("registered image bytes " + asset["asset_key"], served.content == raw)
                continue
            await self.db.execute(
                """INSERT INTO meme_images(id,tag,strategies,emotions,keywords,image_url,is_active)
                VALUES($1,$2,$3,$4,$5,$6,true)""",
                uuid5(NAMESPACE_URL, asset["asset_key"]),
                asset["tag"],
                asset["strategies"],
                classification["emotions"],
                classification["keywords"],
                self.helper_url + "/assets/" + asset["asset_key"],
            )
        self.report["catalog_registration"] = (
            "independent DB seed + hash checked local asset server"
            if self.args.baseline
            else "real admin upload/review/activate APIs; all 10 original tags and hashes verified"
        )

    async def case(
        self, name, *, vote="guilty", tag="GUILTY_LIGHT", timeout=False, lose_response=False
    ):
        write_json(
            self.control, {"tag": tag, "timeout": timeout, "lose_finalize_response": lose_response}
        )
        body = {
            "postType": "considering" if vote in {"agree", "disagree"} else "spent",
            "amountKrw": 12000,
            "category": "교통/택시",
            "item": "택시",
            "reason": "늦잠을 자서 택시를 탔어요.",
            "roomIds": [self.room_id],
        }
        key = str(uuid4())
        started = time.monotonic()
        submit = (
            await self.request(
                "POST",
                "/api/post-submissions",
                expected=201,
                json=body,
                headers={"Idempotency-Key": key},
            )
        ).json()
        if submit["status"] == "NEEDS_INPUT":
            submit = (
                await self.request(
                    "POST",
                    f"/api/post-submissions/{submit['submissionId']}/complete",
                    json={**body, "action": "PROCEED", "revision": submit["revision"]},
                )
            ).json()
        self.check(name + " intake completed", submit["status"] == "COMPLETED", submit["status"])
        post = submit["postId"]
        if not self.args.baseline:
            replay = (
                await self.request(
                    "POST",
                    "/api/post-submissions",
                    expected=201,
                    json=body,
                    headers={"Idempotency-Key": key},
                )
            ).json()
            self.check(name + " submission response-loss replay", replay["postId"] == post)
            await self.request(
                "POST",
                "/api/post-submissions",
                expected=409,
                json={**body, "amountKrw": 13000},
                headers={"Idempotency-Key": key},
            )
        pending = (
            await self.request("GET", f"/api/posts/{post}/verdict?room_id={self.room_id}")
        ).json()
        self.check(name + " pending no verdict", pending["juryStatus"] is None)
        await self.request("GET", f"/api/posts/{post}/verdict", USERS[4], expected=404)
        await self.request(
            "POST",
            f"/api/posts/{post}/votes",
            expected=403,
            json={"verdict": vote, "reason": "작성자 투표 금지", "roomId": self.room_id},
        )
        for user in USERS[1:4]:
            await self.request(
                "POST",
                f"/api/posts/{post}/votes",
                user,
                expected=201,
                json={"verdict": vote, "reason": "한 번 더 생각해 봅시다.", "roomId": self.room_id},
            )
        await self.request(
            "POST",
            f"/api/posts/{post}/votes",
            USERS[1],
            expected=409,
            json={"verdict": vote, "reason": "중복", "roomId": self.room_id},
        )
        end = time.monotonic() + 65
        while time.monotonic() < end:
            verdict = (
                await self.request("GET", f"/api/posts/{post}/verdict?room_id={self.room_id}")
            ).json()
            if verdict["textStatus"] in {"AI_READY", "TEMPLATE_READY"}:
                break
            await asyncio.sleep(max(0.1, min(verdict["pollAfterMs"] / 1000, 1)))
        else:
            raise TimeoutError(f"{name} PREPARE30초+SENTENCE10초 초과")
        expected_statuses = (
            {"AI_READY", "TEMPLATE_READY"}
            if self.live
            else {"TEMPLATE_READY" if timeout else "AI_READY"}
        )
        self.check(name + " text status", verdict["textStatus"] in expected_statuses, verdict)
        row = await self.db.fetchrow("SELECT * FROM verdicts WHERE post_id=$1", UUID(post))
        texts = await self.db.fetch("SELECT * FROM verdict_texts WHERE verdict_id=$1", row["id"])
        self.check(
            name + " vote count",
            await self.db.fetchval("SELECT count(*) FROM votes WHERE post_id=$1", UUID(post)) == 3,
        )
        self.check(
            name + " saved version/text",
            row["text_version"] == verdict["textVersion"] and bool(texts),
        )
        if lose_response:
            self.check(
                name + " committed response loss injected",
                (self.directory / f"response-lost-{row['id']}").exists(),
            )
        if not timeout and verdict["textStatus"] == "AI_READY":
            record = json.loads((self.directory / f"finalize-{row['id']}.json").read_text())
            payload = record["request"]
            replay = await self.request(
                "POST",
                f"/internal/v1/verdicts/{row['id']}/finalize",
                user=None,
                json=payload,
                headers={
                    "Authorization": "Bearer " + self.token,
                    "X-Job-Id": payload["job_id"],
                    "X-Generation-Id": payload["generation_id"],
                    "X-Trace-Id": str(uuid4()),
                    "X-Request-Id": str(uuid4()),
                },
            )
            self.check(name + " finalize replay same commit", replay.json() == record["response"])
        card = (await self.request("GET", f"/api/posts/{post}/share-card")).json()
        self.check(
            name + " card excludes private fields",
            not {"reason", "amountKrw", "item", "sentencingReason", "source"} & card.keys(),
        )
        if not timeout and verdict["textStatus"] == "AI_READY":
            hints = {
                "meme_tag": tag,
                "strategy": "PREMISE_REJECTION",
                "emotion": "RESIGNATION",
                "keywords": ["실망", "체념"],
            }
            if self.live:
                draft = payload["draft"]
                meme_hints = draft.get("meme_hints") or {}
                hints = {
                    "meme_tag": draft["meme_tag"],
                    "strategy": draft["texts"][0]["banter_strategy"],
                    "emotion": meme_hints.get("emotion"),
                    "keywords": meme_hints.get("keywords", []),
                }
            candidates = [
                dict(r)
                for r in await self.db.fetch(
                    "SELECT id::text,tag,strategies,emotions,keywords,"
                    "is_active AS active FROM meme_images"
                )
            ]
            recent = await self.db.fetch(
                """SELECT v.meme_image_id::text AS id FROM verdicts v JOIN posts p ON p.id=v.post_id
                WHERE p.author_id=$1 AND v.id<>$2 AND v.meme_image_id IS NOT NULL
                ORDER BY v.confirmed_at DESC,v.id LIMIT 5""",
                UUID(USERS[0]),
                row["id"],
            )
            chosen = expected_meme(
                candidates,
                hints,
                [r["id"] for r in recent],
                post,
            )
            actual = str(row["meme_image_id"]) if row["meme_image_id"] else None
            self.check(
                name + " selector matches DB",
                actual == chosen,
                {"expected": chosen, "actual": actual},
            )
            self.check(
                name + " card/verdict image matches", card["meme"] == verdict["view"]["meme"]
            )
            again = (await self.request("GET", f"/api/posts/{post}/verdict")).json()
            self.check(
                name + " selected ID stable", again["view"]["meme"] == verdict["view"]["meme"]
            )
        if not self.args.baseline:
            schema = json.loads((ROOT / "contracts/verdict-view-v1.schema.json").read_text())
            jsonschema.validate(verdict, schema)
        result = {
            "name": name,
            "post_id": post,
            "submission_id": submit["submissionId"],
            "verdict": verdict,
            "share_card": card,
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
        self.report["cases"].append(result)
        write_json(self.directory / "report.json", self.report)
        return result

    async def card_png(self, case):
        post = case["post_id"]
        path = f"/api/posts/{post}/share-card"
        created = (await self.request("POST", path + "/render", expected=202)).json()
        for _ in range(120):
            artifact = (await self.request("GET", path + "/render")).json()
            if artifact["status"] == "READY":
                break
            if artifact["status"] == "FAILED":
                raise AssertionError(f"PNG 렌더 실패: {artifact}")
            await asyncio.sleep(0.25)
        else:
            raise TimeoutError("PNG 렌더 30초 초과")
        again = (await self.request("POST", path + "/render", expected=202)).json()
        self.check("PNG request idempotent " + post, created["id"] == again["id"])
        png = await self.request("GET", path + "/png")
        download = await self.request("GET", path + "/download")
        digest = hashlib.sha256(png.content).hexdigest()
        self.check(
            "same PNG download/hash " + post,
            png.content == download.content
            and digest == artifact["pngSha256"]
            and png.content.startswith(b"\x89PNG\r\n\x1a\n"),
        )
        self.check(
            "attachment/no-store " + post,
            "attachment" in download.headers["content-disposition"]
            and "no-store" in download.headers["cache-control"],
        )
        stored = self.directory / "media/blobs" / digest[:2] / digest
        self.check("server PNG file same hash " + post, stored.read_bytes() == png.content)
        (self.directory / f"card-{post}.png").write_bytes(png.content)
        await self.request("GET", path + "/png", user=None, expected=401)
        await self.request("GET", path + "/png", USERS[4], expected=404)
        await self.request(
            "PUT", path + "/sharing", USERS[1], expected=404, json={"enabled": False}
        )
        await self.request("PUT", path + "/sharing", json={"enabled": False})
        await self.request("GET", path + "/png", expected=404)
        await self.request("GET", path + "/download", expected=404)
        await self.request("PUT", path + "/sharing", json={"enabled": True})
        restored = await self.request("GET", path + "/png")
        self.check("sharing revoke and restore same bytes " + post, restored.content == png.content)
        case["png"] = artifact

    async def private_images(self):
        assets = json.loads((ROOT / "outputs/b-meme/catalog-v1.json").read_text())["assets"]
        raw = (ROOT / assets[0]["asset_path"]).read_bytes()
        await self.request("GET", "/api/admin/memes", USERS[1], expected=403)
        await self.request(
            "POST",
            "/api/images",
            USERS[1],
            expected=400,
            files={"file": ("fake.png", b"not a png", "image/png")},
        )
        await self.request(
            "POST",
            "/api/images",
            USERS[1],
            expected=400,
            files={"file": ("fake.jpg", raw, "image/jpeg")},
        )
        before = await self.db.fetchval("SELECT count(*) FROM meme_images")
        upload = (
            await self.request(
                "POST",
                "/api/images",
                USERS[1],
                expected=201,
                files={"file": ("private.png", raw, "image/png")},
            )
        ).json()
        duplicate = (
            await self.request(
                "POST",
                "/api/images",
                USERS[1],
                expected=201,
                files={"file": ("same.png", raw, "image/png")},
            )
        ).json()
        self.check(
            "private upload hash/dedupe",
            upload["id"] == duplicate["id"]
            and upload["sha256"] == hashlib.sha256(raw).hexdigest()
            and upload["status"] == "PRIVATE",
        )
        path = "/api/images/" + upload["id"]
        for suffix in ["", "/content", "/transform"]:
            await self.request("GET", path + suffix, USERS[2], expected=404)
        await self.request(
            "POST", path + "/transform", USERS[2], expected=404, json={"operation": "GRAYSCALE"}
        )
        initial = (
            await self.request(
                "POST", path + "/transform", USERS[1], expected=202, json={"operation": "GRAYSCALE"}
            )
        ).json()
        for _ in range(120):
            transformed = (await self.request("GET", path + "/transform", USERS[1])).json()
            if transformed["status"] == "READY":
                break
            if transformed["status"] == "FAILED":
                raise AssertionError(transformed)
            await asyncio.sleep(0.25)
        else:
            raise TimeoutError("무료 로컬 변환 30초 초과")
        again = (
            await self.request(
                "POST", path + "/transform", USERS[1], expected=202, json={"operation": "GRAYSCALE"}
            )
        ).json()
        result = await self.request("GET", path + "/transform/content", USERS[1])
        self.check(
            "local grayscale dedupe/hash",
            initial["id"] == again["id"]
            and hashlib.sha256(result.content).hexdigest() == transformed["sha256"],
        )
        reviewed = (
            await self.request(
                "POST", path + "/review", USERS[1], json={"approved": True, "version": "GRAYSCALE"}
            )
        ).json()
        self.check(
            "private review stays private",
            reviewed["reviewStatus"] == "APPROVED"
            and reviewed["status"] == "PRIVATE"
            and reviewed["selectedVersion"] == "GRAYSCALE"
            and before == await self.db.fetchval("SELECT count(*) FROM meme_images"),
        )
        self.report["private_image"] = {
            "original": upload,
            "transformation": transformed,
            "paid_calls": 0,
            "provider": "local grayscale",
        }

    async def restart_recovery(self, case):
        """파일 저장 직후 DB commit 전 죽은 상태를 주입하고 실제 Spring 프로세스를 재시작한다."""
        post = case["post_id"]
        path = f"/api/posts/{post}/share-card"
        before = (await self.request("GET", path + "/render")).json()
        llm_count = await self.db.fetchval("SELECT count(*) FROM ai.llm_calls")
        self.stop_process("backend")
        await self.db.execute(
            """UPDATE share_card_jobs SET status='RENDERING',png_sha256=NULL,
            lease_token=gen_random_uuid(),lease_until=now()-interval '1 second' WHERE id=$1""",
            UUID(before["id"]),
        )
        self.start("backend", *self.commands["backend"])
        await self.wait_http(self.base + "/actuator/health")
        self.save_state()
        for _ in range(120):
            after = (await self.request("GET", path + "/render")).json()
            if after["status"] == "READY":
                break
            await asyncio.sleep(0.25)
        else:
            raise TimeoutError("Spring 재시작 후 만료된 PNG lease 복구 실패")
        self.check(
            "restart reuses saved PNG after DB commit loss",
            after["pngSha256"] == before["pngSha256"],
        )
        self.check(
            "card recovery makes no model calls",
            llm_count == await self.db.fetchval("SELECT count(*) FROM ai.llm_calls"),
        )

    async def run(self):
        await self.boot()
        await self.setup_users()
        await self.seed_catalog()
        normal = await self.case("normal", lose_response=True)
        if not self.args.baseline:
            await self.card_png(normal)
            await self.private_images()
            await self.restart_recovery(normal)
        await self.case("no-candidate", vote="notGuilty", tag="NOT_GUILTY")
        await self.case("approved-no-candidate", vote="agree", tag="APPROVED")
        await self.case("model-timeout", timeout=True)
        write_json(self.control, {})
        deleted = await self.case("delete-after-verdict")
        if not self.args.baseline:
            await self.card_png(deleted)
        post = deleted["post_id"]
        await self.request("DELETE", f"/api/posts/{post}", expected=204)
        for suffix in ["verdict", "share-card"]:
            await self.request("GET", f"/api/posts/{post}/{suffix}", expected=404)
        self.check("deleted verdict and share card blocked", True)
        if not self.args.baseline:
            for suffix in ["png", "download", "render"]:
                await self.request("GET", f"/api/posts/{post}/share-card/{suffix}", expected=404)
        calls = [
            dict(row)
            for row in await self.db.fetch(
                "SELECT vendor,model_id,status,count(*) AS count FROM ai.llm_calls GROUP BY 1,2,3"
            )
        ]
        self.check(
            "fake calls recorded; no paid vendor calls",
            bool(calls) and all(row["vendor"] == "fake" for row in calls),
        )
        self.report["model_calls"] = calls
        self.report["jobs"] = [
            dict(row)
            for row in await self.db.fetch(
                "SELECT kind,status,last_error_code,count(*) AS count FROM ai.jobs GROUP BY 1,2,3"
            )
        ]
        self.report["result"] = "PASS"

    async def close(self):
        if self.client:
            await self.client.aclose()
        if self.db:
            await self.db.close()
        if self.args.keep:
            return
        for _, proc in reversed(self.processes):
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
        for _, proc in reversed(self.processes):
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
        if self.container_started:
            subprocess.run(["docker", "stop", self.name], capture_output=True, check=True)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--frontend", type=Path)
    parser.add_argument("--jar", type=Path)
    parser.add_argument(
        "--java-home", type=Path, default=Path("/private/tmp/geoji-e2e-jdk25/Contents/Home")
    )
    parser.add_argument("--db-port", type=int, default=55436)
    parser.add_argument("--backend-port", type=int, default=18080)
    parser.add_argument("--ai-port", type=int, default=18100)
    parser.add_argument("--issuer-port", type=int, default=18099)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument(
        "--baseline", action="store_true", help="수정 전 jar 비교(h11); 계약/신규 API 검사 제외"
    )
    args = parser.parse_args()
    stack = Stack(args)
    print(f"실행 자료: {stack.directory}", flush=True)
    try:
        await stack.run()
    except BaseException as exc:
        stack.report.update(failure(exc, getattr(stack, "env", {})))
    finally:
        write_json(stack.directory / "report.json", stack.report)
        await stack.close()
        print(f"결과: {stack.report.get('result')} — {stack.directory / 'report.json'}", flush=True)
    if stack.report.get("result") != "PASS":
        print(stack.report.get("error", "실행 실패"), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
