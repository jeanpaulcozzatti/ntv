"""Source definitions loaded from sources.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

Kind = Literal["rss", "bluesky", "reddit", "youtube", "wordpress"]
Desk = Literal["news", "culture", "choctaw", "pnw", "any"]


class Source(BaseModel):
    id: str
    name: str
    kind: Kind
    url: str | None = None
    query: str | None = None
    actor: str | None = None
    subreddit: str | None = None
    channel_id: str | None = None
    tier: Literal[1, 2, 3, 4]
    desk_hint: Desk = "any"
    active: bool = True

    @model_validator(mode="after")
    def _check_kind_fields(self) -> "Source":
        if self.kind == "rss" and not self.url:
            raise ValueError("rss source needs url")
        if self.kind == "wordpress" and not self.url:
            raise ValueError("wordpress source needs url (the REST collection, e.g. https://site/wp-json/wp/v2/posts)")
        if self.kind == "bluesky" and bool(self.query) == bool(self.actor):
            raise ValueError("bluesky source needs exactly one of query or actor")
        if self.kind == "reddit" and not self.subreddit:
            raise ValueError("reddit source needs subreddit")
        if self.kind == "youtube" and not self.channel_id:
            raise ValueError("youtube source needs channel_id")
        return self


def load_sources(path: Path = Path("sources.yaml")) -> list[Source]:
    data = yaml.safe_load(path.read_text()) or {}
    return [Source.model_validate(s) for s in data.get("sources", [])]
