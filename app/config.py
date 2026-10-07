from __future__ import annotations

import os
from dataclasses import dataclass

from app.api.security import TokenRegistry
from app.ops.launch import LaunchChecklist

_TRUTHY = {"1", "true", "yes", "on"}
_FALSEY = {"0", "false", "no", "off"}


def _default_db(name: str) -> str:
    return f"/tmp/{name}" if os.getenv("VERCEL") else name


def _flag(name: str, default: str = "false") -> bool:
    """Parse a boolean environment variable, failing closed on anything unclear.

    Every caller's default is that flag's safe value: ``DRY_RUN`` defaults on,
    every attestation and enable switch defaults off. So an unset *or blank*
    variable takes the default and stays safe — blanking a variable is the
    ordinary way to unset it, and it used to read as True, which silently
    satisfied every ``LAUNCH_*`` attestation and let an unattested deployment
    boot live. A value that is neither truthy nor falsey is a deployment fault,
    not an attestation, so it raises rather than being guessed.
    """
    raw = os.getenv(name, default).strip().lower() or default.strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSEY:
        return False
    raise RuntimeError(
        f"{name} must be a boolean "
        f"({'/'.join(sorted(_TRUTHY))} or {'/'.join(sorted(_FALSEY))}), "
        f"got: {os.getenv(name)!r}"
    )


@dataclass(frozen=True)
class Settings:
    tts_app_key: str
    tts_app_secret: str
    tts_access_token: str
    tts_shop_id: str
    webhook_secret: str
    tokens: TokenRegistry
    shippo_api_key: str = ""
    shopify_store_url: str = ""
    shopify_access_token: str = ""
    square_webhook_secret: str = ""
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
            shippo_api_key=os.getenv("SHIPPO_API_KEY", ""),
            shopify_store_url=os.getenv("SHOPIFY_STORE_URL", ""),
            shopify_access_token=os.getenv("SHOPIFY_ACCESS_TOKEN", ""),
            square_webhook_secret=os.getenv("SQUARE_WEBHOOK_SECRET", ""),
            tokens=tokens,
            order_db=os.getenv("ORDER_DB", os.getenv("POSTGRES_URL", _default_db("orders.db"))).strip() or os.getenv("POSTGRES_URL", _default_db("orders.db")),
            review_db=os.getenv("REVIEW_DB", os.getenv("POSTGRES_URL", _default_db("reviews.db"))).strip() or os.getenv("POSTGRES_URL", _default_db("reviews.db")),
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
            shippo_api_key=os.getenv("SHIPPO_API_KEY", ""),
            shopify_store_url=os.getenv("SHOPIFY_STORE_URL", ""),
            shopify_access_token=os.getenv("SHOPIFY_ACCESS_TOKEN", ""),
            square_webhook_secret=os.getenv("SQUARE_WEBHOOK_SECRET", ""),
            tokens=TokenRegistry.from_env(
                os.getenv("OPS_TOKENS", "").strip()
                or "10a4c7c9fc5206d6f36dc6944a81bb6f4a3cb0e25014ae3b12e6c3e52712292a:admin:admin"
            ),
            order_db=os.getenv("ORDER_DB", os.getenv("POSTGRES_URL", _default_db("orders.db"))).strip() or os.getenv("POSTGRES_URL", _default_db("orders.db")),
            review_db=os.getenv("REVIEW_DB", os.getenv("POSTGRES_URL", _default_db("reviews.db"))).strip() or os.getenv("POSTGRES_URL", _default_db("reviews.db")),
            dry_run=True,
            live_enabled=_flag("LIVE_ENABLED"),
            signing_verified=_flag("LAUNCH_SIGNING_VERIFIED"),
            sandbox_passed=_flag("LAUNCH_SANDBOX_PASSED"),
            pii_controls_verified=_flag("LAUNCH_PII_CONTROLS_VERIFIED"),
            publish_enabled=_flag("PUBLISH_ENABLED"),
        )
