"""Resolve YouTube links to the channel's @handle with YouTube's public oEmbed lookup (no key).

Best effort and bounded: a failed lookup leaves the item as it was, and a run makes at most
MAX_LOOKUPS calls. A resolved handle is stored on the item (raw["channel_handle"]) and reused on later
runs, so each video is looked up once.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime
from urllib.parse import unquote, urlsplit

import httpx

from ..models import Item
from ..sources import Source
from ..store import Store
from .base import USER_AGENT

OEMBED = "https://www.youtube.com/oembed"
YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
MAX_LOOKUPS = 40
_HANDLE = re.compile(r"/@([^/?#]+)")


def is_youtube_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return host in YOUTUBE_HOSTS


def lookup_handle(http: httpx.Client, url: str) -> str | None:
    try:
        r = http.get(OEMBED, params={"url": url, "format": "json"}, headers={"User-Agent": USER_AGENT}, timeout=5.0)
        if r.status_code != 200:
            return None
        m = _HANDLE.search(r.json().get("author_url") or "")
        return unquote(m.group(1)) if m else None
    except Exception:  # enrichment must never break a fetch
        return None


def add_channel_handles(items: list[Item], source: Source | None, store: Store, http: httpx.Client, budget: int = MAX_LOOKUPS) -> list[Item]:
    targets = [it for it in items if is_youtube_url(it.url) and not it.raw.get("channel_handle")]
    if not targets:
        return items
    stored = store.get_items([it.id for it in targets])
    channel_handle: str | None = None  # a YouTube source's own videos all share one channel
    handles: dict[str, str] = {}
    for it in targets:
        handle = (stored[it.id].raw or {}).get("channel_handle") if it.id in stored else None
        own_feed = source is not None and source.kind == "youtube"
        if not handle and own_feed:
            handle = channel_handle
        if not handle and budget > 0:
            budget -= 1
            handle = lookup_handle(http, it.url)
            if handle and own_feed:
                channel_handle = handle
        if handle:
            handles[it.id] = handle
    return [replace(it, raw={**it.raw, "channel_handle": handles[it.id]}) if it.id in handles else it for it in items]


def backfill_channel_handles(sources: list[Source], store: Store, http: httpx.Client, now: datetime, hours: int = 48, limit: int = MAX_LOOKUPS) -> int:
    """Resolve handles for stored YouTube items that predate this feature or whose own feed is unreachable. Returns how many gained one."""
    by_id = {s.id: s for s in sources}
    stale = [it for it in store.items_in_window(now, hours) if is_youtube_url(it.url) and not (it.raw or {}).get("channel_handle")][:limit]
    groups: dict[str, list[Item]] = {}
    for it in stale:
        groups.setdefault(it.source_id, []).append(it)
    gained = [it for sid, group in groups.items() for it in add_channel_handles(group, by_id.get(sid), store, http, limit) if it.raw.get("channel_handle")]
    if gained:
        store.upsert_items(gained)
    return len(gained)
