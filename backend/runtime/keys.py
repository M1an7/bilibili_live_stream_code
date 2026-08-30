from __future__ import annotations

import base64


# Release builds replace this public key through BILILIVE_RUNTIME_PUBLIC_KEY.
# The bundled value is a valid Ed25519 public key, but its private counterpart is
# deliberately not stored in this repository.
RELEASE_PUBLIC_KEY_B64 = "dGVzdC1vbmx5LXB1YmxpYy1rZXktMzItYnl0ZSE="


def release_public_key() -> bytes:
    return base64.b64decode(RELEASE_PUBLIC_KEY_B64)
