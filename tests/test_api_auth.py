"""Real per-user JWT authentication (Security B+ → A).

Two layers:
  * unit — TokenVerifier.verify() against the full 401 matrix (alg confusion,
    alg:none, expired, iss/aud, tampered, malformed, JWKS fail-closed, RS256).
  * integration — a live app: a verified token sets an authenticated audit
    actor, the X-Govcon-User header is structurally ignored, unauthenticated
    /api/* is refused, and auth is INDEPENDENT of the synthetic-data gate.

Every expected 401/403/200 is pre-registered in the assertion (B35). Skipped
whole unless the `auth` extra (PyJWT) is installed.
"""

from __future__ import annotations

import base64
import json

import pytest

pytest.importorskip("jwt")  # skip the module without the auth extra

from fastapi.testclient import TestClient  # noqa: E402

from govcon.api import create_app  # noqa: E402
from govcon.api.auth import AuthConfig, AuthError, TokenVerifier  # noqa: E402
from tests import auth_helpers as idp  # noqa: E402

SECRET = "unit-test-secret-value-not-a-real-key"


# ----------------------------------------------------------------- unit: verify
def _hs_verifier(secret=SECRET, required_scope=None):
    cfg = AuthConfig(
        source_env="GOVCON_JWT_SECRET",
        algorithms=("HS256",),
        issuer=idp.ISSUER,
        audience=idp.AUDIENCE,
        secret=secret,
        required_scope=required_scope,
    )
    return TokenVerifier(config=cfg)


def _rs_verifier(public_pem, jwks_client=None):
    cfg = AuthConfig(
        source_env="GOVCON_JWT_PUBLIC_KEY" if jwks_client is None else "GOVCON_JWT_JWKS_URL",
        algorithms=("RS256", "ES256"),
        issuer=idp.ISSUER,
        audience=idp.AUDIENCE,
        public_key=public_pem if jwks_client is None else None,
    )
    return TokenVerifier(config=cfg, _jwks_client=jwks_client)


def test_valid_hs256_yields_authenticated_actor():
    ident = _hs_verifier().verify(idp.mint_hs256(SECRET, sub="analyst-7"))
    assert ident.actor == "auth:analyst-7" and ident.sub == "analyst-7"


def test_expired_token_rejected():
    # well past the 60s clock-skew leeway
    with pytest.raises(AuthError):  # pre-registered: expired → AuthError
        _hs_verifier().verify(idp.mint_hs256(SECRET, exp_offset=-3600))


def test_not_yet_valid_token_rejected():
    with pytest.raises(AuthError):  # nbf in the future (beyond leeway)
        _hs_verifier().verify(idp.mint_hs256(SECRET, nbf_offset=3600))


def test_wrong_issuer_rejected():
    with pytest.raises(AuthError):
        _hs_verifier().verify(idp.mint_hs256(SECRET, iss="https://evil.example/"))


def test_wrong_audience_rejected():
    with pytest.raises(AuthError):
        _hs_verifier().verify(idp.mint_hs256(SECRET, aud="some-other-app"))


def test_missing_required_claims_rejected():
    # a token with no exp/iss/aud must fail the require= list
    import jwt

    bare = jwt.encode({"sub": "x"}, SECRET, algorithm="HS256")
    with pytest.raises(AuthError):
        _hs_verifier().verify(bare)


def test_alg_none_rejected():
    # an unsigned alg:none token must never pass (none not in algorithms)
    def _b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    claims = idp._claims("attacker", iss=idp.ISSUER, aud=idp.AUDIENCE, scope=None,
                         exp_offset=3600, nbf_offset=0, extra=None)
    none_token = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(claims)}."
    with pytest.raises(AuthError):
        _hs_verifier().verify(none_token)


def test_tampered_signature_rejected():
    tok = idp.mint_hs256(SECRET)
    tampered = tok[:-2] + ("aa" if not tok.endswith("aa") else "bb")
    with pytest.raises(AuthError):
        _hs_verifier().verify(tampered)


def test_malformed_token_rejected():
    with pytest.raises(AuthError):
        _hs_verifier().verify("this.is.not-a-jwt")


def test_algorithm_confusion_hs_token_against_rs_server_rejected():
    """The RS↔HS attack: an HS256-signed token presented to an RS-configured
    server must be refused, because the accepted algorithms are derived from the
    server's key type (RS*/ES*), never from the token header. (PyJWT also blocks
    HMAC-signing with a PEM key at mint time — this asserts the verify-side
    defense that is the actual regression guard.)"""
    if not _crypto():
        pytest.skip("crypto backend unavailable")
    _, public_pem = idp.generate_rsa_keypair()
    hs_token = idp.mint_hs256(SECRET, alg="HS256")
    with pytest.raises(AuthError):
        _rs_verifier(public_pem).verify(hs_token)


def test_valid_rs256_yields_authenticated_actor():
    if not _crypto():
        pytest.skip("crypto backend unavailable")
    private_pem, public_pem = idp.generate_rsa_keypair()
    ident = _rs_verifier(public_pem).verify(idp.mint_rs256(private_pem, sub="ctrl-3"))
    assert ident.actor == "auth:ctrl-3"


def test_jwks_fetch_failure_fails_closed():
    ident_verifier = _rs_verifier(None, jwks_client=idp.FakeJWKSClient(raises=True))
    with pytest.raises(AuthError):  # unreachable JWKS → 401, never open
        ident_verifier.verify("x.y.z")


def test_jwks_success_path():
    if not _crypto():
        pytest.skip("crypto backend unavailable")
    private_pem, public_pem = idp.generate_rsa_keypair()
    v = _rs_verifier(None, jwks_client=idp.FakeJWKSClient(public_pem=public_pem))
    assert v.verify(idp.mint_rs256(private_pem, sub="a")).actor == "auth:a"


def test_missing_subject_rejected():
    with pytest.raises(AuthError):
        _hs_verifier().verify(idp.mint_hs256(SECRET, sub=""))


def test_scopes_are_parsed():
    ident = _hs_verifier().verify(idp.mint_hs256(SECRET, scope="ask:run read"))
    assert ident.scopes == frozenset({"ask:run", "read"})


def _crypto() -> bool:
    try:
        import cryptography  # noqa: F401

        return True
    except ModuleNotFoundError:  # pragma: no cover
        return False


# ----------------------------------------------------------- config: fail loud
def test_ambiguous_config_refuses_to_boot(monkeypatch):
    monkeypatch.setenv("GOVCON_JWT_SECRET", "a")
    monkeypatch.setenv("GOVCON_JWT_JWKS_URL", "https://idp/jwks")
    monkeypatch.setenv("GOVCON_JWT_ISSUER", idp.ISSUER)
    monkeypatch.setenv("GOVCON_JWT_AUDIENCE", idp.AUDIENCE)
    with pytest.raises(RuntimeError):  # two sources → refuse, no guessing
        AuthConfig.from_env()


def test_missing_iss_aud_refuses_to_boot(monkeypatch):
    monkeypatch.setenv("GOVCON_JWT_SECRET", "a")
    with pytest.raises(RuntimeError):
        AuthConfig.from_env()


def test_auth_off_when_no_env(monkeypatch):
    for k in ("GOVCON_JWT_SECRET", "GOVCON_JWT_PUBLIC_KEY", "GOVCON_JWT_JWKS_URL"):
        monkeypatch.delenv(k, raising=False)
    assert AuthConfig.from_env() is None


# --------------------------------------------------------------- integration
def _auth_on(monkeypatch, *, required_scope=None):
    monkeypatch.setenv("GOVCON_JWT_SECRET", SECRET)
    monkeypatch.setenv("GOVCON_JWT_ISSUER", idp.ISSUER)
    monkeypatch.setenv("GOVCON_JWT_AUDIENCE", idp.AUDIENCE)
    if required_scope:
        monkeypatch.setenv("GOVCON_JWT_REQUIRED_SCOPE", required_scope)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_valid_token_sets_authenticated_actor(session_factory, monkeypatch):
    _auth_on(monkeypatch)
    c = TestClient(create_app(session_factory=session_factory))
    r = c.get("/api/whoami", headers=_bearer(idp.mint_hs256(SECRET, sub="analyst-7")))
    assert r.status_code == 200 and r.json()["actor"] == "auth:analyst-7"


def test_hostile_user_header_is_ignored_when_auth_on(session_factory, monkeypatch):
    _auth_on(monkeypatch)
    c = TestClient(create_app(session_factory=session_factory))
    r = c.get(
        "/api/whoami",
        headers={**_bearer(idp.mint_hs256(SECRET, sub="analyst-7")),
                 "X-Govcon-User": "attacker"},
    )
    # the spoofable header must NOT win over the verified token
    assert r.json()["actor"] == "auth:analyst-7"


def test_no_token_is_401_when_auth_on(session_factory, monkeypatch):
    _auth_on(monkeypatch)
    c = TestClient(create_app(session_factory=session_factory))
    assert c.get("/api/whoami").status_code == 401


def test_expired_token_is_401_at_the_edge(session_factory, monkeypatch):
    _auth_on(monkeypatch)
    c = TestClient(create_app(session_factory=session_factory))
    r = c.get("/api/whoami", headers=_bearer(idp.mint_hs256(SECRET, exp_offset=-3600)))
    assert r.status_code == 401


def test_about_and_health_stay_public_when_auth_on(session_factory, monkeypatch):
    _auth_on(monkeypatch)
    c = TestClient(create_app(session_factory=session_factory))
    about = c.get("/api/about")
    assert about.status_code == 200 and "AUTHENTICATED" in about.text
    assert c.get("/health").status_code == 200
    assert c.get("/").status_code == 200


def test_auth_off_is_unchanged(session_factory, monkeypatch):
    for k in ("GOVCON_JWT_SECRET", "GOVCON_JWT_PUBLIC_KEY", "GOVCON_JWT_JWKS_URL"):
        monkeypatch.delenv(k, raising=False)
    c = TestClient(create_app(session_factory=session_factory))
    r = c.get("/api/whoami")  # no token needed, asserted actor
    assert r.status_code == 200 and r.json()["actor"] == "web:anonymous"


def test_jwt_supersedes_shared_secret_gate(session_factory, monkeypatch):
    _auth_on(monkeypatch)
    monkeypatch.setenv("GOVCON_API_TOKEN", "s3cret")  # also set — must be ignored
    c = TestClient(create_app(session_factory=session_factory))
    # a valid JWT passes even though it is not the shared secret
    assert c.get("/api/whoami", headers=_bearer(idp.mint_hs256(SECRET))).status_code == 200
    # the shared secret alone (no valid JWT) is refused when JWT auth is on
    assert c.get("/api/whoami", headers={"Authorization": "Bearer s3cret"}).status_code == 401


def test_scope_gate_403_on_ask_only(session_factory, monkeypatch):
    _auth_on(monkeypatch, required_scope="ask:run")
    monkeypatch.setenv("GOVCON_DATA_MODE", "synthetic")
    from tests.ai.conftest import FakeLLMClient, final_turn

    fake = FakeLLMClient([final_turn("ok") for _ in range(4)])
    c = TestClient(create_app(session_factory=session_factory, llm_client=fake))
    no_scope = idp.mint_hs256(SECRET)  # valid identity, missing the scope
    with_scope = idp.mint_hs256(SECRET, scope="ask:run other")
    # authenticated but forbidden on the expensive route
    assert c.post("/api/ask", json={"question": "hi"},
                  headers=_bearer(no_scope)).status_code == 403
    # a read-only determination route is NOT scope-gated
    assert c.post("/api/cas", json={"award_date": "2026-07-15",
                                    "contract_value": "50000000.00",
                                    "contractor_size": "other_than_small"},
                  headers=_bearer(no_scope)).status_code == 200
    # with the scope, /api/ask is allowed through
    assert c.post("/api/ask", json={"question": "hi"},
                  headers=_bearer(with_scope)).status_code == 200


def test_scope_gate_also_covers_tutor(session_factory, monkeypatch):
    # /api/tutor is an expensive AI route too — same required-scope gate as ask
    _auth_on(monkeypatch, required_scope="ask:run")
    monkeypatch.setenv("GOVCON_DATA_MODE", "synthetic")
    from tests.ai.conftest import FakeLLMClient, final_turn

    fake = FakeLLMClient([final_turn("ok") for _ in range(2)])
    c = TestClient(create_app(session_factory=session_factory, llm_client=fake))
    assert c.post("/api/tutor", json={"question": "hi"},
                  headers=_bearer(idp.mint_hs256(SECRET))).status_code == 403
    assert c.post("/api/tutor", json={"question": "hi"},
                  headers=_bearer(idp.mint_hs256(SECRET, scope="ask:run"))).status_code == 200


def test_auth_is_independent_of_synthetic_gate(session_factory, monkeypatch):
    """auth ≠ real-data: a valid token does NOT flip the tool into real-data
    mode — the synthetic gate still fails closed on GOVCON_DATA_MODE=real."""
    _auth_on(monkeypatch)
    monkeypatch.setenv("GOVCON_DATA_MODE", "real")
    from tests.ai.conftest import FakeLLMClient, final_turn

    fake = FakeLLMClient([final_turn("ok")])
    c = TestClient(create_app(session_factory=session_factory, llm_client=fake))
    body = c.post("/api/ask", json={"question": "hi"},
                  headers=_bearer(idp.mint_hs256(SECRET))).json()
    assert body["ai_available"] is False  # gate held despite valid auth
    assert fake.calls == []  # and the LLM was never called
