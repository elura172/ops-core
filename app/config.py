from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.api.security import TokenRegistry
from app.ops.launch import LaunchChecklist

_TRUTHY = {"1", "true", "yes", "on"}
_FALSEY = {"0", "false", "no", "off"}


def _default_db(name: str) -> str:
    return f"/tmp/{name}" if os.getenv("VERCEL") else name


def _database_settings() -> dict[str, str]:
    stores = {
        "order_db": ("ORDER_DB", "orders.db"),
        "review_db": ("REVIEW_DB", "reviews.db"),
        "user_db": ("USER_DB", "users.db"),
        "inventory_db": ("INVENTORY_DB", "inventory.db"),
        "want_list_db": ("WANT_LIST_DB", "wants.db"),
    }
    shared_dsn = os.getenv("POSTGRES_URL", "").strip()
    targets = {
        field: os.getenv(env_name, "").strip() or shared_dsn or _default_db(filename)
        for field, (env_name, filename) in stores.items()
    }
    if os.getenv("VERCEL"):
        sqlite_stores = [
            stores[field][0]
            for field, target in targets.items()
            if not target.startswith(("postgres://", "postgresql://"))
        ]
        if sqlite_stores:
            raise RuntimeError(
                "Vercel deployments require durable PostgreSQL for every store; "
                "configure POSTGRES_URL or each store URL (SQLite fallback: " + ", ".join(sqlite_stores) + ")"
            )
    return targets


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
    amazon_scraper_api_key: str = ""
    amazon_sp_api_client_id: str = field(default="", repr=False)
    amazon_sp_api_client_secret: str = field(default="", repr=False)
    amazon_sp_api_refresh_token: str = field(default="", repr=False)
    amazon_sp_api_seller_id: str = ""
    amazon_sp_api_marketplace_id: str = "A2EUQ1WTGCTBG2"
    shopify_store_url: str = ""
    shopify_access_token: str = ""
    square_webhook_secret: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id: str = ""
    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ebay_access_token: str = ""
    ebay_verification_token: str = ""
    ebay_marketplace_id: str = "EBAY_CA"
    abebooks_api_key: str = ""
    abebooks_client_id: str = ""
    biblio_api_key: str = ""
    biblio_dealer_id: str = ""
    gemini_api_key: str = ""
    cron_secret: str = ""
    custom_site_connections: str = field(default="", repr=False)
    order_db: str = "orders.db"
    review_db: str = "reviews.db"
    user_db: str = "users.db"
    inventory_db: str = "inventory.db"
    want_list_db: str = "wants.db"
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
        database_settings = _database_settings()
        return cls(
            tts_app_key=required("TTS_APP_KEY"),
            tts_app_secret=required("TTS_APP_SECRET"),
            tts_access_token=required("TTS_ACCESS_TOKEN"),
            tts_shop_id=required("TTS_SHOP_ID"),
            webhook_secret=required("TTS_WEBHOOK_SECRET"),
            shippo_api_key=os.getenv("SHIPPO_API_KEY", ""),
            amazon_scraper_api_key=os.getenv("AMAZON_SCRAPER_API_KEY", "").strip(),
            amazon_sp_api_client_id=os.getenv("AMAZON_SP_API_CLIENT_ID", "").strip(),
            amazon_sp_api_client_secret=os.getenv("AMAZON_SP_API_CLIENT_SECRET", "").strip(),
            amazon_sp_api_refresh_token=os.getenv("AMAZON_SP_API_REFRESH_TOKEN", "").strip(),
            amazon_sp_api_seller_id=os.getenv("AMAZON_SP_API_SELLER_ID", "").strip(),
            amazon_sp_api_marketplace_id=os.getenv("AMAZON_SP_API_MARKETPLACE_ID", "A2EUQ1WTGCTBG2").strip(),
            shopify_store_url=os.getenv("SHOPIFY_STORE_URL", ""),
            shopify_access_token=os.getenv("SHOPIFY_ACCESS_TOKEN", ""),
            square_webhook_secret=os.getenv("SQUARE_WEBHOOK_SECRET", ""),
            stripe_secret_key=os.getenv("STRIPE_SECRET_KEY", "").strip(),
            stripe_webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET", "").strip(),
            stripe_price_id=os.getenv("STRIPE_PRICE_ID", "").strip(),
            ebay_client_id=os.getenv("EBAY_CLIENT_ID", ""),
            ebay_client_secret=os.getenv("EBAY_CLIENT_SECRET", ""),
            ebay_access_token=os.getenv("EBAY_ACCESS_TOKEN", ""),
            ebay_verification_token=os.getenv("EBAY_VERIFICATION_TOKEN", ""),
            ebay_marketplace_id=os.getenv("EBAY_MARKETPLACE_ID", "EBAY_CA"),
            abebooks_api_key=os.getenv("ABEBOOKS_API_KEY", ""),
            abebooks_client_id=os.getenv("ABEBOOKS_CLIENT_ID", ""),
            biblio_api_key=os.getenv("BIBLIO_API_KEY", ""),
            biblio_dealer_id=os.getenv("BIBLIO_DEALER_ID", ""),
            gemini_api_key=os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", ""),
            cron_secret=os.getenv("CRON_SECRET", ""),
            custom_site_connections=os.getenv("CUSTOM_SITE_CONNECTIONS", ""),
            tokens=tokens,
            **database_settings,
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
        database_settings = _database_settings()
        return cls(
            tts_app_key=os.getenv("TTS_APP_KEY", ""),
            tts_app_secret=os.getenv("TTS_APP_SECRET", ""),
            tts_access_token=os.getenv("TTS_ACCESS_TOKEN", ""),
            tts_shop_id=os.getenv("TTS_SHOP_ID", ""),
            webhook_secret=os.getenv("TTS_WEBHOOK_SECRET", ""),
            shippo_api_key=os.getenv("SHIPPO_API_KEY", ""),
            amazon_scraper_api_key=os.getenv("AMAZON_SCRAPER_API_KEY", "").strip(),
            amazon_sp_api_client_id=os.getenv("AMAZON_SP_API_CLIENT_ID", "").strip(),
            amazon_sp_api_client_secret=os.getenv("AMAZON_SP_API_CLIENT_SECRET", "").strip(),
            amazon_sp_api_refresh_token=os.getenv("AMAZON_SP_API_REFRESH_TOKEN", "").strip(),
            amazon_sp_api_seller_id=os.getenv("AMAZON_SP_API_SELLER_ID", "").strip(),
            amazon_sp_api_marketplace_id=os.getenv("AMAZON_SP_API_MARKETPLACE_ID", "A2EUQ1WTGCTBG2").strip(),
            shopify_store_url=os.getenv("SHOPIFY_STORE_URL", ""),
            shopify_access_token=os.getenv("SHOPIFY_ACCESS_TOKEN", ""),
            square_webhook_secret=os.getenv("SQUARE_WEBHOOK_SECRET", ""),
            stripe_secret_key=os.getenv("STRIPE_SECRET_KEY", "").strip(),
            stripe_webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET", "").strip(),
            stripe_price_id=os.getenv("STRIPE_PRICE_ID", "").strip(),
            ebay_client_id=os.getenv("EBAY_CLIENT_ID", ""),
            ebay_client_secret=os.getenv("EBAY_CLIENT_SECRET", ""),
            ebay_access_token=os.getenv("EBAY_ACCESS_TOKEN", ""),
            ebay_verification_token=os.getenv("EBAY_VERIFICATION_TOKEN", ""),
            ebay_marketplace_id=os.getenv("EBAY_MARKETPLACE_ID", "EBAY_CA"),
            abebooks_api_key=os.getenv("ABEBOOKS_API_KEY", ""),
            abebooks_client_id=os.getenv("ABEBOOKS_CLIENT_ID", ""),
            biblio_api_key=os.getenv("BIBLIO_API_KEY", ""),
            biblio_dealer_id=os.getenv("BIBLIO_DEALER_ID", ""),
            gemini_api_key=os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", ""),
            cron_secret=os.getenv("CRON_SECRET", ""),
            custom_site_connections=os.getenv("CUSTOM_SITE_CONNECTIONS", ""),
            tokens=TokenRegistry.from_env(os.getenv("OPS_TOKENS", "").strip()),
            **database_settings,
            dry_run=True,
            live_enabled=_flag("LIVE_ENABLED"),
            signing_verified=_flag("LAUNCH_SIGNING_VERIFIED"),
            sandbox_passed=_flag("LAUNCH_SANDBOX_PASSED"),
            pii_controls_verified=_flag("LAUNCH_PII_CONTROLS_VERIFIED"),
            publish_enabled=_flag("PUBLISH_ENABLED"),
        )
