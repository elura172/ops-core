from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping


def sign_request(app_secret: str, path: str, params: Mapping[str, str], body: str = "") -> str:
    """Build a deterministic HMAC-SHA256 signature for request testing.

    The exact canonicalization must be kept in sync with the current TikTok
    Partner Center specification before enabling production traffic.
    """
    if not app_secret or not path.startswith("/"):
        raise ValueError("app_secret and absolute path are required")
    query = "".join(f"{key}{value}" for key, value in sorted(params.items()))
    message = f"{path}{query}{body}"
    return hmac.new(app_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
