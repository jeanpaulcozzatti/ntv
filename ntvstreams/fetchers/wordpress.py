"""WordPress REST fetcher, for sites whose RSS feed is off but whose JSON API is open.

`source.url` is the REST collection, for example https://www.choctawnation.com/wp-json/wp/v2/news.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

import httpx

from ..canonical import canonicalize, item_id, make_excerpt
from ..models import Item, SourceState
from ..sources import Source
from .base import FetchResult, failed, get, ok_state

PER_PAGE = 30
FIELDS = "id,date_gmt,link,title,excerpt"


def _rendered(post: dict, key: str) -> str:
    value = post.get(key)
    if isinstance(value, dict):
        value = value.get("rendered")
    return value if isinstance(value, str) else ""


def post_to_item(post: dict, source: Source, now: datetime) -> Item | None:
    link = (post.get("link") or "").strip()
    if not link:
        return None
    canonical = canonicalize(link)
    title = " ".join(html.unescape(_rendered(post, "title")).split()) or f"{source.name} item"
    published = now
    raw_date = post.get("date_gmt")
    if isinstance(raw_date, str) and raw_date:
        try:
            published = datetime.fromisoformat(raw_date).replace(tzinfo=timezone.utc)  # date_gmt is UTC with no offset
        except ValueError:
            pass
    return Item(
        id=item_id(canonical),
        source_id=source.id,
        url=link,
        canonical_url=canonical,
        kind="article",
        title=title,
        author=None,
        excerpt=make_excerpt(_rendered(post, "excerpt")) or None,
        published_at=published,
        fetched_at=now,
        raw={"link": link, "title": title},
    )


class WordpressFetcher:
    def fetch(self, source: Source, state: SourceState, http: httpx.Client, now: datetime) -> FetchResult:
        url = str(httpx.URL(source.url).copy_merge_params({"per_page": PER_PAGE, "orderby": "date", "order": "desc", "_fields": FIELDS}))
        try:
            resp = get(http, url)
        except httpx.HTTPError as e:
            return failed(state, f"network: {e.__class__.__name__}: {e}")
        if resp.status_code >= 400:
            return failed(state, f"http {resp.status_code}")
        try:
            posts = resp.json()
        except ValueError:
            return failed(state, "not JSON (REST API closed or blocked)")
        if not isinstance(posts, list):
            return failed(state, "not a WordPress REST collection")
        items = [it for p in posts if isinstance(p, dict) and (it := post_to_item(p, source, now))]
        if not items:
            return failed(state, "no entries (empty collection)")
        return FetchResult(items=items, state=ok_state(state, now))
