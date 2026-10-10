"""Fetcher protocol and helpers shared by every source kind."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Protocol

import httpx

from ..models import Item, SourceState
from ..sources import Source

USER_AGENT = "NTVstreams/0.1 (+https://github.com/jpcozzatti/ntv)"
TIMEOUT = 20.0


@dataclass
class FetchResult:
    items: list[Item]
    links: list[tuple[str, str]] = field(default_factory=list)  # (post_item_id, article_item_id)
    state: SourceState | None = None
    error: str | None = None


class Fetcher(Protocol):
    def fetch(self, source: Source, state: SourceState, http: httpx.Client, now: datetime) -> FetchResult: ...


def ok_state(state: SourceState, now: datetime, **changes) -> SourceState:
    return replace(state, last_success_at=now, consecutive_fails=0, last_error=None, **changes)


def fail_state(state: SourceState, error: str) -> SourceState:
    return replace(state, consecutive_fails=state.consecutive_fails + 1, last_error=error)


def failed(state: SourceState, error: str) -> FetchResult:
    return FetchResult(items=[], state=fail_state(state, error), error=error)


def make_resolver(http: httpx.Client) -> Callable[[str], str | None]:
    def resolve(url: str) -> str | None:
        try:
            r = http.head(url, follow_redirects=True, timeout=2.0, headers={"User-Agent": USER_AGENT})
            return str(r.url)
        except Exception:
            return None

    return resolve


def get(http: httpx.Client, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
    h = {"User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    return http.get(url, headers=h, timeout=TIMEOUT, follow_redirects=True)
