import pytest

from app.api.security import (
    DECIDE,
    EXECUTE,
    READ,
    AuthenticationNotConfigured,
    TokenRegistry,
    bearer_token,
    hash_token,
)


def registry(role="reviewer", token="s3cret"):
    return TokenRegistry.from_env(f"{hash_token(token)}:ada:{role}")


def test_only_digests_are_configured_so_env_leaks_are_not_credentials():
    raw = f"{hash_token('s3cret')}:ada:reviewer"
    assert "s3cret" not in raw
    assert registry().resolve("s3cret").name == "ada"


def test_unknown_token_resolves_to_nothing():
    assert registry().resolve("wrong") is None


def test_unconfigured_registry_raises_rather_than_allowing():
    with pytest.raises(AuthenticationNotConfigured):
        TokenRegistry().resolve("anything")


def test_roles_map_to_disjoint_permissions():
    reviewer = registry("reviewer").resolve("s3cret")
    operator = registry("operator").resolve("s3cret")
    admin = registry("admin").resolve("s3cret")
    assert reviewer.can(DECIDE) and not reviewer.can(EXECUTE)
    assert operator.can(EXECUTE) and not operator.can(DECIDE)
    assert all(admin.can(permission) for permission in (READ, DECIDE, EXECUTE))


def test_multiple_roles_union_their_permissions():
    principal = registry("reviewer|operator").resolve("s3cret")
    assert principal.can(DECIDE) and principal.can(EXECUTE)


@pytest.mark.parametrize(
    "raw",
    [
        "not-a-digest:ada:reviewer",
        f"{hash_token('t')}:ada",
        f"{hash_token('t')}:ada:wizard",
        f"{hash_token('t')}:ada:",
    ],
)
def test_malformed_entries_fail_at_startup(raw):
    with pytest.raises(ValueError):
        TokenRegistry.from_env(raw)


@pytest.mark.parametrize(
    ("header", "expected"),
    [("Bearer abc", "abc"), ("bearer abc", "abc"), ("Basic abc", ""), ("", ""), ("abc", "")],
)
def test_bearer_parsing(header, expected):
    assert bearer_token(header) == expected
