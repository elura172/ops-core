from __future__ import annotations

import pytest

from app.api.security import TokenRegistry, hash_token
from app.config import Settings

REVIEWER_TOKEN = "reviewer-token"
OPERATOR_TOKEN = "operator-token"
ADMIN_TOKEN = "admin-token"
WEBHOOK_SECRET = "secret"


@pytest.fixture
def tokens() -> TokenRegistry:
    return TokenRegistry.from_env(
        ",".join(
            (
                f"{hash_token(REVIEWER_TOKEN)}:ada:reviewer",
                f"{hash_token(OPERATOR_TOKEN)}:grace:operator",
                f"{hash_token(ADMIN_TOKEN)}:root:admin",
            )
        )
    )


@pytest.fixture
def settings(tokens: TokenRegistry) -> Settings:
    return Settings(
        tts_app_key="key",
        tts_app_secret="secret",
        tts_access_token="token",
        tts_shop_id="shop",
        webhook_secret=WEBHOOK_SECRET,
        tokens=tokens,
        order_db=":memory:",
        dry_run=True,
    )


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
