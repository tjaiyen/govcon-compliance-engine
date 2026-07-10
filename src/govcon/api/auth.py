"""Real per-user JWT authentication — makes the audit-trail identity genuinely
*authenticated* instead of asserted (lifts the Security posture from B+ to A).

Env-gated and OFF by default. With no ``GOVCON_JWT_*`` configuration the app is
byte-for-byte unchanged: the actor is ASSERTED from the ``X-Govcon-User`` header
(the stated limitation). Configure exactly ONE signing source and every ``/api/*``
call must carry a valid bearer JWT; the audit actor becomes ``auth:<sub>`` (a
cryptographically verified identity) and the header is structurally ignored.

**Authentication and real-data are SEPARATE switches.** Turning auth on does NOT
enable real-data mode, certification, or system-of-record — the synthetic-only
gate (``GOVCON_DATA_MODE``) is independent and stays on. Auth only makes the
immutable, hash-chained audit trail's identity real, which is a prerequisite for
segregation of duties — not SoD itself, and not a DCAA adequacy claim.

Security posture (every item is load-bearing — see tests/test_api_auth.py):
  * signature verification is MANDATORY; ``alg:none`` / unsigned is rejected.
  * algorithm-confusion defense: the accepted algorithms are derived from the
    configured KEY TYPE, never from the token header — a symmetric secret only
    accepts HS*, an asymmetric key/JWKS only accepts RS*/ES*. The two can never
    coexist (the one-source rule), so an HS-signed token can't be replayed
    against an RS public key.
  * ``exp``/``nbf``/``iat`` honored with a small leeway; ``iss`` and ``aud`` are
    required and validated.
  * JWKS keys are fetched over HTTPS only and cached; an unknown ``kid`` or a
    fetch failure fails CLOSED (401), never open, never per-request refetch-storm.
  * fail CLOSED: any verification error → ``AuthError`` → 401; the coarse reason
    is logged, the token and claims never are.

Optional dependency: ``uv sync --extra auth`` (PyJWT). It is imported lazily and
only when auth is actually configured, so the core engine still installs and
tests without it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from govcon.core.identity import sanitize_actor_label

#: The three mutually-exclusive signing sources. Exactly one selects "auth on";
#: zero = off (the default); more than one is a refuse-to-boot misconfiguration.
_SECRET_ENV = "GOVCON_JWT_SECRET"       # symmetric HS* shared secret
_PUBLIC_KEY_ENV = "GOVCON_JWT_PUBLIC_KEY"  # static asymmetric PEM public key
_JWKS_URL_ENV = "GOVCON_JWT_JWKS_URL"   # IdP JWKS endpoint (RS*/ES*)

#: Accepted-algorithm families, keyed by source type. The list is chosen by the
#: server's key, NOT read from the untrusted token header — this is the
#: algorithm-confusion defense.
_HS_FAMILY = ("HS256", "HS384", "HS512")
_ASYM_FAMILY = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")


class AuthError(Exception):
    """Any token that fails verification. Carries only a coarse, token-free
    reason (``expired`` / ``bad_signature`` / …) — never the token or claims."""


@dataclass(frozen=True)
class VerifiedIdentity:
    """The result of a successful verification. ``actor`` is already sanitized
    and bounded, ready to attribute an immutable audit row to."""

    actor: str
    scopes: frozenset[str]
    sub: str | None
    email: str | None


def _configured_sources() -> list[str]:
    """Which signing sources are present in the environment (non-empty)."""
    return [
        name
        for name in (_SECRET_ENV, _PUBLIC_KEY_ENV, _JWKS_URL_ENV)
        if os.environ.get(name, "").strip()
    ]


def auth_is_configured() -> bool:
    """True when real JWT auth is switched on. Cheap (env-only) and dependency-
    free so ``explain_limitations()`` can branch on it without importing PyJWT."""
    return len(_configured_sources()) >= 1


@dataclass(frozen=True)
class AuthConfig:
    source_env: str
    algorithms: tuple[str, ...]
    issuer: str
    audience: str
    leeway_s: float = 60.0
    required_scope: str | None = None
    scope_claim: str = "scope"
    secret: str | None = None
    public_key: str | None = None
    jwks_url: str | None = None

    @staticmethod
    def from_env() -> AuthConfig | None:
        """Read the environment into a config, or ``None`` when auth is off.
        Raises ``RuntimeError`` on an ambiguous / incomplete configuration so a
        deployment fails loudly at boot rather than silently running open."""
        sources = _configured_sources()
        if not sources:
            return None
        if len(sources) > 1:
            raise RuntimeError(
                "ambiguous JWT auth config: set exactly one of "
                f"{_SECRET_ENV} / {_PUBLIC_KEY_ENV} / {_JWKS_URL_ENV}, not "
                f"{', '.join(sources)}"
            )
        source = sources[0]
        issuer = os.environ.get("GOVCON_JWT_ISSUER", "").strip()
        audience = os.environ.get("GOVCON_JWT_AUDIENCE", "").strip()
        if not issuer or not audience:
            raise RuntimeError(
                "JWT auth requires GOVCON_JWT_ISSUER and GOVCON_JWT_AUDIENCE "
                "(iss/aud are validated on every token — no wildcard)"
            )
        family = _HS_FAMILY if source == _SECRET_ENV else _ASYM_FAMILY
        override = os.environ.get("GOVCON_JWT_ALGS", "").strip()
        if override:
            algs = tuple(a.strip() for a in override.split(",") if a.strip())
            bad = [a for a in algs if a not in family]
            if bad:
                raise RuntimeError(
                    f"GOVCON_JWT_ALGS {bad} not allowed for this key type "
                    f"(allowed: {', '.join(family)}) — cross-family algorithms "
                    "are refused to prevent algorithm confusion"
                )
        else:
            # A sensible, single default per family; do not advertise the whole
            # family unless the deployment opts in via GOVCON_JWT_ALGS.
            algs = ("HS256",) if source == _SECRET_ENV else ("RS256", "ES256")
        try:
            leeway = float(os.environ.get("GOVCON_JWT_LEEWAY_S", "60"))
        except ValueError:
            leeway = 60.0
        required_scope = os.environ.get("GOVCON_JWT_REQUIRED_SCOPE", "").strip() or None
        scope_claim = os.environ.get("GOVCON_JWT_SCOPE_CLAIM", "scope").strip() or "scope"
        jwks_url = os.environ.get(_JWKS_URL_ENV, "").strip() or None
        if source == _JWKS_URL_ENV and not (jwks_url or "").lower().startswith("https://"):
            raise RuntimeError(f"{_JWKS_URL_ENV} must be an https:// URL")
        return AuthConfig(
            source_env=source,
            algorithms=algs,
            issuer=issuer,
            audience=audience,
            leeway_s=leeway,
            required_scope=required_scope,
            scope_claim=scope_claim,
            secret=os.environ.get(_SECRET_ENV) if source == _SECRET_ENV else None,
            public_key=os.environ.get(_PUBLIC_KEY_ENV) if source == _PUBLIC_KEY_ENV else None,
            jwks_url=jwks_url if source == _JWKS_URL_ENV else None,
        )


def _coarse_reason(exc: Exception) -> str:
    """Map a PyJWT exception to a short, token-free reason for the audit log."""
    name = type(exc).__name__
    mapping = {
        "ExpiredSignatureError": "expired",
        "ImmatureSignatureError": "not_yet_valid",
        "InvalidIssuerError": "bad_issuer",
        "InvalidAudienceError": "bad_audience",
        "InvalidAlgorithmError": "bad_algorithm",
        "InvalidSignatureError": "bad_signature",
        "DecodeError": "malformed",
        "MissingRequiredClaimError": "missing_claim",
    }
    return mapping.get(name, "invalid")


def _parse_scopes(raw) -> frozenset[str]:
    """OAuth scopes arrive as a space-delimited string or a JSON array."""
    if isinstance(raw, str):
        return frozenset(raw.split())
    if isinstance(raw, (list, tuple)):
        return frozenset(str(s) for s in raw)
    return frozenset()


@dataclass
class TokenVerifier:
    """Verifies bearer JWTs against a fixed configuration. Pure and testable —
    ``verify`` raises ``AuthError`` on any failure and never returns a partially
    trusted identity."""

    config: AuthConfig
    _jwks_client: object = field(default=None, repr=False)

    @property
    def required_scope(self) -> str | None:
        return self.config.required_scope

    def verify(self, token: str) -> VerifiedIdentity:
        import jwt  # lazy: only needed when auth is actually on

        try:
            if self._jwks_client is not None:
                key = self._jwks_client.get_signing_key_from_jwt(token).key
            elif self.config.public_key is not None:
                key = self.config.public_key
            else:
                key = self.config.secret
            claims = jwt.decode(
                token,
                key,
                algorithms=list(self.config.algorithms),
                issuer=self.config.issuer,
                audience=self.config.audience,
                leeway=self.config.leeway_s,
                options={"require": ["exp", "iss", "aud"], "verify_signature": True},
            )
        except AuthError:
            raise
        except Exception as exc:  # fail CLOSED — a parser bug can't open the gate
            raise AuthError(_coarse_reason(exc)) from exc
        principal = claims.get("sub") or claims.get("email")
        # Sanitize the principal, THEN prefix "auth:" — matching how the web:
        # actor is built (the ':' delimiter is ours, not untrusted input).
        safe = sanitize_actor_label(principal) if principal else None
        if not safe:
            raise AuthError("no_subject")
        actor = f"auth:{safe}"
        return VerifiedIdentity(
            actor=actor,
            scopes=_parse_scopes(claims.get(self.config.scope_claim)),
            sub=claims.get("sub"),
            email=claims.get("email"),
        )


def build_verifier() -> TokenVerifier | None:
    """The single entry point create_app uses. ``None`` when auth is off (no
    PyJWT import, no behavior change); a ready ``TokenVerifier`` when on."""
    config = AuthConfig.from_env()
    if config is None:
        return None
    try:
        import jwt  # noqa: F401 — presence check
    except ModuleNotFoundError as exc:  # pragma: no cover - deploy misconfig
        raise RuntimeError(
            "JWT auth is configured but PyJWT is not installed — "
            "run `uv sync --extra auth`"
        ) from exc
    jwks_client = None
    if config.jwks_url is not None:
        from jwt import PyJWKClient

        # cache the JWK set (default lifespan) AND per-kid signing keys, over
        # HTTPS only (validated in from_env); a fetch failure fails closed.
        jwks_client = PyJWKClient(config.jwks_url, cache_keys=True)
    return TokenVerifier(config=config, _jwks_client=jwks_client)
