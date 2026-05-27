"""Tests for the R0UT3 EdDSA token verifier and the R3L4Y heartbeat emitter.

These exercise the real golden token + JWKS produced by R0UT3 (copied into
``tests/fixtures/``):

* the golden token verifies and yields the expected issuer/subject;
* a tampered signature is rejected;
* ``alg:none`` and ``HS256`` headers are rejected before any signature work;
* a within-TTL second lookup is served from cache (no extra source read).
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from r3l4y.auth.heartbeat import build_proof_message, emit_heartbeat
from r3l4y.auth.r0ut3_verify import (
    JWKSClient,
    R0ut3TokenError,
    verify_r0ut3_token,
)

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN_TOKEN = (FIXTURES / "r0ut3-token.jwt").read_text(encoding="utf-8").strip()
JWKS_PATH = str(FIXTURES / "jwks.json")

# Inside the golden token's validity window (iat=1779894382, exp=1779980782).
WITHIN_WINDOW = 1779900000.0
EXPECTED_ISSUER = "spiffe://s4f3/agent/jwt-issuer"


def _b64url_nopad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _make_header_token(header: dict) -> str:
    """Build a structurally valid 3-segment token with a given header.

    The signature segment is a syntactically valid 64-byte blob; verification
    must reject these on the header alg check *before* touching the signature.
    """
    header_b64 = _b64url_nopad(json.dumps(header).encode())
    payload_b64 = _b64url_nopad(
        json.dumps({"iss": EXPECTED_ISSUER, "exp": 1779980782}).encode()
    )
    sig_b64 = _b64url_nopad(b"\x00" * 64)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def test_golden_token_verifies() -> None:
    client = JWKSClient(JWKS_PATH)
    claims = verify_r0ut3_token(GOLDEN_TOKEN, client, now=WITHIN_WINDOW)
    assert claims.issuer == EXPECTED_ISSUER
    assert claims.subject == "spiffe://s4f3/agent/orchestrator"
    assert claims.kid == "1d522f9f2e3253f1"
    assert claims.header["alg"] == "Ed25519"


def test_tampered_signature_fails() -> None:
    header_b64, payload_b64, sig_b64 = GOLDEN_TOKEN.split(".")
    sig = bytearray(base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4)))
    sig[0] ^= 0x01  # flip one signature byte
    tampered_sig_b64 = _b64url_nopad(bytes(sig))
    tampered = f"{header_b64}.{payload_b64}.{tampered_sig_b64}"

    client = JWKSClient(JWKS_PATH)
    with pytest.raises(R0ut3TokenError, match="signature verification failed"):
        verify_r0ut3_token(tampered, client, now=WITHIN_WINDOW)


def test_tampered_payload_fails() -> None:
    header_b64, payload_b64, sig_b64 = GOLDEN_TOKEN.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    claims["sub"] = "spiffe://s4f3/agent/attacker"
    forged_payload_b64 = _b64url_nopad(json.dumps(claims).encode())
    forged = f"{header_b64}.{forged_payload_b64}.{sig_b64}"

    client = JWKSClient(JWKS_PATH)
    with pytest.raises(R0ut3TokenError, match="signature verification failed"):
        verify_r0ut3_token(forged, client, now=WITHIN_WINDOW)


def test_alg_none_rejected() -> None:
    token = _make_header_token({"alg": "none", "typ": "JWT", "kid": "1d522f9f2e3253f1"})
    client = JWKSClient(JWKS_PATH)
    with pytest.raises(R0ut3TokenError, match="forbidden alg"):
        verify_r0ut3_token(token, client, now=WITHIN_WINDOW)


def test_hs256_rejected() -> None:
    token = _make_header_token({"alg": "HS256", "typ": "JWT", "kid": "1d522f9f2e3253f1"})
    client = JWKSClient(JWKS_PATH)
    with pytest.raises(R0ut3TokenError, match="forbidden alg"):
        verify_r0ut3_token(token, client, now=WITHIN_WINDOW)


def test_wrong_issuer_rejected() -> None:
    # A correctly signed token whose issuer differs would be rejected; here we
    # assert the golden token rejects when we demand a different issuer.
    client = JWKSClient(JWKS_PATH)
    with pytest.raises(R0ut3TokenError, match="issuer mismatch"):
        verify_r0ut3_token(
            GOLDEN_TOKEN, client, expected_issuer="spiffe://other", now=WITHIN_WINDOW
        )


def test_expired_token_rejected() -> None:
    client = JWKSClient(JWKS_PATH)
    with pytest.raises(R0ut3TokenError, match="expired"):
        verify_r0ut3_token(GOLDEN_TOKEN, client, now=2_000_000_000.0)


def test_unknown_kid_rejected() -> None:
    header_b64, payload_b64, sig_b64 = GOLDEN_TOKEN.split(".")
    bad_header = _b64url_nopad(
        json.dumps({"alg": "Ed25519", "typ": "JWT", "kid": "deadbeefdeadbeef"}).encode()
    )
    token = f"{bad_header}.{payload_b64}.{sig_b64}"
    client = JWKSClient(JWKS_PATH)
    with pytest.raises(R0ut3TokenError, match="no JWKS key matches"):
        verify_r0ut3_token(token, client, now=WITHIN_WINDOW)


def test_jwks_cache_hit_within_ttl() -> None:
    clock = {"t": 1000.0}
    client = JWKSClient(JWKS_PATH, ttl_seconds=300.0, time_fn=lambda: clock["t"])

    verify_r0ut3_token(GOLDEN_TOKEN, client, now=WITHIN_WINDOW)
    assert client.load_count == 1

    # Advance within TTL -> served from cache, no extra source read.
    clock["t"] = 1200.0
    verify_r0ut3_token(GOLDEN_TOKEN, client, now=WITHIN_WINDOW)
    assert client.load_count == 1, "within-TTL lookup must be a cache hit"

    # Advance past TTL -> one refresh.
    clock["t"] = 1400.0
    verify_r0ut3_token(GOLDEN_TOKEN, client, now=WITHIN_WINDOW)
    assert client.load_count == 2, "post-TTL lookup must refresh"


def test_heartbeat_proof_message_exact_ascii() -> None:
    msg = build_proof_message("S4F3-R3L4Y", 1716800000, True)
    assert msg == b"S4F3-R3L4Y|1716800000.0|True"
    assert build_proof_message("S4F3-R3L4Y", 1716800000, False) == (
        b"S4F3-R3L4Y|1716800000.0|False"
    )


def test_emit_heartbeat_self_verifies(tmp_path: pytest.TempPathFactory) -> None:
    # A throwaway keypair so the test needs no kit secret.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    priv_hex = priv.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    ).hex()
    pub_hex = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()

    hb = emit_heartbeat(
        GOLDEN_TOKEN,
        JWKS_PATH,
        priv_hex=priv_hex,
        pub_hex=pub_hex,
        now=WITHIN_WINDOW,
    )
    assert hb["consumer_id"] == "S4F3-R3L4Y"
    assert hb["eddsa_verify_ok"] is True
    assert hb["ts"] == int(WITHIN_WINDOW)
    assert len(hb["signed_proof_hex"]) == 128

    # Independent re-verify of the emitted signature.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
    msg = build_proof_message("S4F3-R3L4Y", int(WITHIN_WINDOW), True)
    pub.verify(bytes.fromhex(hb["signed_proof_hex"]), msg)  # raises on failure
