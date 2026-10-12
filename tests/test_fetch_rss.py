from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from ntvstreams.fetchers import get_fetcher
from ntvstreams.models import SourceState
from ntvstreams.sources import Source
from tests.conftest import NOW

FIX = Path(__file__).parent / "fixtures"
SRC = Source(id="navajo-times", name="Navajo Times", kind="rss", url="https://navajotimes.com/feed/", tier=1)


@pytest.fixture
def http():
    with httpx.Client() as c:
        yield c


def run(http, state=None, src=SRC):
    return get_fetcher("rss").fetch(src, state or SourceState(source_id=src.id), http, NOW)


def test_rss_maps_entries(respx_mock, http):
    respx_mock.get(SRC.url).respond(200, content=(FIX / "rss_wordpress.xml").read_bytes(), headers={"ETag": "e2"})
    res = run(http)
    assert res.error is None and len(res.items) == 3 and res.links == []
    first = res.items[0]
    assert first.kind == "article" and first.source_id == "navajo-times"
    assert first.canonical_url == "https://navajotimes.com/reznews/council-approves-water"
    assert first.title == "Council approves water settlement funding"
    assert first.author == "Arlyssa Becenti"
    assert "<" not in first.excerpt and "18-4" in first.excerpt
    assert first.published_at == datetime(2026, 10, 10, 12, 5, tzinfo=timezone.utc)
    assert res.items[1].published_at == datetime(2026, 10, 10, 16, 0, tzinfo=timezone.utc)  # -0700 converted
    assert res.items[2].author is None
    assert all(i.fetched_at == NOW for i in res.items)
    assert res.state.etag == "e2" and res.state.last_success_at == NOW and res.state.consecutive_fails == 0


def test_rss_missing_date_uses_now(respx_mock, http):
    respx_mock.get(SRC.url).respond(200, content=(FIX / "rss_nodate.xml").read_bytes())
    res = run(http)
    assert len(res.items) == 1 and res.items[0].published_at == NOW


def test_rss_sends_conditional_headers_and_handles_304(respx_mock, http):
    route = respx_mock.get(SRC.url).respond(304)
    res = run(http, SourceState(source_id=SRC.id, etag="e1", last_modified="Fri, 10 Oct 2026 10:00:00 GMT", consecutive_fails=3))
    sent = route.calls[0].request.headers
    assert sent["If-None-Match"] == "e1" and sent["If-Modified-Since"] == "Fri, 10 Oct 2026 10:00:00 GMT"
    assert res.items == [] and res.error is None
    assert res.state.consecutive_fails == 0 and res.state.etag == "e1"


def test_rss_soft_404_html_returns_error(respx_mock, http):
    respx_mock.get(SRC.url).respond(200, content=(FIX / "soft404.html").read_bytes(), headers={"content-type": "text/html"})
    res = run(http, SourceState(source_id=SRC.id, consecutive_fails=1))
    assert res.items == [] and "no entries" in res.error and res.state.consecutive_fails == 2 and res.state.last_error == res.error


def test_rss_http_error_returns_error(respx_mock, http):
    respx_mock.get(SRC.url).respond(500)
    res = run(http)
    assert res.items == [] and "500" in res.error and res.state.consecutive_fails == 1


def test_rss_network_error_returns_error(respx_mock, http):
    respx_mock.get(SRC.url).mock(side_effect=httpx.ConnectError("boom"))
    res = run(http)
    assert res.items == [] and res.error and res.state.consecutive_fails == 1


def test_rss_user_agent_sent(respx_mock, http):
    route = respx_mock.get(SRC.url).respond(200, content=(FIX / "rss_nodate.xml").read_bytes())
    run(http)
    assert route.calls[0].request.headers["User-Agent"].startswith("NTVstreams/")


def test_rss_google_news_strips_source_suffix(respx_mock, http):
    src = Source(id="gn-navajo", name="Navajo Times", kind="rss", tier=1,
                 url="https://news.google.com/rss/search?q=site:navajotimes.com&hl=en-US&gl=US&ceid=US:en")
    respx_mock.get(url__startswith="https://news.google.com/rss/search").respond(200, content=(FIX / "rss_gnews.xml").read_bytes())
    res = run(http, src=src)
    assert [i.title for i in res.items] == ["Council approves water settlement funding", "A title with - a dash in the middle"]
    assert res.items[0].excerpt is None  # Google News descriptions are just the link again
    assert res.items[0].raw["outlet"] == "Navajo Times"
