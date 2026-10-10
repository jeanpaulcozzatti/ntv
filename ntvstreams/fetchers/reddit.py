"""Reddit fetcher over the public Atom feed. Reddit blocks the JSON listing for non-browser clients."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit

import feedparser
import httpx

from ..canonical import canonicalize, ensure_utc, item_id
from ..models import Item, SourceState
from ..sources import Source
from .base import FetchResult, failed, get, make_resolver, ok_state
from .rss import _entry_datetime

REDDIT_HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com", "redd.it", "i.redd.it", "v.redd.it", "preview.redd.it"}
_LINK_RE = re.compile(r'href="([^"]+)">\s*\[link\]', re.I)


def _subreddit(entry) -> str | None:
    for tag in entry.get("tags") or []:
        term = tag.get("term")
        if term:
            return term
    return None


def entry_to_items(entry, source: Source, now: datetime, resolve=None) -> tuple[list[Item], list[tuple[str, str]]]:
    url = (entry.get("link") or "").strip()
    canonical = canonicalize(url)
    published = ensure_utc(_entry_datetime(entry), now)
    title = " ".join((entry.get("title") or "").split()) or f"{source.name} post"
    author = (entry.get("author") or "").removeprefix("/u/") or None
    post = Item(
        id=item_id(canonical), source_id=source.id, url=url, canonical_url=canonical, kind="post",
        title=title, author=author, excerpt=None, published_at=published, fetched_at=now,
        raw={"subreddit": _subreddit(entry) or source.subreddit, "id": entry.get("id")},
    )
    items, links = [post], []
    content = entry.content[0].value if entry.get("content") else (entry.get("summary") or "")
    m = _LINK_RE.search(content)
    target = m.group(1) if m else ""
    host = (urlsplit(target).hostname or "").lower()
    if target and host and host not in REDDIT_HOSTS:
        a_canon = canonicalize(target, resolve=resolve)
        article = Item(
            id=item_id(a_canon), source_id=source.id, url=target, canonical_url=a_canon, kind="article",
            title=title, author=None, excerpt=None, published_at=published, fetched_at=now,
            raw={"via": url},
        )
        items.append(article)
        links.append((post.id, article.id))
    return items, links


class RedditFetcher:
    def fetch(self, source: Source, state: SourceState, http: httpx.Client, now: datetime) -> FetchResult:
        url = httpx.URL(f"https://www.reddit.com/r/{source.subreddit}/new.rss", params={"limit": 50})
        try:
            resp = get(http, str(url))
        except httpx.HTTPError as e:
            return failed(state, f"network: {e.__class__.__name__}: {e}")
        if resp.status_code >= 400:
            return failed(state, f"http {resp.status_code}")
        parsed = feedparser.parse(resp.content)
        if not parsed.entries:
            return failed(state, "no entries (not a feed, or empty)")
        resolve = make_resolver(http)
        items: list[Item] = []
        links: list[tuple[str, str]] = []
        for e in parsed.entries:
            if not e.get("link"):
                continue
            i, l = entry_to_items(e, source, now, resolve)
            items.extend(i)
            links.extend(l)
        return FetchResult(items=items, links=links, state=ok_state(state, now))
