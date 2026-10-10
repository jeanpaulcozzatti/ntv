"""Reddit fetcher over the public JSON listing (no auth)."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from ..canonical import canonicalize, item_id, make_excerpt
from ..models import Item, SourceState
from ..sources import Source
from .base import FetchResult, failed, get, make_resolver, ok_state

REDDIT_HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com", "redd.it", "i.redd.it", "v.redd.it", "preview.redd.it"}


def post_to_items(d: dict, source: Source, now: datetime, resolve=None) -> tuple[list[Item], list[tuple[str, str]]]:
    url = "https://www.reddit.com" + d["permalink"]
    canonical = canonicalize(url)
    published = datetime.fromtimestamp(float(d.get("created_utc") or now.timestamp()), tz=timezone.utc)
    title = " ".join((d.get("title") or "").split()) or f"{source.name} post"
    post = Item(
        id=item_id(canonical),
        source_id=source.id,
        url=url,
        canonical_url=canonical,
        kind="post",
        title=title,
        author=d.get("author"),
        excerpt=make_excerpt(d.get("selftext")),
        published_at=published,
        fetched_at=now,
        engagement=int(d.get("score") or 0),
        raw={"id": d.get("id"), "subreddit": source.subreddit},
    )
    items, links = [post], []
    target = d.get("url") or ""
    host = (urlsplit(target).hostname or "").lower()
    if not d.get("is_self") and target and host and host not in REDDIT_HOSTS:
        a_canon = canonicalize(target, resolve=resolve)
        article = Item(
            id=item_id(a_canon),
            source_id=source.id,
            url=target,
            canonical_url=a_canon,
            kind="article",
            title=title,
            author=None,
            excerpt=None,
            published_at=published,
            fetched_at=now,
            raw={"via": d.get("permalink")},
        )
        items.append(article)
        links.append((post.id, article.id))
    return items, links


class RedditFetcher:
    def fetch(self, source: Source, state: SourceState, http: httpx.Client, now: datetime) -> FetchResult:
        url = httpx.URL(f"https://www.reddit.com/r/{source.subreddit}/new.json", params={"limit": 50, "raw_json": 1})
        try:
            resp = get(http, str(url))
        except httpx.HTTPError as e:
            return failed(state, f"network: {e.__class__.__name__}: {e}")
        if resp.status_code >= 400:
            return failed(state, f"http {resp.status_code}")
        try:
            children = resp.json()["data"]["children"]
        except (ValueError, KeyError, TypeError) as e:
            return failed(state, f"bad response: {e.__class__.__name__}")
        resolve = make_resolver(http)
        items: list[Item] = []
        links: list[tuple[str, str]] = []
        for child in children:
            d = child.get("data") or {}
            if not d.get("permalink"):
                continue
            i, l = post_to_items(d, source, now, resolve)
            items.extend(i)
            links.extend(l)
        return FetchResult(items=items, links=links, state=ok_state(state, now))
