"""Bluesky fetcher over the public AppView XRPC API (no auth)."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

import httpx

from ..canonical import canonicalize, ensure_utc, item_id, make_excerpt
from ..models import Item, SourceState
from ..sources import Source
from .base import FetchResult, failed, get, make_resolver, ok_state

XRPC = "https://api.bsky.app/xrpc/"  # public.api.bsky.app returns 403 for searchPosts
TITLE_MAX = 140
_SOCIAL_HOSTS = {"bsky.app", "bsky.social"}


def _post_url(post: dict) -> str:
    rkey = post["uri"].rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{post['author']['handle']}/post/{rkey}"


def _external(post: dict) -> dict | None:
    for emb in (post.get("embed"), post.get("record", {}).get("embed")):
        if not isinstance(emb, dict):
            continue
        t = emb.get("$type", "")
        if t.startswith("app.bsky.embed.external"):
            ext = emb.get("external") or {}
            uri = ext.get("uri")
            if uri and (urlsplit(uri).hostname or "").lower() not in _SOCIAL_HOSTS:
                return ext
        if t.startswith("app.bsky.embed.recordWithMedia"):
            media = emb.get("media") or {}
            if media.get("$type", "").startswith("app.bsky.embed.external"):
                ext = media.get("external") or {}
                if ext.get("uri"):
                    return ext
    return None


def post_to_items(post: dict, source: Source, now: datetime, resolve=None) -> tuple[list[Item], list[tuple[str, str]]]:
    record = post.get("record") or {}
    author = post.get("author") or {}
    text = " ".join((record.get("text") or "").split())
    title = (text[: TITLE_MAX - 1].rstrip() + "…") if len(text) > TITLE_MAX else text
    if not title:
        title = f"{source.name} post"
    url = _post_url(post)
    published = ensure_utc(_parse_iso(record.get("createdAt")), now)
    post_item = Item(
        id=item_id(canonicalize(url)),
        source_id=source.id,
        url=url,
        canonical_url=canonicalize(url),
        kind="post",
        title=title,
        author=author.get("displayName") or author.get("handle"),
        excerpt=make_excerpt(text) if text else None,
        published_at=published,
        fetched_at=now,
        engagement=int(post.get("likeCount") or 0) + int(post.get("repostCount") or 0),
        raw={"uri": post.get("uri"), "handle": author.get("handle"), "text": text},
    )
    items, links = [post_item], []
    ext = _external(post)
    if ext:
        canonical = canonicalize(ext["uri"], resolve=resolve)
        article = Item(
            id=item_id(canonical),
            source_id=source.id,
            url=ext["uri"],
            canonical_url=canonical,
            kind="article",
            title=" ".join((ext.get("title") or "").split()) or (urlsplit(canonical).hostname or canonical),
            author=None,
            excerpt=make_excerpt(ext.get("description")),
            published_at=published,
            fetched_at=now,
            raw={"via": post.get("uri"), "via_handle": author.get("handle")},
        )
        items.append(article)
        links.append((post_item.id, article.id))
    return items, links


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


class BlueskyFetcher:
    def fetch(self, source: Source, state: SourceState, http: httpx.Client, now: datetime) -> FetchResult:
        if source.query:
            url = httpx.URL(XRPC + "app.bsky.feed.searchPosts", params={"q": source.query, "limit": 50, "sort": "latest"})
            key = "posts"
        else:
            url = httpx.URL(XRPC + "app.bsky.feed.getAuthorFeed", params={"actor": source.actor, "limit": 50, "filter": "posts_no_replies"})
            key = "feed"
        try:
            resp = get(http, str(url))
        except httpx.HTTPError as e:
            return failed(state, f"network: {e.__class__.__name__}: {e}")
        if resp.status_code >= 400:
            return failed(state, f"http {resp.status_code}")
        try:
            data = resp.json()
            entries = data[key]
        except (ValueError, KeyError, TypeError) as e:
            return failed(state, f"bad response: {e.__class__.__name__}")
        resolve = make_resolver(http)
        items: list[Item] = []
        links: list[tuple[str, str]] = []
        for entry in entries:
            post = entry.get("post") if key == "feed" else entry
            if not post or "uri" not in post:
                continue
            i, l = post_to_items(post, source, now, resolve)
            items.extend(i)
            links.extend(l)
        return FetchResult(items=items, links=links, state=ok_state(state, now))
