from datetime import datetime, timezone

import pytest

from ntvstreams.canonical import canonicalize, ensure_utc, item_id, make_excerpt
from tests.conftest import NOW


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://www.navajotimes.com/a/b/?utm_source=x&utm_medium=y", "https://navajotimes.com/a/b"),
        ("http://Example.com:80/x/amp/", "http://example.com/x"),
        ("https://amp.example.com/x?fbclid=1#frag", "https://example.com/x"),
        ("https://example.com/x?b=2&a=1&s=99", "https://example.com/x?a=1&b=2"),
        ("https://example.com/x/", "https://example.com/x"),
        ("https://example-com.cdn.ampproject.org/c/s/example.com/story/1", "https://example.com/story/1"),
    ],
)
def test_canonicalize(raw, expected):
    assert canonicalize(raw) == expected


def test_shortener_uses_resolver():
    assert canonicalize("https://t.co/abc", resolve=lambda u: "https://example.com/real/") == "https://example.com/real"


def test_shortener_resolver_failure_keeps_original():
    assert canonicalize("https://t.co/abc", resolve=lambda u: None) == "https://t.co/abc"


def test_non_shortener_never_calls_resolver():
    def boom(u):
        raise AssertionError("resolver called")

    assert canonicalize("https://example.com/x", resolve=boom) == "https://example.com/x"


def test_syndications_hash_equal():
    assert item_id(canonicalize("https://x.com/p?utm_campaign=a")) == item_id(canonicalize("https://www.x.com/p/"))


def test_item_id_len():
    assert len(item_id("https://example.com/x")) == 16
    assert item_id("https://example.com/x") != item_id("https://example.com/y")


def test_excerpt_strips_html_and_cuts_on_word():
    out = make_excerpt("<p>Hello&nbsp;<b>world</b> " + "word " * 100 + "</p>", limit=40)
    assert out is not None
    assert "<" not in out
    assert len(out) <= 40
    assert not out.endswith(" ")
    assert out.startswith("Hello world")


def test_excerpt_none_and_blank():
    assert make_excerpt(None) is None
    assert make_excerpt("  <p> </p> ") is None


def test_ensure_utc_naive_is_utc():
    out = ensure_utc(datetime(2026, 10, 10, 6, 0), fallback=NOW)
    assert out.tzinfo == timezone.utc and out.hour == 6


def test_ensure_utc_converts_aware():
    from datetime import timedelta
    pdt = timezone(timedelta(hours=-7))
    assert ensure_utc(datetime(2026, 10, 10, 6, 0, tzinfo=pdt), fallback=NOW).hour == 13


def test_ensure_utc_none_uses_fallback():
    assert ensure_utc(None, fallback=NOW) == NOW
