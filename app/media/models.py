from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AssetLicense(StrEnum):
    SUPPLIER_AUTHORIZED = "SUPPLIER_AUTHORIZED"
    ORIGINAL = "ORIGINAL"
    PUBLIC_DOMAIN = "PUBLIC_DOMAIN"
    LICENSED = "LICENSED"


@dataclass(frozen=True)
class MediaAsset:
    asset_id: str
    uri: str
    media_type: str
    license: AssetLicense
    attribution: str | None = None
    product_match_confirmed: bool = False

    def validate(self) -> None:
        if not self.asset_id or not self.uri:
            raise ValueError("asset ID and URI are required")
        if self.media_type not in {"image", "video", "audio"}:
            raise ValueError("unsupported media type")
        if self.license == AssetLicense.LICENSED and not self.attribution:
            raise ValueError("licensed assets require attribution metadata")


@dataclass(frozen=True)
class AdDraft:
    draft_id: str
    product_id: str
    assets: tuple[MediaAsset, ...]
    hook: str
    body: str
    call_to_action: str
    duration_seconds: int = 15
    aspect_ratio: str = "9:16"
    approved: bool = False

    def validate(self) -> None:
        if self.aspect_ratio != "9:16":
            raise ValueError("ad drafts must use a vertical 9:16 aspect ratio")
        if not 5 <= self.duration_seconds <= 60:
            raise ValueError("ad duration must be between 5 and 60 seconds")
        if not all((self.hook.strip(), self.body.strip(), self.call_to_action.strip())):
            raise ValueError("hook, body, and call to action are required")
        for asset in self.assets:
            asset.validate()
        if not self.assets:
            raise ValueError("at least one authorized asset is required")


class AssetLedger:
    def __init__(self) -> None:
        self.assets: dict[str, MediaAsset] = {}

    def add(self, asset: MediaAsset) -> None:
        asset.validate()
        if asset.asset_id in self.assets:
            raise ValueError("asset ID already exists")
        self.assets[asset.asset_id] = asset

    def product_assets(self, product_id: str, asset_ids: tuple[str, ...]) -> tuple[MediaAsset, ...]:
        selected = tuple(self.assets[asset_id] for asset_id in asset_ids)
        if not selected or not all(asset.product_match_confirmed for asset in selected):
            raise ValueError("all selected assets must be confirmed matches for the product")
        return selected
