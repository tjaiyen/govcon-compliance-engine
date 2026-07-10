"""A tiny fake identity provider for the JWT auth tests — mints tokens the way
a real IdP would (HS256 shared-secret and RS256 asymmetric), and a no-network
stand-in for PyJWKClient so the JWKS path is testable offline.

Imported only by tests/test_api_auth.py, which is skipped unless the `auth`
extra (PyJWT) is installed.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt

ISSUER = "https://idp.example.gov/"
AUDIENCE = "govcon-workbench"


def _claims(sub, *, iss, aud, scope, exp_offset, nbf_offset, extra):
    now = int(time.time())
    claims = {
        "sub": sub,
        "iss": iss,
        "aud": aud,
        "iat": now,
        "nbf": now + nbf_offset,
        "exp": now + exp_offset,
    }
    if scope is not None:
        claims["scope"] = scope
    claims.update(extra or {})
    return claims


def mint_hs256(
    secret,
    *,
    sub="analyst-7",
    iss=ISSUER,
    aud=AUDIENCE,
    scope=None,
    exp_offset=3600,
    nbf_offset=0,
    alg="HS256",
    extra=None,
) -> str:
    return jwt.encode(
        _claims(sub, iss=iss, aud=aud, scope=scope, exp_offset=exp_offset,
                nbf_offset=nbf_offset, extra=extra),
        secret,
        algorithm=alg,
    )


def generate_rsa_keypair():
    """(private_pem, public_pem) as PEM strings — needs the crypto backend."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def mint_rs256(
    private_pem,
    *,
    sub="analyst-7",
    iss=ISSUER,
    aud=AUDIENCE,
    scope=None,
    exp_offset=3600,
    kid="test-key-1",
    extra=None,
) -> str:
    return jwt.encode(
        _claims(sub, iss=iss, aud=aud, scope=scope, exp_offset=exp_offset,
                nbf_offset=0, extra=extra),
        private_pem,
        algorithm="RS256",
        headers={"kid": kid},
    )


class FakeJWKSClient:
    """Stand-in for jwt.PyJWKClient: returns a fixed public key with no network,
    or raises to simulate an unreachable/unknown-kid JWKS endpoint (fail-closed)."""

    def __init__(self, public_pem=None, *, raises=False):
        self._public_pem = public_pem
        self._raises = raises

    def get_signing_key_from_jwt(self, token):
        if self._raises:
            raise RuntimeError("JWKS endpoint unreachable")  # → fail closed (401)
        return SimpleNamespace(key=self._public_pem)
