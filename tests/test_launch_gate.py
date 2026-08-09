from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api.http import create_app
from app.api.security import TokenRegistry
from app.domain.order_store import OrderStore
from app.ops.launch import LaunchGate

ATTESTED = {
    "signing_verified": True,
    "sandbox_passed": True,
    "pii_controls_verified": True,
}


def live(settings, **overrides):
    return replace(settings, dry_run=False, live_enabled=True, **{**ATTESTED, **overrides})


def boot(settings):
    return TestClient(create_app(order_repository=OrderStore(), settings=settings))


def test_dry_run_boots_without_any_attestation(settings):
    assert boot(settings).get("/health").json()["mode"] == "dry-run"


def test_live_mode_boots_once_every_control_is_attested(settings):
    assert boot(live(settings)).get("/health").json()["mode"] == "live"


@pytest.mark.parametrize("missing", sorted(ATTESTED))
def test_live_mode_refuses_to_boot_with_an_unattested_control(settings, missing):
    with pytest.raises(PermissionError) as exc:
        boot(live(settings, **{missing: False}))
    assert missing in str(exc.value)


def test_live_mode_requires_explicit_enablement_beside_dry_run(settings):
    # DRY_RUN=false alone is not enough; LIVE_ENABLED is a second deliberate act.
    with pytest.raises(PermissionError, match="explicit enablement"):
        boot(replace(settings, dry_run=False, live_enabled=False, **ATTESTED))


def test_live_mode_refuses_to_boot_without_shop_credentials(settings):
    with pytest.raises(PermissionError, match="secrets_configured"):
        boot(live(settings, tts_app_secret=""))


def test_live_mode_refuses_to_boot_without_operator_tokens(settings):
    # Approval is only a real control if a reviewer can be authenticated.
    with pytest.raises(PermissionError, match="approval_required"):
        boot(live(settings, tokens=TokenRegistry()))


def test_checklist_derives_rather_than_trusts(settings):
    derived = replace(settings, **ATTESTED).launch_checklist()
    assert derived.secrets_configured is True
    assert derived.approval_required is True
    blank = replace(settings, tts_shop_id="", tokens=TokenRegistry(), **ATTESTED)
    assert blank.launch_checklist().failures() == ("secrets_configured", "approval_required")


def test_gate_reports_every_failure_at_once(settings):
    checklist = replace(settings, tts_app_key="", tokens=TokenRegistry()).launch_checklist()
    with pytest.raises(PermissionError) as exc:
        LaunchGate(checklist, explicitly_enabled=True).assert_live_allowed()
    message = str(exc.value)
    for expected in ("secrets_configured", "signing_verified", "sandbox_passed"):
        assert expected in message


BOOLEAN_ENV = (
    "DRY_RUN",
    "LIVE_ENABLED",
    "PUBLISH_ENABLED",
    "LAUNCH_SIGNING_VERIFIED",
    "LAUNCH_SANDBOX_PASSED",
    "LAUNCH_PII_CONTROLS_VERIFIED",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in BOOLEAN_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPS_TOKENS", "")
    return monkeypatch


@pytest.mark.parametrize("blank", ["", "   "])
def test_blanking_an_attestation_does_not_attest_it(clean_env, blank):
    """Blanking a variable is the ordinary way to unset it.

    It used to read as True, so a compose file that emptied the LAUNCH_* vars
    silently attested every human control and satisfied the launch gate.
    """
    from app.config import Settings

    for name in BOOLEAN_ENV:
        clean_env.setenv(name, blank)

    settings = Settings.from_env_permissive()
    assert settings.dry_run is True, "a blank DRY_RUN must stay in dry-run"
    assert settings.live_enabled is False
    assert settings.publish_enabled is False
    checklist = settings.launch_checklist()
    assert not checklist.signing_verified
    assert not checklist.sandbox_passed
    assert not checklist.pii_controls_verified
    with pytest.raises(PermissionError):
        LaunchGate(checklist, explicitly_enabled=True).assert_live_allowed()


def test_an_unparseable_flag_is_a_deployment_fault(clean_env):
    """A typo must not be silently read as an attestation in either direction."""
    from app.config import Settings

    clean_env.setenv("LAUNCH_SANDBOX_PASSED", "ture")
    with pytest.raises(RuntimeError, match="LAUNCH_SANDBOX_PASSED"):
        Settings.from_env_permissive()
