from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from app.observability.logging import get_logger

logger = get_logger("security")

READ = "review:read"
DECIDE = "review:decide"
EXECUTE = "review:execute"

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "reviewer": frozenset({READ, DECIDE}),
    "operator": frozenset({READ, EXECUTE}),
    "admin": frozenset({READ, DECIDE, EXECUTE}),
}


class AuthenticationNotConfigured(RuntimeError):
    """Raised when no operator tokens exist; the API must fail closed, not open."""


@dataclass(frozen=True)
class Principal:
    name: str
    roles: frozenset[str]

    @property
    def permissions(self) -> frozenset[str]:
        granted: frozenset[str] = frozenset()
        for role in self.roles:
            granted |= ROLE_PERMISSIONS.get(role, frozenset())
        return granted

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TokenRegistry:
    """Bearer tokens resolved to named principals.

    Only SHA-256 digests are configured, so a leaked environment file does not hand
    over usable credentials. Reviewer identity comes from the token, never from the
    request body.
    """

    principals: tuple[tuple[str, Principal], ...] = ()

    @classmethod
    def from_env(cls, raw: str) -> TokenRegistry:
        """Parse ``<sha256>:<name>:<role>[|<role>],...`` entries."""
        entries: list[tuple[str, Principal]] = []
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = chunk.split(":")
            if len(parts) != 3:
                raise ValueError("each token entry must be <sha256>:<name>:<roles>")
            digest, name, roles_raw = (part.strip() for part in parts)
            if len(digest) != 64:
                raise ValueError(f"token digest for {name!r} must be a sha256 hex digest")
            roles = frozenset(role.strip() for role in roles_raw.split("|") if role.strip())
            unknown = roles - ROLE_PERMISSIONS.keys()
            if not roles or unknown:
                raise ValueError(f"unknown roles for {name!r}: {', '.join(sorted(unknown))}")
            entries.append((digest.lower(), Principal(name, roles)))
        return cls(tuple(entries))

    @property
    def configured(self) -> bool:
        return bool(self.principals)

    def resolve(self, token: str) -> Principal | None:
        """Compare against every entry so timing does not reveal which one matched."""
        if not self.configured:
            raise AuthenticationNotConfigured("no operator tokens are configured")
        presented = hash_token(token)
        matched: Principal | None = None
        for digest, principal in self.principals:
            if hmac.compare_digest(presented, digest):
                matched = principal
        return matched


def bearer_token(header: str) -> str:
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


if __name__ == "__main__":  # pragma: no cover - operator convenience
    import getpass

    print(hash_token(getpass.getpass("token: ")))
