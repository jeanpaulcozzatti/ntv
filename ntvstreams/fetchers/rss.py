"""RSS / Atom fetcher with conditional requests."""

from __future__ import annotations

import calendar
from datetime import datetime, timezone

import feedparser
import httpx

from ..canonical import canonicalize, ensure_utc, item_id, make_excerpt
from ..models import Item, SourceState
from ..sources import Source
from .base import FetchResult, failed, get, make_resolver, ok_state


def _entry_datetime(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
    return None


def _entry_body(entry) -> str | None:
    content = entry.get("content")
    if content:
        return content[0].get("value")
    return entry.get("summary")


def _is_google_news(source: Source) -> bool:
    return "news.google.com" in (source.url or "")


def entry_to_item(entry, source: Source, now: datetime, resolve=None) -> Item | None:
    link = (entry.get("link") or "").strip()
    if not link:
        return None
    canonical = canonicalize(link, resolve=resolve)
    title = " ".join((entry.get("title") or "").split()) or f"{source.name} item"
    gnews = _is_google_news(source)
    if gnews:
        # Google News appends " - Outlet Name" to every title; the <source> element carries the same name.
        outlet = (entry.get("source") or {}).get("title") or source.name
        if title.endswith(f" - {outlet}"):
            title = title[: -len(outlet) - 3].rstrip()
    raw = {k: entry.get(k) for k in ("link", "title", "author", "summary", "published") if entry.get(k)}
    if gnews:
        raw["outlet"] = outlet  # the real publisher; the page shows this instead of "Google News"
    return Item(
        id=item_id(canonical),
        source_id=source.id,
        url=link,
        canonical_url=canonical,
        kind="article",
        title=title,
        author=entry.get("author") or None,
        excerpt=None if gnews else make_excerpt(_entry_body(entry)),
        published_at=ensure_utc(_entry_datetime(entry), now),
        fetched_at=now,
        raw=raw,
    )


class RssFetcher:
    def fetch(self, source: Source, state: SourceState, http: httpx.Client, now: datetime) -> FetchResult:
        headers: dict[str, str] = {}
        if state.etag:
            headers["If-None-Match"] = state.etag
        if state.last_modified:
            headers["If-Modified-Since"] = state.last_modified
        try:
            resp = get(http, source.url, headers)
        except httpx.HTTPError as e:
            return failed(state, f"network: {e.__class__.__name__}: {e}")
        if resp.status_code == 304:
            return FetchResult(items=[], state=ok_state(state, now))
        if resp.status_code >= 400:
            return failed(state, f"http {resp.status_code}")
        parsed = feedparser.parse(resp.content)
        if not parsed.entries:
            return failed(state, "no entries (not a feed, or empty)")
        resolve = make_resolver(http)
        items = [it for e in parsed.entries if (it := entry_to_item(e, source, now, resolve))]
        new_state = ok_state(
            state, now,
            etag=resp.headers.get("ETag") or state.etag,
            last_modified=resp.headers.get("Last-Modified") or state.last_modified,
        )
        return FetchResult(items=items, state=new_state)
