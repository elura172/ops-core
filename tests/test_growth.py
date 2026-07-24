from decimal import Decimal

import pytest

from app.growth.attribution import campaign_url, can_spend, rank_creatives
from app.growth.channels import DisabledSocialChannel
from app.growth.models import Campaign, Channel, Creative, CreativePerformance


def campaign():
    return Campaign(
        "c-1", "cr-1", Channel.TIKTOK, Decimal("10"), Decimal("100"), "https://shop.example/p/1"
    )


def test_campaign_url_preserves_existing_query():
    url = campaign_url(
        "https://shop.example/p?ref=x",
        source="tiktok",
        medium="organic",
        campaign="c-1",
        creative="cr-1",
    )
    assert "ref=x" in url and "utm_content=cr-1" in url


def test_rank_creatives_prefers_roas():
    items = [
        CreativePerformance("a", 1000, 100, 2, Decimal("10"), Decimal("20")),
        CreativePerformance("b", 1000, 50, 2, Decimal("10"), Decimal("50")),
    ]
    assert rank_creatives(items)[0].creative_id == "b"


def test_budget_guardrail():
    assert can_spend(Decimal("90"), campaign(), Decimal("10"))
    assert not can_spend(Decimal("90"), campaign(), Decimal("11"))


def test_unverified_creative_and_social_publish_blocked():
    creative = Creative("cr-1", "p-1", Channel.TIKTOK, "https://asset", False)
    with pytest.raises(PermissionError):
        DisabledSocialChannel().publish_draft(creative)
