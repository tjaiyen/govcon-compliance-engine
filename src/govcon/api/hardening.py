"""HTTP hardening for the workbench API (ops hardening — NOT the Phase-5
liability line).

All of it is env-gated and off/loose by default so the localhost dev experience
is unchanged; a deployment opts in. None of it makes the tool a certified
system or admits real data — that stays behind the excluded Phase-5 line. In
particular, the bearer gate is a shared-secret to stop the server being wide
open; it is explicitly NOT an identity provider (per-user auth is Phase 5).
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from govcon.core.logging import get_logger

#: Response headers applied to every response — safe defaults, no external deps.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # The workbench is ONE self-contained, same-origin page: its JS and CSS are
    # inline and its fonts are data: URIs, with NO external resources. So a CSP
    # that blocks every external origin (default-src 'self') while allowing the
    # page's own inline script/style is both strict and correct — 'unsafe-inline'
    # here does not re-open the injected-data XSS surface (that is handled by
    # esc()/safeUrl in the page; CSP is defense-in-depth against EXTERNAL script).
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
}


class RateLimiter:
    """A tiny in-process sliding-window limiter keyed by client IP + scope.
    Bounds AI cost-DoS together with the per-request USD ceiling. Not a
    distributed limiter — one process, best-effort; a real deployment would
    put a proper limiter at the edge."""

    def __init__(self, limit: int, window_s: float):
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            dq = self._hits[key]
            cutoff = now - self.window_s
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self.limit:
                return False
            dq.append(now)
            return True


def _client_key(request: Request) -> str:
    client = request.client
    return client.host if client else "unknown"


def install(app: FastAPI) -> None:
    """Wire request-id + security headers + optional bearer gate + optional
    CORS. Rate limiting is applied per-endpoint via ``rate_limit_ask``."""
    api_token = os.environ.get("GOVCON_API_TOKEN")  # None → gate off (dev)
    cors_origins = [
        o.strip() for o in os.environ.get("GOVCON_CORS_ORIGINS", "").split(",") if o.strip()
    ]

    if cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def _harden(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        # Optional shared-secret gate for /api/* (NOT an IdP — a deploy switch).
        if api_token and request.url.path.startswith("/api/"):
            supplied = request.headers.get("authorization", "")
            if supplied != f"Bearer {api_token}":
                get_logger("govcon.api").warning(
                    "auth_rejected", request_id=request_id, path=request.url.path
                )
                return JSONResponse(
                    {"error": "unauthorized"}, status_code=401,
                    headers={"X-Request-Id": request_id, **_SECURITY_HEADERS},
                )
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        for k, v in _SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response


#: The /api/ask limiter (expensive endpoint). Tunable via env; generous default.
def make_ask_limiter() -> RateLimiter:
    limit = int(os.environ.get("GOVCON_AI_RATE_LIMIT", "30"))
    window = float(os.environ.get("GOVCON_AI_RATE_WINDOW_S", "60"))
    return RateLimiter(limit=limit, window_s=window)
