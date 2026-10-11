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
URL = "https://www.choctawnation.com/wp-json/wp/v2/news"
SRC = Source(id="cno-news", name="Choctaw Nation", kind="wordpress", url=URL, tier=2, desk_hint="choctaw")


@pytest.fixture
def http():
    with httpx.Client() as c:
        yield c


def run(http, state=None, src=SRC):
    return get_fetcher("wordpress").fetch(src, state or SourceState(source_id=src.id), http, NOW)


def test_wordpress_maps_posts(respx_mock, http):
    route = respx_mock.get(url__startswith=URL).respond(200, json=json.loads((FIX / "wp_news.json").read_text()))
    res = run(http)
    assert res.error is None and len(res.items) == 4 and res.links == []  # the post with no link is skipped
    first = res.items[0]
    assert first.kind == "article" and first.source_id == "cno-news"
    assert first.title == "Our Choctaw Heritage and Bow Hunting, Muzzleloader Season"
    assert first.url.startswith("https://www.choctawnation.com/news/chiefs-blog/")
    assert first.published_at == datetime(2026, 10, 9, 21, 4, 55, tzinfo=timezone.utc)
    assert first.excerpt is None
    assert res.items[2].excerpt.startswith("The Choctaw Cultural Center hosts") and "<" not in res.items[2].excerpt
    assert res.items[3].title == "Chief’s Statement on “Treaty Rights” & Sovereignty"
    assert all(i.fetched_at == NOW for i in res.items)
    assert res.state.last_success_at == NOW and res.state.consecutive_fails == 0
    q = route.calls[0].request.url.params
    assert q["orderby"] == "date" and q["order"] == "desc" and q["per_page"] == "30"


def test_wordpress_keeps_existing_query_params(respx_mock, http):
    src = Source(id="x", name="X", kind="wordpress", url="https://example.org/wp-json/wp/v2/posts?categories=7", tier=2)
    route = respx_mock.get(url__startswith="https://example.org/wp-json/wp/v2/posts").respond(200, json=json.loads((FIX / "wp_news.json").read_text()))
    run(http, src=src)
    assert route.calls[0].request.url.params["categories"] == "7"


def test_wordpress_http_error_counts_as_failure(respx_mock, http):
    respx_mock.get(url__startswith=URL).respond(403)
    res = run(http, SourceState(source_id=SRC.id, consecutive_fails=1))
    assert res.items == [] and res.error == "http 403" and res.state.consecutive_fails == 2


def test_wordpress_html_instead_of_json_is_an_error(respx_mock, http):
    respx_mock.get(url__startswith=URL).respond(200, content=b"<html>blocked</html>", headers={"content-type": "text/html"})
    res = run(http)
    assert res.items == [] and "not JSON" in res.error


def test_wordpress_error_object_and_empty_list_are_errors(respx_mock, http):
    respx_mock.get(url__startswith=URL).respond(200, json={"code": "rest_no_route"})
    assert "not a WordPress REST collection" in run(http).error
    respx_mock.get(url__startswith=URL).respond(200, json=[])
    assert "no entries" in run(http).error


def test_wordpress_source_requires_url():
    with pytest.raises(Exception):
        Source(id="x", name="x", kind="wordpress", tier=2)
