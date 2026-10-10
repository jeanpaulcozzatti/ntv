from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from ntvstreams.fetchers import get_fetcher
from ntvstreams.models import SourceState
from ntvstreams.sources import Source
from tests.conftest import NOW

FIX = Path(__file__).parent / "fixtures"
SRC = Source(id="r-indiancountry", name="r/IndianCountry", kind="reddit", subreddit="IndianCountry", tier=4)
URL = "https://www.reddit.com/r/IndianCountry/new.rss"


@pytest.fixture
def http():
    with httpx.Client() as c:
        yield c


def run(http):
    return get_fetcher("reddit").fetch(SRC, SourceState(source_id=SRC.id), http, NOW)


def test_reddit_mapped(respx_mock, http):
    route = respx_mock.get(url__startswith=URL).respond(200, content=(FIX / "reddit_new.xml").read_bytes())
    res = run(http)
    assert res.error is None
    assert dict(route.calls[0].request.url.params)["limit"] == "50"
    posts = [i for i in res.items if i.kind == "post"]
    articles = [i for i in res.items if i.kind == "article"]
    assert len(posts) == 4 and len(articles) == 3
    p0 = posts[0]
    assert p0.url == "https://www.reddit.com/r/IndianCountry/comments/1x2dt9q/as_trumps_wall_closes_in_dhs_intimidates_a_border/"
    assert p0.author == "zsreport" and p0.engagement == 0
    assert p0.published_at == datetime(2026, 10, 10, 12, 12, 45, tzinfo=timezone.utc)
    assert p0.title.startswith("As Trump")
    a0 = articles[0]
    assert a0.canonical_url == "https://propublica.org/article/tohono-oodham-nation-arizona-border-wall"
    assert a0.title == p0.title and a0.published_at == p0.published_at
    assert (p0.id, a0.id) in res.links and len(res.links) == 3
    self_post = posts[1]
    assert self_post.title == "How badly did I screw up?"
    assert all(l[0] != self_post.id for l in res.links)


def test_reddit_user_agent_sent(respx_mock, http):
    route = respx_mock.get(url__startswith=URL).respond(200, content=(FIX / "reddit_new.xml").read_bytes())
    run(http)
    assert route.calls[0].request.headers["User-Agent"].startswith("NTVstreams/")


def test_reddit_429_returns_error(respx_mock, http):
    respx_mock.get(url__startswith=URL).respond(429)
    res = run(http)
    assert res.items == [] and "429" in res.error and res.state.consecutive_fails == 1


def test_reddit_subreddit_comes_from_entry_category(respx_mock, http):
    multi = Source(id="reddit", name="Reddit", kind="reddit", subreddit="IndianCountry+NativeAmerican", tier=4)
    route = respx_mock.get(url__startswith="https://www.reddit.com/r/IndianCountry+NativeAmerican/new.rss").respond(200, content=(FIX / "reddit_new.xml").read_bytes())
    res = get_fetcher("reddit").fetch(multi, SourceState(source_id=multi.id), http, NOW)
    assert route.call_count == 1
    posts = [i for i in res.items if i.kind == "post"]
    assert all(p.raw["subreddit"] == "IndianCountry" for p in posts)
