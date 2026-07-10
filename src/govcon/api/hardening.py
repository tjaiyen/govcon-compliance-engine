"""HTTP hardening for the workbench API (ops hardening — NOT the Phase-5
liability line).

All of it is env-gated and off/loose by default so the localhost dev experience
is unchanged; a deployment opts in. None of it makes the tool a certified
system or admits real data — that stays behind the excluded Phase-5 line. In
particular, the bearer gate is a shared-secret to stop the server being wide
open; it is explicitly NOT an identity provider (per-user auth is Phase 5).
"""

from __future__ import annotations

import hmac
import os
import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from govcon.core.identity import reset_actor, set_actor
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


#: /api/* paths that stay OPEN even when auth is on — a probe and the honesty
#: statement should always be readable (the whole point of a limitations page).
_PUBLIC_API_PATHS = frozenset({"/api/about"})


def _bearer_token(request: Request) -> str | None:
    """Extract a single well-formed ``Bearer <token>``. A missing, duplicated
    (Starlette comma-joins duplicate headers → >2 parts), or non-Bearer header
    yields ``None`` → 401."""
    parts = request.headers.get("authorization", "").split()
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1]:
        return parts[1]
    return None


def _refuse(request_id: str, status: int, error: str) -> JSONResponse:
    return JSONResponse(
        {"error": error}, status_code=status,
        headers={"X-Request-Id": request_id, **_SECURITY_HEADERS},
    )


def install(app: FastAPI, verifier=None) -> None:
    """Wire request-id + security headers + auth gate + optional CORS. Rate
    limiting is applied per-endpoint via the ask limiter.

    ``verifier`` (a ``TokenVerifier`` from ``govcon.api.auth``) enables real
    per-user JWT auth. When set, it is the authority for every gated ``/api/*``
    path and SUPERSEDES the coarse ``GOVCON_API_TOKEN`` shared-secret gate: a
    valid token sets the audit actor to a verified ``auth:<sub>``. When ``None``
    (the default), behavior is unchanged — the optional shared-secret gate, if
    ``GOVCON_API_TOKEN`` is set, still applies."""
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
        path = request.url.path
        gated = path.startswith("/api/") and path not in _PUBLIC_API_PATHS
        actor_token = None
        if gated and verifier is not None:
            # Real JWT auth: cryptographically verified identity is the sole
            # authority (the X-Govcon-User header is ignored — see app.py).
            from govcon.api.auth import AuthError

            token = _bearer_token(request)
            if token is None:
                return _refuse(request_id, 401, "unauthorized")
            try:
                identity = verifier.verify(token)
            except AuthError as exc:
                get_logger("govcon.api").warning(
                    "auth_rejected", request_id=request_id, path=path, reason=str(exc)
                )
                return _refuse(request_id, 401, "unauthorized")
            # Scope gate on the expensive AI route only (authenticated-but-
            # forbidden): read-only determinations stay usable to any valid user.
            if (
                verifier.required_scope
                and path == "/api/ask"
                and verifier.required_scope not in identity.scopes
            ):
                get_logger("govcon.api").warning(
                    "auth_forbidden", request_id=request_id, path=path
                )
                return _refuse(request_id, 403, "forbidden")
            actor_token = set_actor(identity.actor)
        elif gated and api_token:
            # Coarse shared-secret gate (NOT an IdP). Constant-time compare.
            supplied = request.headers.get("authorization", "")
            if not hmac.compare_digest(supplied, f"Bearer {api_token}"):
                get_logger("govcon.api").warning(
                    "auth_rejected", request_id=request_id, path=path
                )
                return _refuse(request_id, 401, "unauthorized")
        try:
            response = await call_next(request)
        finally:
            if actor_token is not None:
                reset_actor(actor_token)
        response.headers["X-Request-Id"] = request_id
        for k, v in _SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response


#: The /api/ask limiter (expensive endpoint). Tunable via env; generous default.
def make_ask_limiter() -> RateLimiter:
    limit = int(os.environ.get("GOVCON_AI_RATE_LIMIT", "30"))
    window = float(os.environ.get("GOVCON_AI_RATE_WINDOW_S", "60"))
    return RateLimiter(limit=limit, window_s=window)
