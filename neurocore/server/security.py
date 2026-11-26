"""
Camada de segurança HTTP do NeuroCore: token de acesso opcional, limite de
requisições por IP, limite de tamanho do corpo e cabeçalhos de segurança.
"""

from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from neurocore.config import get_settings

API_PREFIX = "/api/v1"
PUBLIC_API_PATHS = {f"{API_PREFIX}/health"}
RATE_LIMITED_SUFFIXES = ("/chat", "/chat/stream", "/run")

CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self' https://openrouter.ai",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


def extract_token(request: Request) -> Optional[str]:
    header = request.headers.get("x-neurocore-token")
    if header:
        return header.strip()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # EventSource (SSE) não envia cabeçalhos personalizados.
    return request.query_params.get("token")


def token_is_valid(provided: Optional[str], expected: Optional[str]) -> bool:
    if not expected:
        return True
    if not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def client_ip(request: Request) -> str:
    settings = get_settings()
    if settings.PUBLIC_MODE:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0, max_keys: int = 10000):
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> tuple[bool, int]:
        if self.limit <= 0:
            return True, 0
        now = time.monotonic()
        if len(self._hits) > self.max_keys:
            self._hits.clear()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False, max(1, int(self.window - (now - hits[0])))
        hits.append(now)
        return True, 0


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.limiter = SlidingWindowRateLimiter(get_settings().RATE_LIMIT_PER_MINUTE)

    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        path = request.url.path

        # Corpo grande demais
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.MAX_REQUEST_BYTES:
            return self._secure(JSONResponse({"detail": "Requisição grande demais."}, status_code=413), path)

        if path.startswith(API_PREFIX) and request.method != "OPTIONS":
            if path not in PUBLIC_API_PATHS and not token_is_valid(extract_token(request), settings.ACCESS_TOKEN):
                return self._secure(
                    JSONResponse({"detail": "Token de acesso inválido ou ausente."}, status_code=401), path
                )
            if request.method == "POST" and path.endswith(RATE_LIMITED_SUFFIXES):
                allowed, retry_after = self.limiter.allow(client_ip(request))
                if not allowed:
                    resp = JSONResponse(
                        {"detail": "Muitas requisições. Aguarde um pouco e tente novamente."}, status_code=429
                    )
                    resp.headers["Retry-After"] = str(retry_after)
                    return self._secure(resp, path)

        response = await call_next(request)
        return self._secure(response, path)

    @staticmethod
    def _secure(response: Response, path: str) -> Response:
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if not path.startswith(("/docs", "/redoc")):
            headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        if path.startswith(API_PREFIX):
            headers.setdefault("Cache-Control", "no-store")
        return response
