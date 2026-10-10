from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from ntvstreams.fetchers import get_fetcher
from ntvstreams.models import SourceState
from ntvstreams.sources import Source
from tests.conftest import NOW

FIX = Path(__file__).parent / "fixtures"
SRC = Source(id="yt-nac", name="Native America Calling (YouTube)", kind="youtube", channel_id="UCabc", tier=4)
URL = "https://www.youtube.com/feeds/videos.xml"


@pytest.fixture
def http():
    with httpx.Client() as c:
        yield c


def run(http):
    return get_fetcher("youtube").fetch(SRC, SourceState(source_id=SRC.id), http, NOW)


def test_youtube_mapped(respx_mock, http):
    route = respx_mock.get(url__startswith=URL).respond(200, content=(FIX / "youtube_feed.xml").read_bytes())
    res = run(http)
    assert res.error is None and dict(route.calls[0].request.url.params)["channel_id"] == "UCabc"
    assert len(res.items) == 2 and res.links == []
    v = res.items[0]
    assert v.kind == "video" and v.url == "https://www.youtube.com/watch?v=vid001"
    assert v.canonical_url == "https://youtube.com/watch?v=vid001"
    assert v.author == "Native America Calling" and v.engagement == 1234
    assert v.excerpt.startswith("Tribal nations are building")
    assert v.published_at == datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    assert res.items[1].engagement == 0


def test_youtube_error(respx_mock, http):
    respx_mock.get(url__startswith=URL).respond(404)
    res = run(http)
    assert res.items == [] and "404" in res.error and res.state.consecutive_fails == 1
