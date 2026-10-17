# Shelf photo intake

Shelf capture turns several views of one bookcase into one review draft. Use a
wide photo to show the row and its neighbours, then add close-ups where titles,
editions, or condition are hard to read. Photos may overlap or show separate
parts of the shelf.

For reliable intake, aim for **20–30 readable spines per photo** and keep each
view to **40 or fewer** where possible. Include a wide overview for context, then
use overlapping close-ups for readable titles; overlap by a few neighbouring
books. The app still processes every uploaded view. If it detects more than 40
sightings in one photo, it flags that photo for extra review rather than dropping
records. This is a conservative operating guideline; recognition accuracy has
not yet been measured across a representative set of real stores.

## Review flow

1. In the dashboard, choose **One shelf, several photos** and add up to 12
   photos (12 MiB each). The service extracts up to 400 book sightings and
   keeps the source photo, crop, and each reading together.
2. Review the proposed physical-copy groups. Neighbouring titles and spine
   positions can support a match across views. A title match on its own stays
   an unresolved possible overlap. Identical photo uploads can suggest matching
   sightings at the same position.
3. For every sighting, choose a separate copy, the same copy as another photo,
   or **not a book** for a false detection. Two sightings from the same photo
   cannot be combined: they are two visible physical copies, even when their
   titles and ISBNs match.
4. Confirm the resulting copy count and approve the shelf draft. Approval only
   authorizes creation of one pending book review per confirmed copy. Execute
   that step separately; each book review then follows the normal detail review
   and approval flow. This feature does not register inventory or publish a
   listing.

Suggested groups are editable. The reviewer must account for every sighting
exactly once and explicitly confirm the count. Conflicting edition or format
readings remain separate suggestions. Close-up readings can fill missing fields
in a book review; source readings and their crops remain attached as evidence.

## Limits and data handling

This is evidence matching, not panorama construction or pixel stitching. It
uses normalized spine boxes, unique neighbouring title anchors, and conservative
position alignment to suggest that sightings in different photos are the same
copy. Repeated titles do not serve as alignment anchors. Strong perspective,
rotation, occlusion, or too few shared neighbours can leave a real overlap
uncertain; inspect the photos and keep copies separate when identity is unclear.
The flow does not match a capture against existing inventory or another capture.

The authenticated review payload retains private JPEG previews, per-sighting
crops, extraction readings, and source-photo names so the operator can compare
evidence. They follow the configured review-store retention and access controls.
Do not upload photographs that should not be retained there.

The dashboard defaults to `shelf_capture`. The existing `/intake/camera` modes
`multi_stack` and `multi_angle_single_book` remain available for older clients;
the older stack mode does not provide shelf-level physical-copy review.

## Decision payload

The shelf review update consists only of confirmed groups, false detections,
and the explicit count confirmation. Every observation ID from the proposal
must occur once in `copy_groups` or `excluded_observations`:

```json
{
  "copy_groups": [["p1-b1", "p2-b2"], ["p1-b2"]],
  "excluded_observations": [],
  "copy_count_confirmed": true
}
```

The example says `p1-b1` and `p2-b2` are one physical copy, while `p1-b2` is a
second copy. Once executed, child review IDs are deterministic for that
capture and grouping, so a repeated execution cannot create duplicate reviews.
If child creation partially fails, reconcile the failed parent using its
recorded result and existing review rows; execution failures are not retried
automatically.

The automated suite exercises memory, SQLite, and PostgreSQL store paths. The
local dashboard was also exercised with synthetic photos, including the
same-photo merge guard, narrow-screen layout, parent approval, and child review
creation. Synthetic QA verifies the review workflow; it does not measure title
recognition quality on a real bookcase. Review a real capture before relying on
its extraction results.
