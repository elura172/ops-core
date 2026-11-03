"""Bookstagram Drop Studio & Viral Affiliate Flywheel domain engine.

Transforms cataloged inventory items into ready-to-post Bookstagram / Readergram
spec-card metadata, multi-tone captions, curated niche hashtag clouds, and
affiliate attribution links.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.db.inventory import InventoryItem

CORE_BOOKSTAGRAM_TAGS: tuple[str, ...] = (
    "#bookstagram",
    "#readergram",
    "#antiquarianbooks",
    "#rarebooks",
    "#bookcollector",
    "#indiebookshop",
    "#usedbookstore",
    "#bibliophile",
    "#bookhaul",
    "#shelfie",
)

GENRE_TAG_MAP: dict[str, tuple[str, ...]] = {
    "sci-fi": ("#vintagescifi", "#scifibooks", "#speculativefiction", "#scifibookstagram"),
    "science fiction": ("#vintagescifi", "#scifibooks", "#speculativefiction", "#scifibookstagram"),
    "fantasy": ("#fantasybooks", "#vintagefantasy", "#epicfantasy", "#bookstagramfantasy"),
    "rare": ("#rarebookcollection", "#firsteditionbooks", "#antiquarianbookseller", "#finebooks"),
    "vault": ("#rarebookcollection", "#firsteditionbooks", "#antiquarianbookseller", "#finebooks"),
    "canadiana": ("#canadianliterature", "#canadiana", "#canlit", "#ottawabooks"),
    "poetry": ("#poetrycommunity", "#vintagepoetry", "#poetrybooks", "#poetsofinstagram"),
    "philosophy": ("#philosophybooks", "#darkacademia", "#classicalliterature", "#philosophymatters"),
    "history": ("#historybooks", "#historiansofinstagram", "#vintagehistory", "#archival"),
    "literature": ("#classicbooks", "#literaryfiction", "#darkacademiabooks", "#wellread"),
}


@dataclass(frozen=True)
class BookstagramDrop:
    sku: str
    title: str
    authors_display: str
    format_label: str
    condition_grade: str
    price_cad: str
    in_store_price_cad: str
    section: str
    shelf: str
    publisher_line: str
    edition_badge: str
    condition_blurb: str
    shop_name: str
    instagram_handle: str
    affiliate_code: str
    referral_url: str
    captions: dict[str, str]
    hashtags: list[str]
    card_spec: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "title": self.title,
            "authors_display": self.authors_display,
            "format_label": self.format_label,
            "condition_grade": self.condition_grade,
            "price_cad": self.price_cad,
            "in_store_price_cad": self.in_store_price_cad,
            "section": self.section,
            "shelf": self.shelf,
            "publisher_line": self.publisher_line,
            "edition_badge": self.edition_badge,
            "condition_blurb": self.condition_blurb,
            "shop_name": self.shop_name,
            "instagram_handle": self.instagram_handle,
            "affiliate_code": self.affiliate_code,
            "referral_url": self.referral_url,
            "captions": self.captions,
            "hashtags": self.hashtags,
            "card_spec": self.card_spec,
        }


def _slugify_code(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "", text.lower())
    return cleaned[:16] or "corvusvault"


def _human_format(fmt: str) -> str:
    mapping = {
        "hardcover_with_jacket": "Hardcover in Dust Jacket",
        "hardcover_no_jacket": "Cloth Hardcover",
        "trade_paperback": "Trade Paperback",
        "mass_market_paperback": "Vintage Mass Market Paperback",
        "leather": "Full Morocco Leather",
        "half-leather": "Half-Leather Binding",
    }
    key = (fmt or "").strip().lower()
    if key in mapping:
        return mapping[key]
    return (fmt or "Hardcover").replace("_", " ").title()


def _detect_edition_badge(item: InventoryItem) -> str:
    combined = f"{item.title} {item.condition_notes} {item.collation}".lower()
    if "first edition" in combined or "1st edition" in combined or "1st ed" in combined:
        return "TRUE 1ST EDITION"
    if "signed" in combined or "inscribed" in combined:
        return "SIGNED COPY"
    if "leather" in (item.format or "").lower() or "morocco" in combined:
        return "FINE BINDING"
    if item.year and 0 < item.year <= 1975:
        return f"VINTAGE {item.year} IMPRINT"
    return "CURATED VAULT ACQUISITION"


def _select_hashtags(item: InventoryItem, edition_badge: str) -> list[str]:
    tags: list[str] = list(CORE_BOOKSTAGRAM_TAGS)
    sec_lower = (item.section or "").lower()
    title_lower = (item.title or "").lower()

    for key, extra in GENRE_TAG_MAP.items():
        if key in sec_lower or key in title_lower:
            for t in extra:
                if t not in tags:
                    tags.append(t)

    if "1ST EDITION" in edition_badge:
        for t in ("#firstedition", "#truefirst", "#collectiblebooks", "#bookcollecting"):
            if t not in tags:
                tags.append(t)
    elif "FINE BINDING" in edition_badge:
        for t in ("#finebinding", "#leatherbound", "#antiquarian", "#oldbooks"):
            if t not in tags:
                tags.append(t)
    else:
        for t in ("#vintagebooks", "#bookstagrammer", "#readersofinstagram", "#bookish"):
            if t not in tags:
                tags.append(t)

    author_slug = ""
    if item.authors:
        author_slug = re.sub(r"[^a-z0-9]", "", item.authors[0].lower())
    if author_slug and len(author_slug) >= 4:
        tags.append(f"#{author_slug}")

    return tags[:20]


class BookstagramDropGenerator:
    """Generates social drop copy, hashtag clouds, and 4:5 spec card metadata."""

    @classmethod
    def generate(
        cls,
        item: InventoryItem,
        *,
        shop_name: str = "Elura Antiquarian Vault",
        instagram_handle: str = "@elura.antiquarian",
        affiliate_code: str = "",
        base_referral_url: str = "https://corvus.books",
    ) -> BookstagramDrop:
        authors_display = ", ".join(item.authors) if item.authors else "Unknown Author"
        format_label = _human_format(item.format)
        edition_badge = _detect_edition_badge(item)

        effective_price = item.online_price_cad if item.online_price_cad > 0 else item.in_store_price_cad
        price_str = f"{effective_price:.2f}"
        in_store_str = f"{item.in_store_price_cad:.2f}"

        pub_parts: list[str] = []
        if item.publisher:
            pub_parts.append(item.publisher)
        if item.year and item.year > 0:
            pub_parts.append(str(item.year))
        publisher_line = " · ".join(pub_parts) if pub_parts else f"Cataloged in {item.section or 'General'}"

        condition_blurb = (
            item.condition_notes.strip()
            if item.condition_notes and item.condition_notes.strip()
            else (
                f"Graded {item.condition} ({format_label}). Carefully collated and "
                f"shelved in {item.section or 'General'} ({item.shelf or 'Vault'})."
            )
        )

        clean_handle = instagram_handle.strip() or "@corvus.antiquarian"
        if not clean_handle.startswith("@"):
            clean_handle = f"@{clean_handle}"

        code = affiliate_code.strip() or _slugify_code(shop_name)
        referral_url = f"{base_referral_url.rstrip('/')}/?ref={code}&sku={item.sku}"

        hashtags = _select_hashtags(item, edition_badge)
        tags_block = " ".join(hashtags)

        # 1. Dark Academia / Literary Collector Tone
        dark_academia = (
            f"🕯️ Fresh from the {shop_name} stacks:\n\n"
            f"📖 {item.title}\n"
            f"✍️ {authors_display}\n"
            f"🏛️ {publisher_line} · {format_label}\n"
            f"🔍 Condition: {item.condition} — {condition_blurb}\n"
            f"🏷️ ${price_str} CAD (SKU: {item.sku})\n\n"
            f"To claim this copy before it crosses the ocean on AbeBooks or Biblio, "
            f"comment 'CLAIM' below or DM {clean_handle} and Corvus will lock a "
            f"48-hour Priority Courtesy Hold for you.\n\n"
            f"✦ Cataloged & verified via Corvus Antiquarian ({referral_url})\n\n"
            f"{tags_block}"
        )

        # 2. Rare Vault / First-Edition Spec Tone
        vault_drop = (
            f"⚡ [{edition_badge}] VAULT DROP ⚡\n\n"
            f"{item.title} — {authors_display}\n"
            f"• Format: {format_label}\n"
            f"• Imprint: {publisher_line}\n"
            f"• Grade: {item.condition}\n"
            f"• Notes: {condition_blurb}\n"
            f"• Offering Price: ${price_str} CAD\n\n"
            f"Single physical copy in stock at {shop_name} (Shelf {item.shelf or 'Vault-1'}). "
            f"DM {clean_handle} with '{item.sku}' to trigger an instant 48-hour courtesy hold "
            f"across all marketplace channels.\n\n"
            f"✦ Authenticated with Corvus Antiquarian · {referral_url}\n\n"
            f"{tags_block}"
        )

        # 3. Conversational Bookstagram Haul / Story Hook Tone
        stack_haul = (
            f"Look what just walked into {shop_name} today ✨📚\n\n"
            f"We just finished inspecting this copy of '{item.title}' by {authors_display} "
            f"({format_label}, graded {item.condition}). {condition_blurb}\n\n"
            f"Available now in-shop (${in_store_str} CAD) or online (${price_str} CAD). "
            f"Want us to set it aside on the courtesy hold shelf for 48 hours? "
            f"Drop a 'CLAIM' in the comments or DM {clean_handle}!\n\n"
            f"✦ Powered by Corvus Antiquarian · {referral_url}\n\n"
            f"{tags_block}"
        )

        captions = {
            "dark_academia": dark_academia,
            "vault_drop": vault_drop,
            "stack_haul": stack_haul,
        }

        card_spec: dict[str, Any] = {
            "aspect_ratio": "4:5",
            "width": 1080,
            "height": 1350,
            "header_crest": edition_badge,
            "title": item.title,
            "authors": authors_display,
            "publisher_line": publisher_line,
            "format_label": format_label,
            "condition_grade": item.condition,
            "condition_blurb": condition_blurb,
            "price_badge": f"${price_str} CAD",
            "sku": item.sku,
            "location": f"{item.section or 'General'} · {item.shelf or 'Vault'}",
            "shop_name": shop_name,
            "instagram_handle": clean_handle,
            "cta_line": f"DM {clean_handle} or comment CLAIM for a 48h Priority Courtesy Hold",
            "watermark": f"✦ Verified via Corvus Antiquarian · ref:{code}",
        }

        return BookstagramDrop(
            sku=item.sku,
            title=item.title,
            authors_display=authors_display,
            format_label=format_label,
            condition_grade=item.condition,
            price_cad=price_str,
            in_store_price_cad=in_store_str,
            section=item.section or "General",
            shelf=item.shelf or "Vault",
            publisher_line=publisher_line,
            edition_badge=edition_badge,
            condition_blurb=condition_blurb,
            shop_name=shop_name,
            instagram_handle=clean_handle,
            affiliate_code=code,
            referral_url=referral_url,
            captions=captions,
            hashtags=hashtags,
            card_spec=card_spec,
        )
