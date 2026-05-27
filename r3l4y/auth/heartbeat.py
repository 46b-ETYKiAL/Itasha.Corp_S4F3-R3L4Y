"""Readiness-heartbeat emitter for the R3L4Y consumer.

After verifying a real R0UT3-issued Ed25519 token, the relay proves readiness
by signing an exact ASCII proof string with *its own* consumer Ed25519 private
key. The proof message is, byte-for-byte::

    <CONSUMER_ID>|<TS>.0|<OK>

where ``<TS>`` is integer Unix seconds (the literal ``.0`` suffix matches
Python's ``f"{float(ts_int)}"`` whole-second form) and ``<OK>`` is the Python
bool repr ``True`` / ``False``.

The private key is provisioned out-of-band (env var ``R0UT3_CONSUMER_PRIV_HEX``
or an explicit path) and is NEVER committed to the repository.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .r0ut3_verify import JWKSClient, R0ut3TokenError, verify_r0ut3_token

CONSUMER_ID = "S4F3-R3L4Y"
PRIV_HEX_ENV = "R0UT3_CONSUMER_PRIV_HEX"


def build_proof_message(consumer_id: str, ts_int: int, ok: bool) -> bytes:
    """Build the exact ASCII proof message bytes.

    Args:
        consumer_id: The registry consumer id (e.g. ``S4F3-R3L4Y``).
        ts_int: Integer Unix seconds.
        ok: Whether token verification succeeded.

    Returns:
        The ASCII-encoded ``"<id>|<ts>.0|<True|False>"`` message.
    """
    return f"{consumer_id}|{ts_int}.0|{'True' if ok else 'False'}".encode("ascii")


def _load_priv_hex(priv_hex: str | None, priv_path: str | None) -> str:
    """Resolve the consumer private-key hex from explicit arg, env, or path."""
    if priv_hex:
        return priv_hex.strip()
    env_val = os.environ.get(PRIV_HEX_ENV)
    if env_val:
        return env_val.strip()
    if priv_path:
        return Path(priv_path).read_text(encoding="utf-8").strip()
    raise R0ut3TokenError(
        f"no private key: set ${PRIV_HEX_ENV} or pass --priv-path"
    )


def emit_heartbeat(
    token: str,
    jwks_source: str,
    *,
    priv_hex: str | None = None,
    priv_path: str | None = None,
    consumer_id: str = CONSUMER_ID,
    ttl_seconds: float | None = None,
    pub_hex: str | None = None,
    now: float | None = None,
) -> dict:
    """Verify a R0UT3 token, then sign + self-verify a readiness heartbeat.

    Args:
        token: The compact R0UT3 JWT to verify.
        jwks_source: Path or URL to the JWKS document.
        priv_hex: Consumer private-key hex (overrides env/path).
        priv_path: Path to a file containing the private-key hex.
        consumer_id: Registry consumer id. Defaults to :data:`CONSUMER_ID`.
        ttl_seconds: JWKS cache TTL; ``None`` uses the client default (300s).
        pub_hex: Optional public-key hex for an explicit self-verify check.
        now: Injectable timestamp for tests (Unix seconds).

    Returns:
        The heartbeat dict ready to serialize:
        ``{"consumer_id", "ts", "eddsa_verify_ok", "signed_proof_hex"}``.

    Raises:
        R0ut3TokenError: If signing or the signature self-check fails.
    """
    client = (
        JWKSClient(jwks_source)
        if ttl_seconds is None
        else JWKSClient(jwks_source, ttl_seconds=ttl_seconds)
    )

    ok = True
    try:
        verify_r0ut3_token(token, client, now=now)
    except R0ut3TokenError:
        ok = False

    priv = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(_load_priv_hex(priv_hex, priv_path))
    )
    ts_int = int(time.time()) if now is None else int(now)
    message = build_proof_message(consumer_id, ts_int, ok)
    signature = priv.sign(message)
    signed_proof_hex = signature.hex()

    # Self-verify (sanity check) before returning.
    verify_pub: Ed25519PublicKey
    if pub_hex:
        verify_pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex.strip()))
    else:
        verify_pub = priv.public_key()
    try:
        verify_pub.verify(signature, message)
    except InvalidSignature as exc:  # pragma: no cover - cryptographic invariant
        raise R0ut3TokenError("heartbeat self-verify failed") from exc

    return {
        "consumer_id": consumer_id,
        "ts": ts_int,
        "eddsa_verify_ok": ok,
        "signed_proof_hex": signed_proof_hex,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: verify a R0UT3 token then emit a readiness heartbeat."""
    parser = argparse.ArgumentParser(
        description="Verify a R0UT3 EdDSA token and emit a R3L4Y readiness heartbeat."
    )
    parser.add_argument("--token", required=True, help="Path to the compact JWT (or '-' for stdin).")
    parser.add_argument("--jwks", required=True, help="Path or URL to the JWKS document.")
    parser.add_argument("--priv-path", help="Path to consumer private-key hex (else $R0UT3_CONSUMER_PRIV_HEX).")
    parser.add_argument("--pub-path", help="Optional path to consumer public-key hex for explicit self-verify.")
    parser.add_argument("--out", help="Path to write the heartbeat JSON (else stdout).")
    parser.add_argument("--consumer-id", default=CONSUMER_ID, help="Registry consumer id.")
    args = parser.parse_args(argv)

    token = sys.stdin.read().strip() if args.token == "-" else Path(args.token).read_text(encoding="utf-8").strip()
    pub_hex = Path(args.pub_path).read_text(encoding="utf-8").strip() if args.pub_path else None

    heartbeat = emit_heartbeat(
        token,
        args.jwks,
        priv_path=args.priv_path,
        consumer_id=args.consumer_id,
        pub_hex=pub_hex,
    )
    payload = json.dumps(heartbeat)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"wrote heartbeat -> {args.out}")
    else:
        print(payload)
    print(
        f"proof tuple: {args.consumer_id}|{heartbeat['ts']}.0|"
        f"{'True' if heartbeat['eddsa_verify_ok'] else 'False'}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
