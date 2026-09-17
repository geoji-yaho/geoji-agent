"""실제 로컬 스택 실행기가 사용하는 격리·기대값·임시 JWT 도구."""

import base64
import json
import subprocess
import time
import zlib
from pathlib import Path
from urllib.parse import urlsplit


def localhost_url(value: str) -> str:
    url = urlsplit(value)
    if (
        url.scheme != "http"
        or url.hostname not in {"127.0.0.1", "localhost", "::1"}
        or url.username
        or url.password
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        raise ValueError("로컬 호스트 루트 HTTP URL만 사용할 수 있습니다.")
    return value.rstrip("/")


def expected_meme(candidates, hints, recent, post_id):
    """Java 점수 정본과 별도로 계산한 E2E 기대값(10 §11)."""
    eligible = [c for c in candidates if c["active"] and c["tag"] == hints["meme_tag"]]

    def order(candidate):
        score = 3 * (hints["strategy"] in (candidate["strategies"] or []))
        score += 2 * (hints["emotion"] in (candidate["emotions"] or []))
        score += len(set(hints["keywords"]) & set(candidate["keywords"] or []))
        score -= 5 * (candidate["id"] in recent)
        tie = zlib.crc32((post_id + candidate["id"]).encode())
        return -score, tie, candidate["id"]

    return min(eligible, key=order)["id"] if eligible else None


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


class LocalIssuer:
    """실행마다 생성한 RSA 키. 비밀 키·JWT는 보고서에 기록하지 않는다."""

    def __init__(self, directory: Path, issuer: str):
        self.issuer = localhost_url(issuer)
        self.key = directory / "issuer-private.pem"
        if not self.key.exists():
            self.key.touch(mode=0o600)
            subprocess.run(
                ["openssl", "genrsa", "-out", str(self.key), "2048"],
                check=True,
                capture_output=True,
            )
        modulus = (
            subprocess.check_output(
                ["openssl", "rsa", "-in", str(self.key), "-noout", "-modulus"],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
            .split("=", 1)[1]
        )
        self.jwk = {
            "kty": "RSA",
            "use": "sig",
            "kid": "local-e2e",
            "alg": "RS256",
            "n": b64url(bytes.fromhex(modulus)),
            "e": "AQAB",
        }

    def token(self, user_id: str, *, expires_in: int = 7200, issuer: str | None = None) -> str:
        header = b64url(json.dumps({"alg": "RS256", "kid": "local-e2e"}).encode())
        payload = b64url(
            json.dumps(
                {
                    "iss": issuer or self.issuer,
                    "sub": user_id,
                    "aud": "authenticated",
                    "iat": int(time.time()),
                    "exp": int(time.time()) + expires_in,
                    "role": "authenticated",
                }
            ).encode()
        )
        signing_input = f"{header}.{payload}".encode()
        signature = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(self.key)],
            input=signing_input,
            capture_output=True,
            check=True,
        ).stdout
        return f"{header}.{payload}.{b64url(signature)}"
