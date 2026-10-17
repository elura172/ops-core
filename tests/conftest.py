from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from app.api.security import TokenRegistry, hash_token
from app.config import Settings


def pytest_configure(config: pytest.Config) -> None:
    # Isolated deterministic signing material for the test process only.
    os.environ.setdefault("JWT_SECRET_KEY", "test-only-jwt-signing-secret-material-32-bytes-minimum")


#: A live Postgres for the tests that prove multi-process behavior, e.g.
#:   OPS_TEST_POSTGRES_DSN=postgresql://ops@127.0.0.1:54329/ops_core_test
#: Postgres-backed tests skip when it is unset, so the default run stays
#: dependency-free; CI and the load harness set it.
POSTGRES_DSN_VAR = "OPS_TEST_POSTGRES_DSN"


@pytest.fixture
def postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_VAR, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_VAR} is not set")
    return dsn


@pytest.fixture
def postgres_store(postgres_dsn: str) -> Iterator[object]:
    from app.db.migrate import migrate
    from app.db.postgres import PostgresReviewStore

    migrate(postgres_dsn)
    store = PostgresReviewStore(postgres_dsn, pool_size=16)
    with store.pool.connection() as connection:
        connection.execute("TRUNCATE reviews, audit_events")
    yield store
    store.close()


@pytest.fixture
def postgres_order_repository(postgres_dsn: str) -> Iterator[object]:
    from app.db.migrate import migrate
    from app.db.postgres_orders import PostgresOrderRepository

    migrate(postgres_dsn)
    repository = PostgresOrderRepository(postgres_dsn, pool_size=16)
    with repository.pool.connection() as connection:
        connection.execute("TRUNCATE orders, processed_events, quarantined_events")
    yield repository
    repository.close()


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
        # Both, or create_app() writes a real reviews.db into whatever directory
        # pytest was run from and keeps it open across the whole session.
        review_db=":memory:",
        inventory_db=":memory:",
        want_list_db=":memory:",
        user_db=":memory:",
        dry_run=True,
    )


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
