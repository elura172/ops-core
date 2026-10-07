from __future__ import annotations

import base64
import contextlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.ai.errors import (
    RateLimited,
    TerminalProviderError,
    TransientProviderError,
)
from app.ai.usage import UsageLedger
from app.domain.books import BookStackExtraction
from app.net import RetryPolicy
from app.observability.logging import get_logger

logger = get_logger("book_vision")

BOOK_STACK_SYSTEM_PROMPT = """\
You are a specialist antiquarian and commercial used-book intake cataloger.
Analyze the provided image containing a pile, stack, or shelf of books and extract individual
bibliographic records from every visible spine.

GUIDELINES:
1. SEQUENCE:
   - For horizontal stacks: sequence strictly from TOP to BOTTOM (stack_order: 1 is topmost).
   - For vertical shelves: sequence strictly from LEFT to RIGHT (stack_order: 1 is leftmost).
   - If mixed or angled, sequence by the most dominant logical reading order.

2. BOUNDING BOX:
   - Provide normalized bounding box coordinates for each spine as box_2d:
     [ymin, xmin, ymax, xmax] scaled 0 to 1000.

3. TYPOGRAPHIC SEGREGATION:
   - Disentangle the Title from Author, Subtitle, and Publisher logos/colophons.
   - Account for rotated or vertical spine text.
   - Publisher marks (e.g. Penguin, Knopf, Tor, HarperCollins, Vintage) must be placed
     in publisher_imprint, never merged into title or author.

4. BINDING CLASSIFICATION:
   - hardcover_with_jacket: visible paper wrap / dust jacket flaps.
   - hardcover_no_jacket: cloth, buckram, or board with visible hinge groove without jacket.
   - trade_paperback: large softcover, square glued spine, regular paper.
   - mass_market_paperback: pocket size, narrow spine, pulp paper.
   - spiral_or_other: spiral wire, comb, booklet.
   - unknown: cannot be determined.

5. DEFECT AND WEAR DETECTION:
   - Look for: clean_as_new, sunned_faded_spine, creased_reading_spine, bumped_headcap_or_tail,
     torn_or_chipped_jacket, ex_library_stickers_or_tape, remainder_mark_visible, cocked_spine.

6. RAW TEXT:
   - Always record raw_spine_text with verbatim transcribed spine text for downstream fuzzy search.
"""

BOOK_STACK_GEMINI_SCHEMA = {
    "type": "object",
    "properties": {
        "books": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "stack_order": {"type": "integer"},
                    "box_2d": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "authors": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "publisher_imprint": {"type": "string"},
                    "format": {
                        "type": "string",
                        "enum": [
                            "hardcover_with_jacket",
                            "hardcover_no_jacket",
                            "trade_paperback",
                            "mass_market_paperback",
                            "spiral_or_other",
                            "unknown",
                        ],
                    },
                    "visible_condition_cues": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "clean_as_new",
                                "sunned_faded_spine",
                                "creased_reading_spine",
                                "bumped_headcap_or_tail",
                                "torn_or_chipped_jacket",
                                "ex_library_stickers_or_tape",
                                "remainder_mark_visible",
                                "cocked_spine",
                            ],
                        },
                    },
                    "edition_or_series_markers": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "raw_spine_text": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                },
                "required": [
                    "stack_order",
                    "box_2d",
                    "title",
                    "authors",
                    "format",
                    "visible_condition_cues",
                    "raw_spine_text",
                    "confidence",
                ],
            },
        },
        "orientation": {
            "type": "string",
            "enum": ["horizontal_stack", "vertical_shelf", "mixed_or_scattered"],
        },
        "unidentified_spines_count": {"type": "integer"},
    },
    "required": ["books", "orientation", "unidentified_spines_count"],
}

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def resolve_gemini_api_key(explicit_key: str | None = None) -> str:
    """Finds Gemini API key from explicit arg, env, or local ~/.hermes/.env fallback."""
    if explicit_key:
        return explicit_key

    for env_var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = os.environ.get(env_var)
        if val:
            return val.strip()

    hermes_env = Path.home() / ".hermes" / ".env"
    if hermes_env.exists():
        try:
            for line in hermes_env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("GEMINI_API_KEY=") or line.startswith("GOOGLE_API_KEY="):
                    parts = line.split("=", 1)
                    if len(parts) == 2 and parts[1].strip():
                        return parts[1].strip().strip("'\"")
        except Exception as exc:
            logger.debug(f"Failed to read {hermes_env}: {exc}")

    raise ValueError(
        "No Gemini API key found. Set GEMINI_API_KEY in environment or provide directly."
    )


class BookVisionProvider(Protocol):
    """Protocol for book stack vision extraction providers."""

    def extract_stack(
        self, image_bytes: bytes, mime_type: str = "image/jpeg"
    ) -> BookStackExtraction: ...


@dataclass
class GeminiBookVisionProvider(BookVisionProvider):
    """High-reliability client for Google Gemini Vision with structured schema output."""

    api_key: str
    model: str = "gemini-2.5-flash"
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout: float = 45.0
    retries: RetryPolicy = field(default_factory=RetryPolicy)
    usage: UsageLedger = field(default_factory=UsageLedger)

    def extract_stack(
        self, image_bytes: bytes, mime_type: str = "image/jpeg"
    ) -> BookStackExtraction:
        if not image_bytes:
            raise ValueError("image_bytes cannot be empty")

        b64_img = base64.b64encode(image_bytes).decode("ascii")

        body: dict[str, Any] = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": b64_img,
                            }
                        },
                        {
                            "text": BOOK_STACK_SYSTEM_PROMPT,
                        },
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": BOOK_STACK_GEMINI_SCHEMA,
                "temperature": 0.1,
            },
        }

        url = f"{self.base_url.rstrip('/')}/models/{self.model}:generateContent?key={self.api_key}"
        raw_response = self._post_with_retry(url, body)
        return self._parse_gemini_response(raw_response)

    def _post_with_retry(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        req = Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        for attempt in range(1, self.retries.max_attempts + 1):
            try:
                with urlopen(req, timeout=self.timeout) as resp:
                    if resp.status in RETRYABLE_STATUS:
                        raise TransientProviderError(f"Gemini returned HTTP {resp.status}")
                    if resp.status >= 300:
                        raise TerminalProviderError(f"Gemini returned HTTP {resp.status}")
                    return json.loads(resp.read().decode("utf-8"))
            except HTTPError as exc:
                if exc.code == 429:
                    if attempt >= self.retries.max_attempts:
                        raise RateLimited("Gemini rate limited (HTTP 429)") from exc
                elif exc.code in RETRYABLE_STATUS:
                    if attempt >= self.retries.max_attempts:
                        raise TransientProviderError(f"Gemini HTTP {exc.code}") from exc
                else:
                    raise TerminalProviderError(f"Gemini HTTP {exc.code}") from exc

                sleep_sec = self.retries.backoff(attempt)
                logger.info(
                    f"Gemini request failed (attempt {attempt}), retrying in {sleep_sec:.2f}s"
                )
                time.sleep(sleep_sec)
            except URLError as exc:
                if attempt >= self.retries.max_attempts:
                    raise TransientProviderError(f"Network error: {exc.reason}") from exc
                time.sleep(self.retries.backoff(attempt))

        raise TransientProviderError("Exceeded maximum retries for Gemini API")

    def _parse_gemini_response(self, raw_resp: dict[str, Any]) -> BookStackExtraction:
        candidates = raw_resp.get("candidates", [])
        if not candidates:
            raise TerminalProviderError("Gemini returned no candidates in response")

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        if not parts:
            raise TerminalProviderError("Gemini candidate contains no content parts")

        text = parts[0].get("text", "")
        if not text:
            raise TerminalProviderError("Empty text payload from Gemini candidate")

        try:
            parsed_json = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TerminalProviderError(f"Failed to parse structured JSON: {exc}") from exc

        # Record usage if metadata exists
        usage_meta = raw_resp.get("usageMetadata", {})
        if usage_meta and hasattr(self.usage, "record"):
            prompt_tokens = usage_meta.get("promptTokenCount", 0)
            candidates_tokens = usage_meta.get("candidatesTokenCount", 0)
            with contextlib.suppress(Exception):
                self.usage.record(self.model, prompt_tokens, candidates_tokens)

        return BookStackExtraction.from_dict(parsed_json)
