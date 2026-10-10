"""YouTube channel fetcher over the public Atom feed (no auth)."""

from __future__ import annotations

from datetime import datetime

import feedparser
import httpx

from ..canonical import canonicalize, ensure_utc, item_id, make_excerpt
from ..models import Item, SourceState
from ..sources import Source
from .base import FetchResult, failed, get, ok_state
from .rss import _entry_datetime

FEED = "https://www.youtube.com/feeds/videos.xml"


def _views(entry) -> int:
    stats = entry.get("media_statistics") or {}
    try:
        return int(stats.get("views") or 0)
    except (TypeError, ValueError):
        return 0


def entry_to_item(entry, source: Source, now: datetime) -> Item | None:
    link = (entry.get("link") or "").strip()
    if not link:
        return None
    canonical = canonicalize(link)
    return Item(
        id=item_id(canonical),
        source_id=source.id,
        url=link,
        canonical_url=canonical,
        kind="video",
        title=" ".join((entry.get("title") or "").split()) or f"{source.name} video",
        author=entry.get("author") or None,
        excerpt=make_excerpt(entry.get("media_description") or entry.get("summary")),
        published_at=ensure_utc(_entry_datetime(entry), now),
        fetched_at=now,
        engagement=_views(entry),
        raw={"video_id": entry.get("yt_videoid"), "title": entry.get("title")},
    )


class YoutubeFetcher:
    def fetch(self, source: Source, state: SourceState, http: httpx.Client, now: datetime) -> FetchResult:
        url = httpx.URL(FEED, params={"channel_id": source.channel_id})
        try:
            resp = get(http, str(url))
        except httpx.HTTPError as e:
            return failed(state, f"network: {e.__class__.__name__}: {e}")
        if resp.status_code >= 400:
            return failed(state, f"http {resp.status_code}")
        parsed = feedparser.parse(resp.content)
        if not parsed.entries:
            return failed(state, "no entries (not a feed, or empty)")
        items = [it for e in parsed.entries if (it := entry_to_item(e, source, now))]
        return FetchResult(items=items, state=ok_state(state, now))
