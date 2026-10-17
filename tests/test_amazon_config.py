from app.config import Settings


def test_amazon_scraper_uses_its_own_environment_credential(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("AMAZON_SCRAPER_API_KEY", "amazon-data-key")
    monkeypatch.setenv("SHIPPO_API_KEY", "shippo-shipping-key")

    settings = Settings.from_env_permissive()

    assert settings.amazon_scraper_api_key == "amazon-data-key"
    assert settings.shippo_api_key == "shippo-shipping-key"


def test_amazon_scraper_credential_is_optional(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.delenv("AMAZON_SCRAPER_API_KEY", raising=False)

    settings = Settings.from_env_permissive()

    assert settings.amazon_scraper_api_key == ""
