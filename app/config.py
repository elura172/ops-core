from __future__ import annotations

import os
from dataclasses import dataclass

from app.api.security import TokenRegistry
from app.ops.launch import LaunchChecklist

_FALSEY = {"0", "false", "no"}


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() not in _FALSEY


@dataclass(frozen=True)
class Settings:
    tts_app_key: str
    tts_app_secret: str
    tts_access_token: str
    tts_shop_id: str
    webhook_secret: str
    tokens: TokenRegistry
    order_db: str = "orders.db"
    review_db: str = "reviews.db"
    dry_run: bool = True
    #: Second key beside DRY_RUN=false, so live traffic needs two deliberate acts.
    live_enabled: bool = False
    #: Attestations a human makes after doing the work; nothing here can verify
    #: them for you, which is exactly why they are separate from the derived checks.
    signing_verified: bool = False
    sandbox_passed: bool = False
    pii_controls_verified: bool = False
    publish_enabled: bool = False

    def launch_checklist(self) -> LaunchChecklist:
        """Derive what can be derived; take the rest as explicit attestation."""
        return LaunchChecklist(
            secrets_configured=all(
                (
                    self.tts_app_key,
                    self.tts_app_secret,
                    self.tts_access_token,
                    self.tts_shop_id,
                    self.webhook_secret,
                )
            ),
            signing_verified=self.signing_verified,
            sandbox_passed=self.sandbox_passed,
            pii_controls_verified=self.pii_controls_verified,
            # The approval control is only real when operator tokens exist to
            # authenticate the reviewer, so this is derived rather than asserted.
            approval_required=self.tokens.configured,
        )

    @classmethod
    def from_env(cls) -> Settings:
        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise RuntimeError(f"missing required secret: {name}")
            return value

        dry_run = _flag("DRY_RUN", "true")
        tokens = TokenRegistry.from_env(os.getenv("OPS_TOKENS", ""))
        if not dry_run and not tokens.configured:
            raise RuntimeError("live mode requires OPS_TOKENS to be configured")
        return cls(
            tts_app_key=required("TTS_APP_KEY"),
            tts_app_secret=required("TTS_APP_SECRET"),
            tts_access_token=required("TTS_ACCESS_TOKEN"),
            tts_shop_id=required("TTS_SHOP_ID"),
            webhook_secret=required("TTS_WEBHOOK_SECRET"),
            tokens=tokens,
            order_db=os.getenv("ORDER_DB", "orders.db").strip() or "orders.db",
            review_db=os.getenv("REVIEW_DB", "reviews.db").strip() or "reviews.db",
            dry_run=dry_run,
            live_enabled=_flag("LIVE_ENABLED"),
            signing_verified=_flag("LAUNCH_SIGNING_VERIFIED"),
            sandbox_passed=_flag("LAUNCH_SANDBOX_PASSED"),
            pii_controls_verified=_flag("LAUNCH_PII_CONTROLS_VERIFIED"),
            publish_enabled=_flag("PUBLISH_ENABLED"),
        )

    @classmethod
    def from_env_permissive(cls) -> Settings:
        """Boot without shop credentials so a dry-run deployment can start.

        Credentials are only required once live mode is enabled; the operator token
        registry and webhook secret are still resolved so both fail closed.
        """
        if not _flag("DRY_RUN", "true"):
            return cls.from_env()
        return cls(
            tts_app_key=os.getenv("TTS_APP_KEY", ""),
            tts_app_secret=os.getenv("TTS_APP_SECRET", ""),
            tts_access_token=os.getenv("TTS_ACCESS_TOKEN", ""),
            tts_shop_id=os.getenv("TTS_SHOP_ID", ""),
            webhook_secret=os.getenv("TTS_WEBHOOK_SECRET", ""),
            tokens=TokenRegistry.from_env(os.getenv("OPS_TOKENS", "")),
            order_db=os.getenv("ORDER_DB", "orders.db").strip() or "orders.db",
            review_db=os.getenv("REVIEW_DB", "reviews.db").strip() or "reviews.db",
            dry_run=True,
            live_enabled=_flag("LIVE_ENABLED"),
            signing_verified=_flag("LAUNCH_SIGNING_VERIFIED"),
            sandbox_passed=_flag("LAUNCH_SANDBOX_PASSED"),
            pii_controls_verified=_flag("LAUNCH_PII_CONTROLS_VERIFIED"),
            publish_enabled=_flag("PUBLISH_ENABLED"),
        )
