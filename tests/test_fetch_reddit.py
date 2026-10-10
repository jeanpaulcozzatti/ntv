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
SRC = Source(id="r-indiancountry", name="r/IndianCountry", kind="reddit", subreddit="IndianCountry", tier=4)
URL = "https://www.reddit.com/r/IndianCountry/new.json"


@pytest.fixture
def http():
    with httpx.Client() as c:
        yield c


def run(http):
    return get_fetcher("reddit").fetch(SRC, SourceState(source_id=SRC.id), http, NOW)


def test_reddit_mapped(respx_mock, http):
    route = respx_mock.get(url__startswith=URL).respond(200, json=json.loads((FIX / "reddit_new.json").read_text()))
    res = run(http)
    assert res.error is None
    params = dict(route.calls[0].request.url.params)
    assert params["limit"] == "50" and params["raw_json"] == "1"
    posts = [i for i in res.items if i.kind == "post"]
    articles = [i for i in res.items if i.kind == "article"]
    assert len(posts) == 3 and len(articles) == 1
    p0 = posts[0]
    assert p0.url == "https://www.reddit.com/r/IndianCountry/comments/1abc/thoughts_on_the_new_language_program/"
    assert p0.author == "rezkid" and p0.engagement == 44
    assert p0.excerpt == "My tribe just launched an immersion school. Curious what others think."
    assert p0.published_at == datetime.fromtimestamp(1791640800, tz=timezone.utc)
    a = articles[0]
    assert a.canonical_url == "https://navajotimes.com/reznews/council-approves-water"
    assert a.title == "Navajo Times: Council approves water settlement funding"
    assert res.links == [(posts[1].id, a.id)]


def test_reddit_user_agent_sent(respx_mock, http):
    route = respx_mock.get(url__startswith=URL).respond(200, json={"data": {"children": []}})
    res = run(http)
    assert route.calls[0].request.headers["User-Agent"].startswith("NTVstreams/")
    assert res.items == [] and res.error is None


def test_reddit_429_returns_error(respx_mock, http):
    respx_mock.get(url__startswith=URL).respond(429)
    res = run(http)
    assert res.items == [] and "429" in res.error and res.state.consecutive_fails == 1
