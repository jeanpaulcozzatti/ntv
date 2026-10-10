from pathlib import Path

import httpx
import pytest

from ntvstreams.pipeline import fetch_stage
from ntvstreams.sources import Source
from tests.conftest import NOW

FIX = Path(__file__).parent / "fixtures"
GOOD = Source(id="good", name="Good", kind="rss", url="https://good.example/feed", tier=1)
BAD = Source(id="bad", name="Bad", kind="rss", url="https://bad.example/feed", tier=1)
OFF = Source(id="off", name="Off", kind="rss", url="https://off.example/feed", tier=1, active=False)
SOCIAL = Source(id="r-x", name="r/X", kind="reddit", subreddit="X", tier=4)


def test_fetch_stage_isolates_failures_and_skips_inactive(store, respx_mock):
    respx_mock.get(GOOD.url).respond(200, content=(FIX / "rss_wordpress.xml").read_bytes())
    respx_mock.get(BAD.url).respond(500)
    off_route = respx_mock.get(OFF.url).respond(200, content=(FIX / "rss_wordpress.xml").read_bytes())
    with httpx.Client() as http:
        summary = fetch_stage([GOOD, BAD, OFF], store, http, NOW)
    assert summary.items_new == 3
    assert summary.errors == [{"source_id": "bad", "message": "http 500"}]
    assert store.get_source_state("bad").consecutive_fails == 1
    assert store.get_source_state("good").last_success_at == NOW
    assert off_route.call_count == 0
    assert len(store.items_in_window(NOW, 48)) == 3


def test_fetch_stage_records_discussion_links(store, respx_mock):
    respx_mock.get(url__startswith="https://www.reddit.com/r/X/new.rss").respond(200, content=(FIX / "reddit_new.xml").read_bytes())
    with httpx.Client() as http:
        summary = fetch_stage([SOCIAL], store, http, NOW)
    assert summary.items_new == 7  # 4 posts + 3 linked articles
    articles = [i for i in store.items_in_window(NOW, 48) if i.kind == "article"]
    assert len(articles) == 3 and all(len(store.discussion_for(a.id)) == 1 for a in articles)


def test_fetch_stage_survives_fetcher_exception(store, respx_mock, monkeypatch):
    class Boom:
        def fetch(self, *a, **k):
            raise RuntimeError("kaboom")

    import ntvstreams.pipeline as p
    monkeypatch.setattr(p, "get_fetcher", lambda kind: Boom())
    with httpx.Client() as http:
        summary = fetch_stage([GOOD], store, http, NOW)
    assert summary.items_new == 0 and "kaboom" in summary.errors[0]["message"]
    assert store.get_source_state("good").consecutive_fails == 1


def test_fetch_stage_skips_items_older_than_retention(store, respx_mock):
    old_feed = (FIX / "rss_wordpress.xml").read_text().replace("Fri, 10 Oct 2026 12:05:00 +0000", "Mon, 01 Sep 2026 12:05:00 +0000")
    respx_mock.get(GOOD.url).respond(200, content=old_feed.encode())
    with httpx.Client() as http:
        summary = fetch_stage([GOOD], store, http, NOW)
    assert summary.items_new == 2
    assert len(store.get_items(["x"])) == 0
    assert all(i.published_at > NOW - __import__("datetime").timedelta(days=7) for i in store.items_in_window(NOW, 24 * 30))


def test_fetch_stage_drops_items_with_unsafe_url_scheme(store, respx_mock):
    feed = (FIX / "rss_wordpress.xml").read_text().replace("https://navajotimes.com/opinion/language-land/", "javascript:alert(1)")
    respx_mock.get(GOOD.url).respond(200, content=feed.encode())
    with httpx.Client() as http:
        summary = fetch_stage([GOOD], store, http, NOW)
    items = store.items_in_window(NOW, 48)
    assert summary.items_new == 2 and len(items) == 2
    assert all(i.url.startswith("https://") for i in items)
