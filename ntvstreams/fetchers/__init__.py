"""Fetcher registry."""

from __future__ import annotations

from ..sources import Kind
from .base import USER_AGENT, FetchResult, Fetcher, fail_state, make_resolver, ok_state
from .rss import RssFetcher

_REGISTRY: dict[str, Fetcher] = {"rss": RssFetcher()}


def register(kind: str, fetcher: Fetcher) -> None:
    _REGISTRY[kind] = fetcher


def get_fetcher(kind: Kind) -> Fetcher:
    try:
        return _REGISTRY[kind]
    except KeyError:
        raise ValueError(f"no fetcher for kind {kind!r}") from None


__all__ = ["USER_AGENT", "FetchResult", "Fetcher", "fail_state", "get_fetcher", "make_resolver", "ok_state", "register"]
