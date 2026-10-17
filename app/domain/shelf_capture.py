"""Photo observations are evidence of copies, never copies merely because titles match."""

from __future__ import annotations

import base64
import copy
import hashlib
import io
from collections import Counter, defaultdict
from dataclasses import replace
from statistics import median
from typing import Any

from app.domain.books import BookFormat, SpineExtractionItem
from app.domain.intake_batch import BatchExtractionItem
from app.domain.intake_batch import BatchSpineDeduplicator as Text
from app.review.queue import ReviewItem

RECOMMENDED_SPINES_PER_PHOTO = 30
REVIEW_SPINES_PER_PHOTO = 40


def valid_box(box: tuple[int, int, int, int]) -> bool:
    return (
        len(box) == 4
        and all(isinstance(v, int) and 0 <= v <= 1000 for v in box)
        and box[0] < box[2]
        and box[1] < box[3]
    )


def compatible(a: SpineExtractionItem, b: SpineExtractionItem) -> bool:
    if Text.title_similarity(a.title, b.title) < 0.90:
        return False
    if not Text.normalize_title(a.title) or a.title.strip().lower() in ("unknown", "unknown title"):
        return False
    return not (a.authors and b.authors and not Text.authors_match(a.authors, b.authors)[0])


def metadata_conflicts(a: SpineExtractionItem, b: SpineExtractionItem) -> list[str]:
    conflicts = []
    if a.format != b.format and BookFormat.UNKNOWN not in (a.format, b.format):
        conflicts.append("different formats")
    if (
        a.publisher_imprint
        and b.publisher_imprint
        and Text.normalize_title(a.publisher_imprint) != Text.normalize_title(b.publisher_imprint)
    ):
        conflicts.append("different publisher readings")
    if (
        a.edition_or_series_markers
        and b.edition_or_series_markers
        and set(a.edition_or_series_markers) != set(b.edition_or_series_markers)
    ):
        conflicts.append("different edition readings")
    return conflicts


def _point(item: BatchExtractionItem) -> tuple[float, float]:
    y, x, yy, xx = item.book.box_2d
    return ((x + xx) / 2000, (y + yy) / 2000)


def _axis_fit(pairs: list[tuple[BatchExtractionItem, BatchExtractionItem]], axis: int) -> tuple[float, float] | None:
    xs = [_point(a)[axis] for a, b in pairs]
    ys = [_point(b)[axis] for a, b in pairs]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    variance = sum((x - mx) ** 2 for x in xs)
    if variance > 0.0001:
        scale = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / variance
    else:
        # A single shelf row has little vertical spread; estimate zoom from spine size.
        lo, hi = (1, 3) if axis == 0 else (0, 2)
        scale = median(
            (b.book.box_2d[hi] - b.book.box_2d[lo]) / (a.book.box_2d[hi] - a.book.box_2d[lo]) for a, b in pairs
        )
    offset = my - scale * mx
    if not 0.08 <= scale <= 12:
        return None
    if max(abs(scale * x + offset - y) for x, y in zip(xs, ys, strict=True)) > 0.035:
        return None
    return scale, offset


def _alignment(
    left: list[BatchExtractionItem], right: list[BatchExtractionItem]
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    # Unique titles anchor location, while repeated titles remain separate observations.
    la = Counter(Text.normalize_title(i.book.title) for i in left)
    rb = Counter(Text.normalize_title(i.book.title) for i in right)
    anchors = []
    for a in left:
        if not valid_box(a.book.box_2d) or la[Text.normalize_title(a.book.title)] != 1:
            continue
        matches = [
            b
            for b in right
            if valid_box(b.book.box_2d)
            and rb[Text.normalize_title(b.book.title)] == 1
            and compatible(a.book, b.book)
            and not metadata_conflicts(a.book, b.book)
        ]
        if len(matches) == 1:
            anchors.append((a, matches[0]))
    # A single matching title, even with the same ISBN, cannot establish physical identity.
    if len(anchors) < 3 or len({Text.normalize_title(a.book.title) for a, b in anchors}) < 3:
        return None
    x = _axis_fit(anchors, 0)
    y = _axis_fit(anchors, 1)
    return (x, y) if x and y else None


def _thumbnail(raw: bytes, box: tuple[int, int, int, int] | None = None) -> str | None:
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            if box and valid_box(box):
                y, x, yy, xx = box
                w, h = image.size
                image = image.crop(
                    (
                        int(x * w / 1000),
                        int(y * h / 1000),
                        max(int(xx * w / 1000), int(x * w / 1000) + 1),
                        max(int(yy * h / 1000), int(y * h / 1000) + 1),
                    )
                )
            image.thumbnail((480, 480) if box else (1400, 1400))
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=78)
            return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()
    except (OSError, ValueError):
        return None


def plan_capture(items: list[BatchExtractionItem], images: dict[int, bytes]) -> dict[str, Any]:
    if len(items) > 400:
        raise ValueError("Use a smaller shelf capture: at most 400 book sightings.")
    by_photo: dict[int, list[BatchExtractionItem]] = defaultdict(list)
    ids: dict[int, str] = {}
    observations: list[dict[str, Any]] = []
    for item in items:
        by_photo[item.source_image_index].append(item)
        oid = f"p{item.source_image_index + 1}-b{len(by_photo[item.source_image_index])}"
        ids[id(item)] = oid
        observations.append(
            {
                "id": oid,
                **item.to_dict(),
                "crop": _thumbnail(images.get(item.source_image_index, b""), item.book.box_2d)
                if valid_box(item.book.box_2d)
                else None,
            }
        )
    dense_photo_warnings = [
        {
            "image_index": photo_index,
            "filename": sightings[0].source_filename,
            "sighting_count": len(sightings),
            "message": (
                f"{len(sightings)} spines were detected. For clearer readings, split this view "
                f"into closer photos of about 20–{RECOMMENDED_SPINES_PER_PHOTO} books each. "
                "Review every sighting before approval."
            ),
        }
        for photo_index, sightings in sorted(by_photo.items())
        if len(sightings) > REVIEW_SPINES_PER_PHOTO
    ]
    groups = {o["id"]: {o["id"]} for o in observations}

    def group(oid: str) -> set[str]:
        return next(value for value in groups.values() if oid in value)

    pair_hints: list[dict[str, str]] = []
    possible: list[dict[str, str]] = []
    possible_count = 0
    photos = sorted(by_photo)
    for n, left_index in enumerate(photos):
        for right_index in photos[n + 1 :]:
            left, right = by_photo[left_index], by_photo[right_index]
            transform = _alignment(left, right)
            same_image = (
                bool(images.get(left_index))
                and hashlib.sha256(images[left_index]).digest() == hashlib.sha256(images.get(right_index, b"")).digest()
            )
            accepted: list[tuple[str, str]] = []
            for a in left:
                for b in right:
                    if not compatible(a.book, b.book):
                        continue
                    conflicts = metadata_conflicts(a.book, b.book)
                    supported = False
                    if not conflicts and valid_box(a.book.box_2d) and valid_box(b.book.box_2d):
                        if same_image:
                            supported = max(abs(x - y) for x, y in zip(a.book.box_2d, b.book.box_2d, strict=True)) <= 15
                        elif transform:
                            ax, ay = _point(a)
                            bx, by = _point(b)
                            dx = abs(transform[0][0] * ax + transform[0][1] - bx)
                            dy = abs(transform[1][0] * ay + transform[1][1] - by)
                            width = (b.book.box_2d[3] - b.book.box_2d[1]) / 1000
                            height = (b.book.box_2d[2] - b.book.box_2d[0]) / 1000
                            supported = dx <= max(0.006, min(0.025, width * 0.4)) and dy <= max(
                                0.006, min(0.025, height * 0.4)
                            )
                    aid, bid = ids[id(a)], ids[id(b)]
                    if supported:
                        accepted.append((aid, bid))
                    else:
                        possible_count += 1
                        if len(possible) < 200:
                            possible.append(
                                {
                                    "left": aid,
                                    "right": bid,
                                    "reason": ", ".join(conflicts) or "Title match only; overlap is unconfirmed.",
                                }
                            )
            # A sighting must not match two copies in another photo.
            ac = Counter(a for a, b in accepted)
            bc = Counter(b for a, b in accepted)
            for aid, bid in accepted:
                if ac[aid] != 1 or bc[bid] != 1:
                    continue
                ga, gb = group(aid), group(bid)
                photo_a = {o["source_image_index"] for o in observations if o["id"] in ga}
                photo_b = {o["source_image_index"] for o in observations if o["id"] in gb}
                if ga is not gb and not photo_a.intersection(photo_b):
                    key = next(k for k, v in groups.items() if v is gb)
                    ga.update(gb)
                    del groups[key]
                    pair_hints.append(
                        {
                            "left": aid,
                            "right": bid,
                            "reason": "Same image and spine position."
                            if same_image
                            else "Three or more neighbouring titles and spine positions align.",
                        }
                    )
    lookup = {o["id"]: o for o in observations}
    suggested = [
        sorted(
            g,
            key=lambda oid: (
                -Text.candidate_richness_score(SpineExtractionItem.from_dict(lookup[oid])),
                oid,
            ),
        )
        for g in groups.values()
    ]
    return {
        "observations": observations,
        "dense_photo_warnings": dense_photo_warnings,
        "suggested_groups": suggested,
        "overlap_hints": pair_hints,
        "possible_overlaps": possible,
        "possible_overlap_count": possible_count,
        "copy_count_confirmed": False,
        "copy_groups": [],
        "excluded_observations": [],
    }


def validate_decisions(payload: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(update, dict) or set(update) - {
        "copy_groups",
        "excluded_observations",
        "copy_count_confirmed",
    }:
        raise ValueError("Shelf review can change copy groups and exclusions only.")
    if update.get("copy_count_confirmed") is not True:
        raise ValueError("Check photo overlaps and confirm the physical copy count before approval.")
    observations = payload.get("observations", [])
    lookup = {o["id"]: o for o in observations}
    groups = update.get("copy_groups", [])
    excluded = update.get("excluded_observations", [])
    if not isinstance(groups, list) or not isinstance(excluded, list):
        raise ValueError("Invalid shelf copy groups.")
    seen = []
    for group in groups:
        if not isinstance(group, list) or not group or not all(isinstance(oid, str) and oid in lookup for oid in group):
            raise ValueError("Each copy needs known photo observations.")
        photos = [lookup[oid]["source_image_index"] for oid in group]
        if len(set(photos)) != len(photos):
            raise ValueError("Two spines in the same photo are separate physical copies.")
        seen.extend(group)
    if not all(isinstance(oid, str) and oid in lookup for oid in excluded):
        raise ValueError("Unknown excluded observation.")
    seen.extend(excluded)
    if len(seen) != len(set(seen)) or set(seen) != set(lookup):
        raise ValueError("Every sighting must belong to exactly one copy or be marked not a book.")
    return update


def _enrich(books: list[SpineExtractionItem]) -> SpineExtractionItem:
    primary = books[0]

    def first(attr: str) -> Any:
        return next((getattr(b, attr) for b in books if getattr(b, attr)), getattr(primary, attr))

    cues = tuple(dict.fromkeys(cue for b in books for cue in b.visible_condition_cues))
    if len(cues) > 1:
        cues = tuple(c for c in cues if c != "clean_as_new")
    return replace(
        primary,
        subtitle=first("subtitle"),
        publisher_imprint=first("publisher_imprint"),
        authors=next(
            (
                tuple(a for a in b.authors if a.strip().lower() != "unknown")
                for b in books
                if any(a.strip().lower() != "unknown" for a in b.authors)
            ),
            (),
        ),
        visible_condition_cues=cues,
        edition_or_series_markers=first("edition_or_series_markers"),
        raw_copyright_text=first("raw_copyright_text"),
        raw_flap_text=first("raw_flap_text"),
        format=next((b.format for b in books if b.format != BookFormat.UNKNOWN), primary.format),
    )


def create_book_reviews(store: Any, payload: dict[str, Any], tenant: int) -> dict[str, Any]:
    validate_decisions(
        payload,
        {k: payload.get(k) for k in ("copy_groups", "excluded_observations", "copy_count_confirmed")},
    )
    lookup = {o["id"]: o for o in payload["observations"]}
    result: list[str] = []
    for group in payload["copy_groups"]:
        observations = [lookup[oid] for oid in group]
        primary = observations[0]
        child = copy.deepcopy(primary["analysis"])
        merged = _enrich([SpineExtractionItem.from_dict(o) for o in observations])
        inferred_authors = child["extraction"].get("authors", [])
        child["extraction"].update(merged.to_dict())
        if not merged.authors:
            child["extraction"]["authors"] = inferred_authors
        child["extraction"]["source_observations"] = [
            {k: v for k, v in o.items() if k not in ("analysis", "crop")} for o in observations
        ]
        child["shelf_evidence"] = {
            "capture_id": payload["capture_id"],
            "observations": observations,
            "copy_count_confirmed": True,
        }
        digest = hashlib.sha256((payload["capture_id"] + "|" + ",".join(sorted(group))).encode()).hexdigest()[:20]
        rid = "shelf-book-" + digest
        old = store.get(rid, tenant_id=tenant)
        if old is None:
            try:
                store.submit(ReviewItem(rid, "intake_book", child), tenant_id=tenant)
            except ValueError:
                if store.get(rid, tenant_id=tenant) is None:
                    raise
        result.append(rid)
    return {
        "status": "book_reviews_created",
        "copy_count": len(result),
        "review_ids": result,
        "message": "Book details still need review. No inventory was registered or published.",
    }
