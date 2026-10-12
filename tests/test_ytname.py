import httpx
import pytest

from ntvstreams.fetchers.ytname import MAX_LOOKUPS, add_channel_handles, is_youtube_url, lookup_handle
from ntvstreams.sources import Source
from tests.conftest import make_item

OEMBED = "https://www.youtube.com/oembed"
REDDIT = Source(id="reddit", name="Reddit", kind="reddit", subreddit="IndianCountry", tier=4)
YT = Source(id="yt-powwows", name="Powwows.com (YouTube)", kind="youtube", channel_id="UC1", tier=4)


@pytest.fixture
def http():
    with httpx.Client() as c:
        yield c


def _yt_item(n: int, **kw):
    return make_item(f"yt{n:014d}", source_id="reddit", url=f"https://www.youtube.com/watch?v=vid{n}", **kw)


def test_is_youtube_url():
    assert all(is_youtube_url(u) for u in ["https://www.youtube.com/watch?v=a", "https://youtu.be/a?si=x", "https://m.youtube.com/shorts/a"])
    assert not any(is_youtube_url(u) for u in ["https://example.com/youtube.com", "https://notyoutube.com/a", "https://ictnews.org/x"])


def test_lookup_reads_the_handle_from_author_url(respx_mock, http):
    respx_mock.get(OEMBED).respond(200, json={"author_name": "Native American LoFi-N-Chill", "author_url": "https://www.youtube.com/@NativeAmericanLoFi"})
    assert lookup_handle(http, "https://youtu.be/AMmzpt1xmac") == "NativeAmericanLoFi"


def test_lookup_failures_return_none(respx_mock, http):
    respx_mock.get(OEMBED).respond(404)
    assert lookup_handle(http, "https://youtu.be/x") is None
    respx_mock.get(OEMBED).respond(200, json={"author_name": "No handle", "author_url": "https://www.youtube.com/channel/UC123"})
    assert lookup_handle(http, "https://youtu.be/x") is None
    respx_mock.get(OEMBED).respond(200, content=b"not json")
    assert lookup_handle(http, "https://youtu.be/x") is None


def test_enrich_adds_handle_and_leaves_other_items_alone(respx_mock, http, store):
    respx_mock.get(OEMBED).respond(200, json={"author_url": "https://www.youtube.com/@stopcasting"})
    video, article = _yt_item(1), make_item("art0000000000001", url="https://ictnews.org/x")
    out = add_channel_handles([video, article], REDDIT, store, http)
    assert out[0].raw["channel_handle"] == "stopcasting" and out[1] is article
    assert len(respx_mock.calls) == 1


def test_enrich_reuses_stored_handle_without_a_lookup(respx_mock, http, store):
    first = _yt_item(1, raw={"channel_handle": "stopcasting", "via": "u"})
    store.upsert_items([first])
    again = _yt_item(1, raw={"via": "u"})  # the fetcher emits the item fresh each run
    out = add_channel_handles([again], REDDIT, store, http)
    assert out[0].raw["channel_handle"] == "stopcasting" and len(respx_mock.calls) == 0


def test_youtube_source_resolves_its_channel_once(respx_mock, http, store):
    respx_mock.get(OEMBED).respond(200, json={"author_url": "https://www.youtube.com/@PowWowsCom"})
    videos = [make_item(f"vid{n:013d}", source_id="yt-powwows", kind="video", url=f"https://www.youtube.com/watch?v=v{n}") for n in range(5)]
    out = add_channel_handles(videos, YT, store, http)
    assert {i.raw["channel_handle"] for i in out} == {"PowWowsCom"} and len(respx_mock.calls) == 1


def test_enrich_is_bounded_and_failures_are_silent(respx_mock, http, store):
    respx_mock.get(OEMBED).respond(200, json={"author_url": "https://www.youtube.com/@a"})
    items = [_yt_item(n) for n in range(MAX_LOOKUPS + 5)]
    out = add_channel_handles(items, REDDIT, store, http)
    assert len(respx_mock.calls) == MAX_LOOKUPS
    assert sum(1 for i in out if i.raw.get("channel_handle")) == MAX_LOOKUPS
    respx_mock.get(OEMBED).mock(side_effect=httpx.ConnectError("down"))
    assert add_channel_handles([_yt_item(900)], REDDIT, store, http)[0].raw.get("channel_handle") is None


def test_backfill_names_stored_items_and_is_idempotent(respx_mock, http, store):
    from ntvstreams.fetchers.ytname import backfill_channel_handles
    from tests.conftest import NOW

    route = respx_mock.get(OEMBED).respond(200, json={"author_url": "https://www.youtube.com/@PowWowsCom"})
    old = make_item("old0000000000001", source_id="yt-powwows", kind="video", url="https://www.youtube.com/watch?v=old1")
    other = make_item("old0000000000002", source_id="yt-powwows", kind="video", url="https://www.youtube.com/watch?v=old2")
    plain = make_item("old0000000000003", source_id="reddit", url="https://ictnews.org/a")
    store.upsert_items([old, other, plain])
    assert backfill_channel_handles([YT, REDDIT], store, http, NOW) == 2
    assert route.call_count == 1  # one channel, one lookup
    got = store.get_items([old.id, other.id, plain.id])
    assert got[old.id].raw["channel_handle"] == "PowWowsCom" and got[other.id].raw["channel_handle"] == "PowWowsCom"
    assert "channel_handle" not in got[plain.id].raw
    assert backfill_channel_handles([YT, REDDIT], store, http, NOW) == 0 and route.call_count == 1
