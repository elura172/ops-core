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
