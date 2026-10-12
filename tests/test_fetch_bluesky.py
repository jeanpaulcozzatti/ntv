import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from ntvstreams.fetchers import get_fetcher
from ntvstreams.models import SourceState
from ntvstreams.sources import Source
from tests.conftest import NOW

FIX = Path(__file__).parent / "fixtures"
SEARCH = Source(id="bsky-indian-country", name="Bluesky", kind="bluesky", query='"Indian Country"', tier=4)
AUTHOR = Source(id="bsky-nno", name="Bluesky", kind="bluesky", actor="nativenewsonline.bsky.social", tier=4)
BASE = "https://api.bsky.app/xrpc/"


@pytest.fixture
def http():
    with httpx.Client() as c:
        yield c


def run(http, src):
    return get_fetcher("bluesky").fetch(src, SourceState(source_id=src.id), http, NOW)


def test_search_posts_mapped(respx_mock, http):
    route = respx_mock.get(url__startswith=BASE + "app.bsky.feed.searchPosts").respond(200, json=json.loads((FIX / "bsky_search.json").read_text()))
    res = run(http, SEARCH)
    assert res.error is None
    params = dict(route.calls[0].request.url.params)
    assert params["q"] == '"Indian Country"' and params["sort"] == "latest" and params["limit"] == "50"
    posts = [i for i in res.items if i.kind == "post"]
    articles = [i for i in res.items if i.kind == "article"]
    assert len(posts) == 3 and len(articles) == 1
    p0 = posts[0]
    assert p0.url == "https://bsky.app/profile/ictnews.bsky.social/post/3kxyz001"
    assert p0.author == "ICT" and p0.engagement == 52
    assert p0.published_at == datetime(2026, 10, 10, 12, 30, tzinfo=timezone.utc)
    assert p0.title.startswith("Court rules in favor")
    a = articles[0]
    assert a.canonical_url == "https://ictnews.org/news/x" and a.title == "Court rules for tribe in water case"
    assert a.excerpt == "A federal judge sided with the tribe on Thursday." and a.published_at == p0.published_at
    assert res.links == [(p0.id, a.id)]
    assert a.raw["via_handle"] == "ictnews.bsky.social"  # who shared the link, shown instead of "Bluesky"
    assert "\n" not in posts[2].title


def test_empty_text_title_fallback(respx_mock, http):
    respx_mock.get(url__startswith=BASE).respond(200, json=json.loads((FIX / "bsky_search.json").read_text()))
    res = run(http, SEARCH)
    assert [i for i in res.items if i.kind == "post"][1].title == "Bluesky post"
    assert [i for i in res.items if i.kind == "post"][1].author == "photog.bsky.social"


def test_author_feed_endpoint_used(respx_mock, http):
    route = respx_mock.get(url__startswith=BASE + "app.bsky.feed.getAuthorFeed").respond(200, json=json.loads((FIX / "bsky_author_feed.json").read_text()))
    res = run(http, AUTHOR)
    params = dict(route.calls[0].request.url.params)
    assert params["actor"] == "nativenewsonline.bsky.social" and params["filter"] == "posts_no_replies"
    assert len(res.items) == 2 and res.state.last_success_at == NOW


def test_http_error_returns_error(respx_mock, http):
    respx_mock.get(url__startswith=BASE).respond(500)
    res = run(http, SEARCH)
    assert res.items == [] and "500" in res.error and res.state.consecutive_fails == 1


def test_bad_json_returns_error(respx_mock, http):
    respx_mock.get(url__startswith=BASE).respond(200, content=b"<html>nope</html>")
    res = run(http, SEARCH)
    assert res.items == [] and res.error and res.state.consecutive_fails == 1
