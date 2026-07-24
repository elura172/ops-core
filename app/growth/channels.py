from __future__ import annotations

from typing import Protocol

from app.growth.models import Campaign, Creative


class SocialChannel(Protocol):
    def publish_draft(self, creative: Creative) -> str: ...
    def start_campaign(self, campaign: Campaign) -> str: ...


class DisabledSocialChannel:
    """Prevents unattended publishing or ad spend until a reviewed adapter is installed."""

    def publish_draft(self, creative: Creative) -> str:
        creative.validate()
        raise RuntimeError("social publishing is disabled by default")

    def start_campaign(self, campaign: Campaign) -> str:
        campaign.validate()
        raise RuntimeError("paid campaign launch is disabled by default")
