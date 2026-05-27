"""Verify R0UT3-issued EdDSA (Ed25519) identity tokens.

R0UT3 issues compact JWTs of the form::

    base64url(header).base64url(payload).base64url(signature)

where base64url is **unpadded**, the header is
``{"alg": "Ed25519", "typ": "JWT", "kid": "<16-hex>"}`` and the signature is a
raw 64-byte Ed25519 signature over the ASCII bytes of
``"<header_b64>.<payload_b64>"``.

The public keys are published as an RFC 7517 JWKS document containing OKP /
Ed25519 keys. R3L4Y is an *edge relay* in the R0UT3 registry, so the
:class:`JWKSClient` keeps a short TTL cache (default 300s) over the JWKS
document — refreshing at most once per cache window rather than on every
verification.

Security posture (see ``CONSUMER_SPEC.md``):

* Only ``alg`` values in :data:`ACCEPTED_ALGS` (``Ed25519`` fully-specified per
  RFC 9864, ``EdDSA`` legacy) are accepted. ``none`` and any HMAC algorithm
  (``HS256`` etc.) are rejected outright.
* ``typ`` must equal ``JWT``.
* ``exp`` must not be in the past.
* ``iss`` must equal :data:`EXPECTED_ISSUER`.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# ---------------------------------------------------------------------------
# Contract constants (see contract.json / CONSUMER_SPEC.md)
# ---------------------------------------------------------------------------

EXPECTED_ISSUER = "spiffe://s4f3/agent/jwt-issuer"
"""SPIFFE id every legitimate R0UT3 token must declare in ``iss``."""

ACCEPTED_ALGS = frozenset({"Ed25519", "EdDSA"})
"""Accepted JOSE ``alg`` header values. ``none``/HMAC are never accepted."""

EXPECTED_TYP = "JWT"

DEFAULT_JWKS_TTL_SECONDS = 300.0
"""R3L4Y edge-relay role: sub-5-minute JWKS cache window."""

JWKS_ENDPOINT = "/.well-known/jwks.json"


class R0ut3TokenError(Exception):
    """Raised when a R0UT3 token fails any verification step."""


@dataclass(frozen=True)
class TokenClaims:
    """Decoded, verified token claims plus the raw header."""

    header: Mapping[str, Any]
    claims: Mapping[str, Any]

    @property
    def issuer(self) -> str:
        return str(self.claims.get("iss", ""))

    @property
    def subject(self) -> str:
        return str(self.claims.get("sub", ""))

    @property
    def kid(self) -> str:
        return str(self.header.get("kid", ""))


# ---------------------------------------------------------------------------
# base64url (no padding) helpers
# ---------------------------------------------------------------------------


def _b64url_decode(segment: str) -> bytes:
    """Decode an unpadded base64url segment to bytes.

    Args:
        segment: The base64url-encoded string, with or without padding.

    Returns:
        The decoded raw bytes.

    Raises:
        R0ut3TokenError: If the segment is not valid base64url.
    """
    if not isinstance(segment, str):  # defensive: callers pass split parts
        raise R0ut3TokenError("token segment is not a string")
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except (ValueError, TypeError) as exc:
        raise R0ut3TokenError(f"invalid base64url segment: {exc}") from exc


# ---------------------------------------------------------------------------
# JWKS client with TTL cache
# ---------------------------------------------------------------------------


@dataclass
class JWKSClient:
    """Loads a JWKS document and caches it for a short TTL window.

    R3L4Y is an edge relay, so the default TTL is
    :data:`DEFAULT_JWKS_TTL_SECONDS` (300s). The client resolves either a
    local file path (tests / offline) or an HTTP(S) URL (production). Each
    distinct ``kid`` is indexed for O(1) lookup once the document is cached.

    Args:
        source: Either a filesystem path or an ``http(s)://`` URL pointing at
            the JWKS document.
        ttl_seconds: Cache lifetime in seconds. Defaults to 300s.
        time_fn: Monotonic-ish clock used for TTL accounting. Injectable for
            tests.
    """

    source: str
    ttl_seconds: float = DEFAULT_JWKS_TTL_SECONDS
    time_fn: Any = time.monotonic

    _keys_by_kid: dict[str, Ed25519PublicKey] = field(
        default_factory=dict, init=False, repr=False
    )
    _fetched_at: float | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )
    _load_count: int = field(default=0, init=False, repr=False)

    @property
    def load_count(self) -> int:
        """Number of times the underlying JWKS source was actually read.

        Useful for tests asserting that a within-TTL lookup is a cache hit.
        """
        return self._load_count

    def _is_fresh(self) -> bool:
        if self._fetched_at is None:
            return False
        return (self.time_fn() - self._fetched_at) < self.ttl_seconds

    def _read_source(self) -> str:
        if self.source.startswith(("http://", "https://")):
            with urllib.request.urlopen(self.source, timeout=10) as resp:  # noqa: S310 - controlled JWKS endpoint
                return resp.read().decode("utf-8")
        return Path(self.source).read_text(encoding="utf-8")

    def _refresh(self) -> None:
        raw = self._read_source()
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise R0ut3TokenError(f"JWKS document is not valid JSON: {exc}") from exc

        keys = doc.get("keys")
        if not isinstance(keys, list):
            raise R0ut3TokenError("JWKS document missing 'keys' array")

        indexed: dict[str, Ed25519PublicKey] = {}
        for jwk in keys:
            if not isinstance(jwk, dict):
                continue
            if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
                # R3L4Y only consumes Ed25519/OKP keys.
                continue
            kid = jwk.get("kid")
            x = jwk.get("x")
            if not isinstance(kid, str) or not isinstance(x, str):
                continue
            pub_bytes = _b64url_decode(x)
            if len(pub_bytes) != 32:
                raise R0ut3TokenError(
                    f"JWK '{kid}' has a {len(pub_bytes)}-byte key (expected 32)"
                )
            indexed[kid] = Ed25519PublicKey.from_public_bytes(pub_bytes)

        if not indexed:
            raise R0ut3TokenError("JWKS document contained no usable Ed25519 keys")

        self._keys_by_kid = indexed
        self._fetched_at = self.time_fn()
        self._load_count += 1

    def get_key(self, kid: str) -> Ed25519PublicKey:
        """Return the Ed25519 public key for ``kid``, refreshing if stale.

        A within-TTL lookup is served from cache (no source read). A stale or
        cold cache triggers exactly one refresh. If the requested ``kid`` is
        still absent after a fresh load, an error is raised.

        Args:
            kid: The key id from the token header.

        Returns:
            The matching Ed25519 public key.

        Raises:
            R0ut3TokenError: If no key matches ``kid`` after a fresh load.
        """
        with self._lock:
            if not self._is_fresh():
                self._refresh()
            key = self._keys_by_kid.get(kid)
            if key is None:
                # One forced refresh in case the key rotated within the window.
                self._refresh()
                key = self._keys_by_kid.get(kid)
            if key is None:
                raise R0ut3TokenError(f"no JWKS key matches kid '{kid}'")
            return key


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


def verify_r0ut3_token(
    token: str,
    jwks_client: JWKSClient,
    *,
    expected_issuer: str = EXPECTED_ISSUER,
    now: float | None = None,
    leeway_seconds: float = 0.0,
) -> TokenClaims:
    """Verify a R0UT3-issued Ed25519 JWT and return its claims.

    The verification chain is, in order:

    1. Split the compact token into three segments.
    2. Decode + parse the header; reject ``alg`` not in
       :data:`ACCEPTED_ALGS` (so ``none``/HMAC are rejected) and any ``typ``
       other than ``JWT``.
    3. Look up the public key by ``kid`` via the TTL-cached JWKS client.
    4. Verify the raw 64-byte Ed25519 signature over the ASCII signing input
       ``"<header_b64>.<payload_b64>"``.
    5. Decode + parse the payload; reject expired ``exp`` and a mismatched
       ``iss``.

    Args:
        token: The compact JWT string.
        jwks_client: A configured :class:`JWKSClient`.
        expected_issuer: The SPIFFE id the token must declare. Defaults to
            :data:`EXPECTED_ISSUER`.
        now: Unix-seconds "current time" for ``exp`` checks. Defaults to
            ``time.time()``. Injectable for tests.
        leeway_seconds: Optional clock-skew tolerance applied to ``exp``.

    Returns:
        The verified :class:`TokenClaims`.

    Raises:
        R0ut3TokenError: On any structural, algorithmic, signature, or claim
            validation failure.
    """
    if not isinstance(token, str) or not token:
        raise R0ut3TokenError("token must be a non-empty string")

    parts = token.split(".")
    if len(parts) != 3:
        raise R0ut3TokenError(
            f"compact JWT must have 3 segments, found {len(parts)}"
        )
    header_b64, payload_b64, signature_b64 = parts

    # --- header ---------------------------------------------------------
    try:
        header = json.loads(_b64url_decode(header_b64))
    except json.JSONDecodeError as exc:
        raise R0ut3TokenError(f"token header is not valid JSON: {exc}") from exc
    if not isinstance(header, dict):
        raise R0ut3TokenError("token header is not a JSON object")

    alg = header.get("alg")
    # Reject alg:none and HMAC/RSA/EC algs — Ed25519/EdDSA only.
    if alg not in ACCEPTED_ALGS:
        raise R0ut3TokenError(
            f"unsupported or forbidden alg {alg!r}; "
            f"only {sorted(ACCEPTED_ALGS)} are accepted"
        )
    if header.get("typ") != EXPECTED_TYP:
        raise R0ut3TokenError(
            f"unexpected typ {header.get('typ')!r}; expected {EXPECTED_TYP!r}"
        )
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise R0ut3TokenError("token header missing 'kid'")

    # --- signature ------------------------------------------------------
    signature = _b64url_decode(signature_b64)
    if len(signature) != 64:
        raise R0ut3TokenError(
            f"Ed25519 signature must be 64 bytes, found {len(signature)}"
        )
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    public_key = jwks_client.get_key(kid)
    try:
        public_key.verify(signature, signing_input)
    except InvalidSignature as exc:
        raise R0ut3TokenError("Ed25519 signature verification failed") from exc

    # --- payload / claims (only trusted after signature verifies) -------
    try:
        claims = json.loads(_b64url_decode(payload_b64))
    except json.JSONDecodeError as exc:
        raise R0ut3TokenError(f"token payload is not valid JSON: {exc}") from exc
    if not isinstance(claims, dict):
        raise R0ut3TokenError("token payload is not a JSON object")

    issuer = claims.get("iss")
    if issuer != expected_issuer:
        raise R0ut3TokenError(
            f"issuer mismatch: {issuer!r} != {expected_issuer!r}"
        )

    exp = claims.get("exp")
    if exp is None:
        raise R0ut3TokenError("token missing 'exp' claim")
    try:
        exp_val = float(exp)
    except (TypeError, ValueError) as exc:
        raise R0ut3TokenError(f"'exp' is not numeric: {exp!r}") from exc
    current = time.time() if now is None else float(now)
    if current > exp_val + leeway_seconds:
        raise R0ut3TokenError(
            f"token expired: exp={exp_val} < now={current}"
        )

    return TokenClaims(header=header, claims=claims)
