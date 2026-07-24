from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.media.models import AdDraft


@dataclass(frozen=True)
class RenderedMedia:
    draft_id: str
    output_uri: str
    dry_run: bool


class MediaRenderer(Protocol):
    def render(self, draft: AdDraft) -> RenderedMedia: ...


class DisabledMediaRenderer:
    """Prevents accidental file uploads or external publishing by default."""

    def render(self, draft: AdDraft) -> RenderedMedia:
        draft.validate()
        raise RuntimeError("media rendering is disabled until an approved renderer is configured")


class DryRunMediaRenderer:
    def render(self, draft: AdDraft) -> RenderedMedia:
        draft.validate()
        return RenderedMedia(draft.draft_id, f"dry-run://media/{draft.draft_id}.mp4", True)
