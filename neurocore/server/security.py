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
# Anexos são servidos por links com ids aleatórios de 128 bits (como um link
# privado); assim <img src> funciona mesmo com NEUROCORE_ACCESS_TOKEN ativo.
PUBLIC_GET_PREFIXES = (f"{API_PREFIX}/files/",)
UPLOAD_PATH = f"{API_PREFIX}/uploads"
RATE_LIMITED_SUFFIXES = ("/chat", "/chat/stream", "/run")

# KaTeX e highlight.js vêm do jsDelivr em versões fixas, com hash de
# integridade (SRI) no HTML: o navegador recusa qualquer arquivo alterado.
CDN = "https://cdn.jsdelivr.net"
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    f"script-src 'self' {CDN}",
    f"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com {CDN}",
    f"font-src 'self' https://fonts.gstatic.com {CDN}",
    "img-src 'self' data: blob:",
    "media-src 'self' blob:",
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
        settings = get_settings()
        self.limiter = SlidingWindowRateLimiter(settings.RATE_LIMIT_PER_MINUTE)
        self.upload_limiter = SlidingWindowRateLimiter(settings.UPLOAD_RATE_LIMIT_PER_MINUTE)

    def _rate_limited(self, limiter: SlidingWindowRateLimiter, request: Request, path: str) -> Optional[Response]:
        allowed, retry_after = limiter.allow(client_ip(request))
        if allowed:
            return None
        resp = JSONResponse({"detail": "Muitas requisições. Aguarde um pouco e tente novamente."}, status_code=429)
        resp.headers["Retry-After"] = str(retry_after)
        return self._secure(resp, path)

    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        path = request.url.path

        # Corpo grande demais (uploads têm um limite próprio, maior)
        max_bytes = settings.MAX_UPLOAD_BYTES if path == UPLOAD_PATH else settings.MAX_REQUEST_BYTES
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > max_bytes:
            return self._secure(JSONResponse({"detail": "Requisição grande demais."}, status_code=413), path)

        if path.startswith(API_PREFIX) and request.method != "OPTIONS":
            public = path in PUBLIC_API_PATHS or (request.method == "GET" and path.startswith(PUBLIC_GET_PREFIXES))
            if not public and not token_is_valid(extract_token(request), settings.ACCESS_TOKEN):
                return self._secure(
                    JSONResponse({"detail": "Token de acesso inválido ou ausente."}, status_code=401), path
                )
            if request.method == "POST" and path == UPLOAD_PATH:
                blocked = self._rate_limited(self.upload_limiter, request, path)
                if blocked:
                    return blocked
            elif request.method == "POST" and path.endswith(RATE_LIMITED_SUFFIXES):
                blocked = self._rate_limited(self.limiter, request, path)
                if blocked:
                    return blocked

        response = await call_next(request)
        return self._secure(response, path)

    @staticmethod
    def _secure(response: Response, path: str) -> Response:
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        # Microfone só para o próprio site (ditado por voz na caixa de mensagem).
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(self), geolocation=()")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if not path.startswith(("/docs", "/redoc")):
            headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        if path.startswith(API_PREFIX):
            headers.setdefault("Cache-Control", "no-store")
        return response
