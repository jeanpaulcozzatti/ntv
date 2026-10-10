"""URL canonicalization, item ids, excerpts, and datetime normalization. Pure functions."""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS: frozenset[str] = frozenset(
    {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source", "s", "ncid", "igshid"}
)
SHORTENER_HOSTS: frozenset[str] = frozenset({"t.co", "bit.ly", "buff.ly", "ow.ly"})

_AMP_CACHE_RE = re.compile(r"^/c/(s/)?(.+)$")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _unwrap_amp(scheme: str, host: str, path: str) -> tuple[str, str, str]:
    if host.endswith(".cdn.ampproject.org"):
        m = _AMP_CACHE_RE.match(path)
        if m:
            secure, rest = m.group(1), m.group(2)
            inner_host, _, inner_path = rest.partition("/")
            return ("https" if secure else "http", inner_host, "/" + inner_path)
    if host.startswith("amp."):
        host = host[4:]
    segments = [seg for seg in path.split("/") if seg != "amp"]
    path = "/".join(segments)
    return scheme, host, path


def canonicalize(url: str, resolve: Callable[[str], str | None] | None = None) -> str:
    url = url.strip()
    parts = urlsplit(url)
    if resolve is not None and parts.hostname and parts.hostname.lower() in SHORTENER_HOSTS:
        resolved = resolve(url)
        if resolved:
            url = resolved
            parts = urlsplit(url)

    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if parts.port and not (
        (scheme == "http" and parts.port == 80) or (scheme == "https" and parts.port == 443)
    ):
        host = f"{host}:{parts.port}"
    path = parts.path or "/"
    scheme, host, path = _unwrap_amp(scheme, host, path)
    if host.startswith("www."):
        host = host[4:]

    kept = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    )
    query = urlencode(kept)
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, host, path, query, ""))


def item_id(canonical_url: str) -> str:
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]


def make_excerpt(text: str | None, limit: int = 300) -> str | None:
    if not text:
        return None
    plain = html.unescape(_TAG_RE.sub(" ", text))
    plain = _WS_RE.sub(" ", plain.replace("\xa0", " ")).strip()
    if not plain:
        return None
    if len(plain) <= limit:
        return plain
    cut = plain.rfind(" ", 0, limit + 1)
    if cut <= 0:
        cut = limit
    return plain[:cut].rstrip()


def ensure_utc(dt: datetime | None, fallback: datetime) -> datetime:
    if dt is None:
        return fallback
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
