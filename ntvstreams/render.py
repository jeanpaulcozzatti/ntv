"""Render the static site from database state. Pure: no network, no model."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Config
from .models import Item, SourceState, Story
from .sources import Source
from .store import Store

TEMPLATES = Path(__file__).parent / "templates"
STATIC = Path(__file__).parent / "static"
CULTURE_SHOWN = 32  # main column; the cap is higher, the rest stay in the River
NEWS_SHOWN = 8      # side column; never padded
CHOCTAW_SHOWN = 8   # side column; never padded
PNW_SHOWN = 12      # side column; never padded
RIVER_ON_INDEX = 60
STALE_AFTER = 24

FOOTER_NOTE = ("Headlines are written by an automated editor from the linked sources and refreshed hourly. "
               "Every source is listed on the Sources page.")


@dataclass
class StoryView:
    story: Story
    lead: Item
    lead_source: Source
    coverage: list[tuple[Item, Source]]
    discussion: list[tuple[Item, Source]]


@dataclass
class PageData:
    updated_at: datetime
    news: list[StoryView]
    culture: list[StoryView]
    river: list[Item]
    sources_by_id: dict[str, Source]
    states: dict[str, SourceState]
    site_url: str = ""
    timezone: str = "America/Los_Angeles"
    contact_email: str = ""
    choctaw: list[StoryView] = field(default_factory=list)
    pnw: list[StoryView] = field(default_factory=list)


# ── filters ────────────────────────────────────────────────────────────────
def pt_time(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%H:%M PT")


def age(dt: datetime, now: datetime, tz: ZoneInfo) -> str:
    delta = now - dt
    if delta < timedelta(hours=1):
        return f"{max(int(delta.total_seconds() // 60), 0)}m"
    if delta < timedelta(hours=24):
        return f"{int(delta.total_seconds() // 3600)}h"
    days = (now.astimezone(tz).date() - dt.astimezone(tz).date()).days
    return "yesterday" if days <= 1 else f"{days}d"


def hour_group(dt: datetime, now: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    label = local.strftime("%-I %p")
    days = (now.astimezone(tz).date() - local.date()).days
    if days <= 0:
        return label
    if days == 1:
        return f"YESTERDAY {label}"
    return f"{local.strftime('%b %-d').upper()} {label}"


# Hosting suffixes that carry no identity. The account is whatever comes before them.
PLATFORM_SUFFIXES = ("bsky.social", "latinsky.social", "blacksky.app", "bsky.team", "ap.brid.gy")
# Bridged websites: the part before the suffix is the original domain (usluck.com.web.brid.gy -> usluck.com).
BRIDGE_SUFFIXES = ("web.brid.gy",)


def short_handle(handle: str) -> str:
    """The part of a handle that identifies the account: fox2detroit.bsky.social -> fox2detroit, nytimes.com -> nytimes."""
    handle = handle.strip().lstrip("@").lower()
    for suffix in PLATFORM_SUFFIXES:
        if handle.endswith("." + suffix):
            return handle[: -len(suffix) - 1]
    for suffix in BRIDGE_SUFFIXES:
        if handle.endswith("." + suffix):
            handle = handle[: -len(suffix) - 1]
            break
    labels = handle.removeprefix("www.").split(".")
    return ".".join(labels[:-1]) if len(labels) > 1 else handle


def _host(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return {"youtu.be": "youtube.com"}.get(host, host) or None


def social_label(item: Item, source: Source) -> str:
    raw = item.raw or {}
    if source.kind == "bluesky":
        handle = raw.get("handle") or raw.get("via_handle")  # via_handle: who shared a link card
        if not handle:
            parts = urlsplit(item.url).path.split("/")
            handle = parts[2] if len(parts) > 2 and parts[1] == "profile" else None
        if handle:
            return "@" + short_handle(handle)
        return _host(item.url) or source.name
    if source.kind == "reddit":
        parts = urlsplit(item.url).path.split("/")
        sub = parts[2] if len(parts) > 2 and parts[1].lower() == "r" else None
        sub = sub or raw.get("subreddit") or raw.get("via_subreddit")
        if sub and "+" not in sub:
            return f"r/{sub}"
        return _host(item.url) or source.name
    if source.kind == "youtube":
        return item.author or source.name
    return source.name


SOCIAL_KINDS = ("bluesky", "reddit", "youtube")


def source_label(item: Item, source: Source | None) -> str:
    """The name shown on a kicker or link: a social item shows who posted (@handle, r/sub, channel), not the network."""
    if source is None:
        return item.source_id
    if source.kind in SOCIAL_KINDS:
        return social_label(item, source)
    outlet = (item.raw or {}).get("outlet")  # Google News: the real publisher
    return outlet or source.name


def link_labels(*pair_lists) -> list[tuple[str, str]]:
    """(url, label) for each linked item, one per distinct label: two posts by one account show the account once."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for pairs in pair_lists:
        for item, src in pairs:
            label = source_label(item, src)
            if label.lower() in seen:
                continue
            seen.add(label.lower())
            out.append((item.url, label))
    return out


def group_river(items: list[Item], now: datetime, tz: ZoneInfo) -> list[tuple[str, list[Item]]]:
    groups: list[tuple[str, list[Item]]] = []
    for it in items:
        label = hour_group(it.published_at, now, tz)
        if groups and groups[-1][0] == label:
            groups[-1][1].append(it)
        else:
            groups.append((label, [it]))
    return groups


# ── data ───────────────────────────────────────────────────────────────────
def _views(store: Store, stories: list[Story], sources_by_id: dict[str, Source]) -> list[StoryView]:
    members = {s.id: store.story_members(s.id) for s in stories}
    ids = {m.item_id for ms in members.values() for m in ms} | {s.lead_item_id for s in stories}
    items = store.get_items(ids)
    views = []
    for s in stories:
        lead = items.get(s.lead_item_id)
        lead_src = sources_by_id.get(lead.source_id) if lead else None
        if lead is None or lead_src is None:
            continue
        cov, disc = [], []
        for m in members[s.id]:
            it = items.get(m.item_id)
            src = sources_by_id.get(it.source_id) if it else None
            if it is None or src is None:
                continue
            if m.role == "coverage":
                cov.append((it, src))
            elif m.role == "discussion":
                disc.append((it, src))
        cov.sort(key=lambda p: (p[1].tier, -p[0].published_at.timestamp()))
        disc.sort(key=lambda p: (-p[0].engagement, -p[0].published_at.timestamp()))
        views.append(StoryView(story=s, lead=lead, lead_source=lead_src, coverage=cov, discussion=disc))
    return views


def build_page_data(store: Store, sources: list[Source], config: Config, now: datetime) -> PageData:
    sources_by_id = {s.id: s for s in sources}
    stories = store.active_stories()
    news = _views(store, [s for s in stories if s.desk == "news"], sources_by_id)[:NEWS_SHOWN]
    culture = _views(store, [s for s in stories if s.desk == "culture"], sources_by_id)[:CULTURE_SHOWN]
    choctaw = _views(store, [s for s in stories if s.desk == "choctaw"], sources_by_id)[:CHOCTAW_SHOWN]
    pnw = _views(store, [s for s in stories if s.desk == "pnw"], sources_by_id)[:PNW_SHOWN]
    river = store.items_in_window(now, config.window_hours)
    states = {s.id: store.get_source_state(s.id) for s in sources}
    return PageData(updated_at=now, news=news, culture=culture, river=river, sources_by_id=sources_by_id,
                    states=states, site_url=config.site_url, timezone=config.timezone, choctaw=choctaw, pnw=pnw)


# ── rendering ──────────────────────────────────────────────────────────────
def _env(data: PageData) -> Environment:
    tz = ZoneInfo(data.timezone)
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "xml"]), trim_blocks=True, lstrip_blocks=True)
    env.filters["pt_time"] = lambda dt: pt_time(dt, tz)
    env.filters["age"] = lambda dt: age(dt, data.updated_at, tz)
    env.filters["social_label"] = lambda pair: social_label(pair[0], pair[1])
    env.globals["source_label"] = lambda item, source: source_label(item, source)
    env.globals["link_labels"] = link_labels
    env.globals["SOCIAL_KINDS"] = SOCIAL_KINDS
    env.filters["rfc822"] = lambda dt: dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
    env.globals["group_river"] = lambda items: group_river(items, data.updated_at, tz)
    env.globals["footer_note"] = FOOTER_NOTE
    env.globals["stale_after"] = STALE_AFTER
    return env


def render_site(data: PageData, out_dir: Path, static_src: Path | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    env = _env(data)
    pages = {
        "index.html": ("index.html", {"river": data.river[:RIVER_ON_INDEX]}),
        "river.html": ("river.html", {"river": data.river}),
        "sources.html": ("sources.html", {}),
        "about.html": ("about.html", {}),
        "feed.xml": ("feed.xml", {}),
    }
    written: list[Path] = []
    for name, (template, extra) in pages.items():
        html = env.get_template(template).render(data=data, current=name, **extra)
        path = out_dir / name
        path.write_text(html, encoding="utf-8")
        written.append(path)
    src = static_src or STATIC
    dest = out_dir / "static"
    shutil.copytree(src, dest, dirs_exist_ok=True)
    written.extend(sorted(p for p in dest.iterdir() if p.is_file()))
    return written
