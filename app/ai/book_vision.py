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
1. EXHAUSTIVE EXTRACTION MANDATE:
   - You MUST detect and extract EVERY SINGLE visible book spine in the pile, stack, or shelf.
   - NEVER stop after extracting only 1 or 2 books when more spines are visible in the image.
   - If there are 3, 5, 8, 10, or 20 books visible, your `books` array MUST contain an entry for EVERY one of them.
   - Methodically scan across the entire image from edge to edge (top-to-bottom for stacks, left-to-right for shelves).

2. SEQUENCE:
   - For horizontal stacks: sequence strictly from TOP to BOTTOM (stack_order: 1 is topmost).
   - For vertical shelves: sequence strictly from LEFT to RIGHT (stack_order: 1 is leftmost).
   - For a whole bookcase, read one shelf row at a time, left-to-right, then top-to-bottom.
   - If mixed or angled, sequence by the most dominant logical reading order.
   - Two visible spines with the same title are TWO physical copies. Return both with separate boxes.
   - A close-up may show just one book. Never invent additional spines to fill a shelf.

3. BOUNDING BOX:
   - Provide normalized bounding box coordinates for each spine as box_2d:
     [ymin, xmin, ymax, xmax] scaled 0 to 1000.

4. TYPOGRAPHIC SEGREGATION:
   - Disentangle the Title from Author, Subtitle, and Publisher logos/colophons.
   - Account for rotated or vertical spine text.
   - Publisher marks (e.g. Penguin, Knopf, Tor, HarperCollins, Vintage, TAN) must be placed
     in publisher_imprint, never merged into title or author.

5. BINDING CLASSIFICATION:
   - hardcover_with_jacket: visible paper wrap / dust jacket flaps.
   - hardcover_no_jacket: cloth, buckram, or board with visible hinge groove without jacket.
   - trade_paperback: large softcover, square glued spine, regular paper.
   - mass_market_paperback: pocket size, narrow spine, pulp paper.
   - spiral_or_other: spiral wire, comb, booklet.
   - unknown: cannot be determined.

6. DEFECT AND WEAR DETECTION:
   - Look for: clean_as_new, sunned_faded_spine, creased_reading_spine, bumped_headcap_or_tail,
     torn_or_chipped_jacket, ex_library_stickers_or_tape, remainder_mark_visible, cocked_spine.

7. RAW TEXT:
   - Always record raw_spine_text with verbatim transcribed spine text for downstream fuzzy search.

8. AUTHOR INFERENCE:
   - When the spine does not visibly print the author's name (e.g. only Title and Publisher
     colophon/imprint like "TAN", "Penguin", "Dover", "Everyman", "Oxford" are present), use
     bibliographic knowledge to INFER the canonical author (e.g. "TOTAL CONSECRATION" published
     by "TAN" -> "St. Louis de Montfort").
   - Populate `authors` with the inferred canonical author(s) whenever the title and publisher
     imprint clearly identify the canonical work, rather than leaving `authors` empty or defaulting
     to ["Unknown"].
"""

MULTI_ANGLE_BOOK_SYSTEM_PROMPT = """\
You are an expert antiquarian and commercial book cataloger.
You are provided multiple photographs of the SAME SINGLE BOOK taken from multiple angles
(e.g. spine, front cover, title page, copyright/colophon page, back cover).
Analyze all provided images TOGETHER and synthesize a single authoritative bibliographic record
for this volume in the `books` array (with exactly 1 book entry in the array).

SYNTHESIS GUIDELINES:
1. TITLE & SUBTITLE:
   - Extract the complete canonical title and subtitle, reconciling what is visible on the spine,
     front cover, and title page.
2. AUTHORS:
   - Identify all primary authors, editors, translators, or illustrators stated on the title page or cover.
3. PUBLISHER & IMPRINT:
   - Extract the exact publishing house and imprint (e.g. "Arkham House", "Gnome Press",
     "Doubleday", "Faber & Faber", "Penguin").
4. BINDING & FORMAT:
   - Determine whether it is hardcover_with_jacket, hardcover_no_jacket, trade_paperback, or
     mass_market_paperback by inspecting all angles.
5. EDITION & PRINTING IDENTIFICATION:
   - Inspect the title page and copyright page for edition points: "First Edition", "First Printing",
     publisher numberline (e.g. "1 2 3 4 5 6 7 8 9 10"), printing date, or original copyright year.
     Record these in `edition_or_series_markers`.
6. PHYSICAL CONDITION:
   - Aggregate all visible condition flaws across every angle: sunned_faded_spine,
     creased_reading_spine, bumped_headcap_or_tail, torn_or_chipped_jacket,
     ex_library_stickers_or_tape, remainder_mark_visible, cocked_spine, or clean_as_new.
7. TRANSCRIPTION:
   - Record key transcribed text from the spine and title page in `raw_spine_text`. Set confidence to "high".

8. COPYRIGHT & NUMBERLINE TRANSCRIPTION:
   - Verbatim transcribe all text on the copyright or colophon page, especially printing statements,
     date codes, and numberline sequences (e.g. "First Edition", "2 4 6 8 9 7 5 3",
     "First Published in 1954") into `raw_copyright_text`.

9. DUST JACKET FLAP & PRICE:
   - If the dust jacket front flap is visible, transcribe the printed retail price (e.g. "$16.95")
     or note the absence of price into `raw_flap_text`.
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
                    "raw_copyright_text": {"type": "string"},
                    "raw_flap_text": {"type": "string"},
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

# Shelf photos can contain dozens of spines.  Keep the response schema small:
# verbose per-book fields (condition, raw transcription, publisher, edition)
# multiply into thousands of unnecessary output tokens and can truncate the
# structured response before Gemini closes the JSON object.
BOOK_SHELF_SYSTEM_PROMPT = """\
Catalog every visible book spine in the shelf photo. Read one shelf row at a time,
left to right and top to bottom. Return one record per physical book, including
repeated titles as separate books. Give each book a normalized [top,left,bottom,right]
box from 0 to 1000. Transcribe only the title and author when legible; infer a
well-known author only when the title clearly identifies the work. Use format
unknown unless the binding is clear. Keep records concise; do not return raw text,
condition notes, publisher names, or edition notes. In the JSON, `u` is only the
number of visible spines that you could not include as a record; it is not the total
number of books. Set it to 0 when every visible spine has a record.
"""

BOOK_SHELF_GEMINI_SCHEMA = {
    "type": "object",
    "properties": {
        "b": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "x": {"type": "array", "items": {"type": "integer"}},
                    "t": {"type": "string"},
                    "a": {"type": "array", "items": {"type": "string"}},
                    "f": {
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
                    "q": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["n", "x", "t", "a", "f", "q"],
            },
        },
        "u": {"type": "integer"},
    },
    "required": ["b", "u"],
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

    raise ValueError("No Gemini API key found. Set GEMINI_API_KEY in environment or provide directly.")


def prepare_vision_image(image_bytes: bytes, raw_mime: str | None = None) -> tuple[bytes, str]:
    """Sanitizes image bytes, auto-orients EXIF, downscales massive dimensions, and detects clean MIME type."""
    import io

    from PIL import Image, ImageOps

    if not image_bytes:
        raise ValueError("image_bytes cannot be empty")

    cleaned_mime = (raw_mime or "").split(";")[0].strip().lower()

    # Magic byte inspection
    if len(image_bytes) >= 3 and image_bytes[:3] == b"\xff\xd8\xff":
        detected_mime = "image/jpeg"
    elif len(image_bytes) >= 8 and image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        detected_mime = "image/png"
    elif len(image_bytes) >= 12 and image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        detected_mime = "image/webp"
    elif len(image_bytes) >= 12 and image_bytes[4:8] == b"ftyp":
        brand = image_bytes[8:12]
        if brand in (b"heic", b"heix", b"heim", b"heis", b"mif1", b"msf1", b"hevc", b"hevx"):
            detected_mime = "image/heic"
        elif brand in (b"avif", b"avis"):
            detected_mime = "image/avif"
        else:
            detected_mime = cleaned_mime if cleaned_mime in ("image/heic", "image/heif") else "image/jpeg"
    else:
        if cleaned_mime in ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"):
            detected_mime = cleaned_mime
        elif cleaned_mime in ("image/jpg", "image/pjpeg"):
            detected_mime = "image/jpeg"
        else:
            detected_mime = "image/jpeg"

    # Pillow normalization & EXIF auto-rotation
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            max_dim = 1600
            if img.width > max_dim or img.height > max_dim:
                img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=85, optimize=True)
            return buf.getvalue(), "image/jpeg"
    except Exception as exc:
        logger.debug(f"Pillow normalization skipped: {exc}")
        safe_mime = (
            detected_mime
            if detected_mime in ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")
            else "image/jpeg"
        )
        return image_bytes, safe_mime


class BookVisionProvider(Protocol):
    """Protocol for book stack vision extraction providers."""

    def extract_stack(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> BookStackExtraction: ...

    def extract_multi_angle(self, images: list[tuple[bytes, str]]) -> BookStackExtraction: ...


@dataclass
class GeminiBookVisionProvider(BookVisionProvider):
    """High-reliability client for Google Gemini Vision with structured schema output and model cascade."""

    api_key: str
    model: str = "gemini-flash-lite-latest"
    fallback_models: list[str] = field(
        default_factory=lambda: [
            "gemini-flash-lite-latest",
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash-lite",
        ]
    )
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout: float = 25.0
    retries: RetryPolicy = field(default_factory=lambda: RetryPolicy(max_attempts=2, base_delay=0.3, max_delay=1.5))
    usage: UsageLedger = field(default_factory=UsageLedger)

    def extract_stack(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> BookStackExtraction:
        if not image_bytes:
            raise ValueError("image_bytes cannot be empty")

        valid_mimes = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
        clean_mime = (mime_type or "image/jpeg").split(";")[0].strip().lower()
        if clean_mime in ("image/jpg", "image/pjpeg"):
            clean_mime = "image/jpeg"
        if clean_mime not in valid_mimes:
            clean_mime = "image/jpeg"

        b64_img = base64.b64encode(image_bytes).decode("ascii")

        body: dict[str, Any] = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": clean_mime,
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
                "maxOutputTokens": 8192,
            },
        }

        # Model cascade: try primary model, then fallbacks
        models_to_try = [self.model] if self.model not in self.fallback_models else []
        models_to_try.extend(self.fallback_models)
        seen: set[str] = set()
        unique_models = [m for m in models_to_try if not (m in seen or seen.add(m))]

        last_error: Exception | None = None
        for model in unique_models:
            url = f"{self.base_url.rstrip('/')}/models/{model}:generateContent?key={self.api_key}"
            try:
                raw_response = self._post_with_retry(url, body)
                res = self._parse_gemini_response(raw_response)

                # Undercount safeguard: If only 1 book was extracted on an image that looks like a stack or shelf
                if len(res.books) <= 1 and res.unidentified_spines_count > 0:
                    retry_body = {
                        "contents": [
                            {
                                "parts": [
                                    {
                                        "inline_data": {
                                            "mime_type": clean_mime,
                                            "data": b64_img,
                                        }
                                    },
                                    {
                                        "text": BOOK_STACK_SYSTEM_PROMPT
                                        + (
                                            "\n\nCRITICAL MANDATE: Only 1 book was extracted on the previous attempt, "
                                            "but this image contains multiple books in a stack/shelf. You MUST scan "
                                            "every book spine across the entire image and return ALL books "
                                            "in the books array."
                                        ),
                                    },
                                ]
                            }
                        ],
                        "generationConfig": {
                            "response_mime_type": "application/json",
                            "response_schema": BOOK_STACK_GEMINI_SCHEMA,
                            "temperature": 0.15,
                            "maxOutputTokens": 8192,
                        },
                    }
                    try:
                        retry_resp = self._post_with_retry(url, retry_body)
                        retry_res = self._parse_gemini_response(retry_resp)
                        if len(retry_res.books) > len(res.books):
                            logger.info(
                                f"Undercount recovery: expanded from {len(res.books)} to {len(retry_res.books)} books"
                            )
                            return retry_res
                    except Exception as retry_exc:
                        logger.debug(f"Undercount retry skipped: {retry_exc}")

                return res
            except (TransientProviderError, TerminalProviderError, HTTPError) as exc:
                last_error = exc
                logger.warning(f"Gemini model {model} failed: {exc}, trying next model in cascade...")
                continue

        if last_error:
            raise last_error
        raise TransientProviderError("All Gemini vision models failed")

    def extract_shelf_stack(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> BookStackExtraction:
        """Extract a dense shelf using a compact schema to stay within output limits."""
        if not image_bytes:
            raise ValueError("image_bytes cannot be empty")
        clean_mime = (mime_type or "image/jpeg").split(";")[0].strip().lower()
        if clean_mime in ("image/jpg", "image/pjpeg"):
            clean_mime = "image/jpeg"
        if clean_mime not in {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}:
            clean_mime = "image/jpeg"
        body = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": clean_mime,
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            }
                        },
                        {"text": BOOK_SHELF_SYSTEM_PROMPT},
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": BOOK_SHELF_GEMINI_SCHEMA,
                "temperature": 0.1,
                "maxOutputTokens": 8192,
            },
        }
        models = [self.model] if self.model not in self.fallback_models else []
        models.extend(self.fallback_models)
        last_error: Exception | None = None
        for model in dict.fromkeys(models):
            url = f"{self.base_url.rstrip('/')}/models/{model}:generateContent?key={self.api_key}"
            try:
                raw = self._post_with_retry(url, body)
                candidate = (raw.get("candidates") or [{}])[0]
                if candidate.get("finishReason") == "MAX_TOKENS":
                    raise TerminalProviderError("Gemini shelf response reached its output limit")
                parts = candidate.get("content", {}).get("parts", [])
                parsed = json.loads(parts[0].get("text", ""))
                normalized = {
                    "books": [
                        {
                            "stack_order": book.get("n", i + 1),
                            "box_2d": book.get("x", [0, 0, 0, 0]),
                            "title": book.get("t", ""),
                            "authors": book.get("a", []),
                            "format": book.get("f", "unknown"),
                            "confidence": book.get("q", "low"),
                        }
                        for i, book in enumerate(parsed.get("b", []))
                    ],
                    "orientation": "vertical_shelf",
                    "unidentified_spines_count": parsed.get("u", 0),
                }
                return BookStackExtraction.from_dict(normalized)
            except (
                TransientProviderError,
                TerminalProviderError,
                HTTPError,
                json.JSONDecodeError,
                IndexError,
                TypeError,
                ValueError,
            ) as exc:
                last_error = exc
                logger.warning("Gemini compact shelf extraction failed", model=model, error=str(exc))
        if last_error:
            raise last_error
        raise TransientProviderError("All Gemini shelf models failed")

    def extract_multi_angle(self, images: list[tuple[bytes, str]]) -> BookStackExtraction:
        """Synthesizes multiple photos of a single book (spine, cover, title/copyright page)."""
        if not images:
            raise ValueError("images list cannot be empty")
        if len(images) == 1:
            return self.extract_stack(images[0][0], images[0][1])

        valid_mimes = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
        parts: list[dict[str, Any]] = []
        for img_bytes, raw_mime in images:
            if not img_bytes:
                continue
            clean_mime = (raw_mime or "image/jpeg").split(";")[0].strip().lower()
            if clean_mime in ("image/jpg", "image/pjpeg"):
                clean_mime = "image/jpeg"
            if clean_mime not in valid_mimes:
                clean_mime = "image/jpeg"
            b64_img = base64.b64encode(img_bytes).decode("ascii")
            parts.append(
                {
                    "inline_data": {
                        "mime_type": clean_mime,
                        "data": b64_img,
                    }
                }
            )

        if not parts:
            raise ValueError("No valid image data provided for multi-angle extraction")

        parts.append({"text": MULTI_ANGLE_BOOK_SYSTEM_PROMPT})

        body: dict[str, Any] = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": BOOK_STACK_GEMINI_SCHEMA,
                "temperature": 0.1,
                "maxOutputTokens": 8192,
            },
        }

        models_to_try = [self.model] if self.model not in self.fallback_models else []
        models_to_try.extend(self.fallback_models)
        seen: set[str] = set()
        unique_models = [m for m in models_to_try if not (m in seen or seen.add(m))]

        last_error: Exception | None = None
        for model in unique_models:
            url = f"{self.base_url.rstrip('/')}/models/{model}:generateContent?key={self.api_key}"
            try:
                raw_response = self._post_with_retry(url, body)
                return self._parse_gemini_response(raw_response)
            except (TransientProviderError, TerminalProviderError, HTTPError) as exc:
                last_error = exc
                logger.warning(f"Gemini model {model} failed in multi-angle: {exc}, trying next in cascade...")
                continue

        if last_error:
            raise last_error
        raise TransientProviderError("All Gemini vision models failed for multi-angle extraction")

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
                elif exc.code in (503, 404):
                    # Fail fast so model cascade can try the next candidate model
                    raise TransientProviderError(f"Gemini HTTP {exc.code}") from exc
                elif exc.code in RETRYABLE_STATUS:
                    if attempt >= self.retries.max_attempts:
                        raise TransientProviderError(f"Gemini HTTP {exc.code}") from exc
                else:
                    raise TerminalProviderError(f"Gemini HTTP {exc.code}") from exc

                sleep_sec = self.retries.backoff(attempt)
                logger.info(f"Gemini request failed (attempt {attempt}), retrying in {sleep_sec:.2f}s")
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

        text = parts[0].get("text", "").strip()
        if not text:
            raise TerminalProviderError("Empty text payload from Gemini candidate")

        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

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
