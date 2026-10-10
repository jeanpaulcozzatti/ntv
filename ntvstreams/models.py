"""Shared dataclasses used by every stage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

ItemKind = Literal["article", "post", "video"]
DeskName = Literal["news", "culture"]
Role = Literal["lead", "coverage", "discussion"]


@dataclass(frozen=True)
class Item:
    id: str
    source_id: str
    url: str
    canonical_url: str
    kind: ItemKind
    title: str
    author: str | None
    excerpt: str | None
    published_at: datetime
    fetched_at: datetime
    engagement: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Story:
    id: str
    run_id: str
    desk: DeskName
    headline: str
    lead_item_id: str
    rank: int
    rationale: str
    created_at: datetime
    updated_at: datetime
    expired_at: datetime | None = None


@dataclass(frozen=True)
class StoryMember:
    story_id: str
    item_id: str
    role: Role


@dataclass(frozen=True)
class SourceState:
    source_id: str
    etag: str | None = None
    last_modified: str | None = None
    last_success_at: datetime | None = None
    consecutive_fails: int = 0
    last_error: str | None = None


@dataclass
class Run:
    id: str
    started_at: datetime
    finished_at: datetime | None = None
    stage_reached: str = "fetch"
    items_new: int = 0
    curated: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
