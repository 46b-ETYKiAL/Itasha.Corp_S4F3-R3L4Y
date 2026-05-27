# R0UT3 EdDSA identity verification

R3L4Y authenticates AI agents using R0UT3-issued Ed25519 (EdDSA) identity
tokens. This package verifies those tokens and emits a signed readiness
heartbeat proving R3L4Y consumed a real R0UT3 token.

## Modules

| Module | Purpose |
|--------|---------|
| `r0ut3_verify.py` | `verify_r0ut3_token()` + a TTL-cached `JWKSClient`. Verifies the raw 64-byte Ed25519 signature over `"<header_b64>.<payload_b64>"`, validates `alg ∈ {Ed25519, EdDSA}` (rejects `none`/HMAC), `typ == JWT`, `exp`, and `iss == spiffe://s4f3/agent/jwt-issuer`. |
| `heartbeat.py` | `emit_heartbeat()` + CLI. Verifies a token, then signs the exact ASCII proof `"<CONSUMER_ID>\|<ts>.0\|<True\|False>"` with the consumer key and self-verifies before returning. |

## Edge-relay JWKS cache

R3L4Y's R0UT3 registry role is *edge relay* with a sub-5-minute JWKS cache, so
`JWKSClient` defaults to a **300-second TTL**. A lookup within the window is a
cache hit (no re-fetch); a stale window triggers exactly one refresh. The
client resolves either a local path (tests/offline) or the production
`/.well-known/jwks.json` URL.

## Verify a token

```python
from r3l4y.auth import JWKSClient, verify_r0ut3_token

client = JWKSClient("https://r0ut3.internal/.well-known/jwks.json")  # 300s TTL
claims = verify_r0ut3_token(token, client)  # raises R0ut3TokenError on failure
print(claims.subject)
```

## Emit a readiness heartbeat (CLI)

```bash
export R0UT3_CONSUMER_PRIV_HEX="<consumer ed25519 private-key hex>"
python -m r3l4y.auth.heartbeat \
  --token r0ut3-token.jwt \
  --jwks jwks.json \
  --out s4f3-r3l4y.heartbeat.json
```

## Key provisioning (out-of-band)

The consumer Ed25519 **private key is never committed to this repository.** It
is provisioned out-of-band and supplied at runtime via the
`R0UT3_CONSUMER_PRIV_HEX` environment variable (or `--priv-path` pointing at a
file outside the tree). Only the corresponding public key is registered with
the R0UT3 `ConsumerRegistry`.
