import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ntvstreams.models import SourceState
from ntvstreams.render import PageData, StoryView, hour_group, render_site, social_label
from ntvstreams.sources import Source
from tests.conftest import NOW, hours_ago, make_item, make_story

FIX = Path(__file__).parent / "fixtures"
PT = ZoneInfo("America/Los_Angeles")
SRC = {s.id: s for s in [
    Source(id="ict", name="ICT", kind="rss", url="https://x/feed", tier=1, desk_hint="news"),
    Source(id="navajo", name="Navajo Times", kind="rss", url="https://x/feed", tier=1),
    Source(id="bsky", name="Bluesky", kind="bluesky", query="x", tier=4),
    Source(id="reddit", name="Reddit", kind="reddit", subreddit="IndianCountry", tier=4),
    Source(id="yt", name="ICT (YouTube)", kind="youtube", channel_id="UC1", tier=4),
]}


def _story_view(n: int, desk: str, rank: int, with_discussion: bool = True) -> StoryView:
    lead = make_item(f"lead{n:012d}", source_id="ict", title=f"Lead title {n}", author="A. Reporter", published_at=hours_ago(n))
    cov = make_item(f"cov{n:013d}", source_id="navajo", title=f"Coverage {n}", published_at=hours_ago(n))
    story = make_story(f"S{desk}{n}", desk=desk, lead_item_id=lead.id, rank=rank, headline=f"Headline {desk} {n} says something plain")
    discussion = []
    if with_discussion:
        post = make_item(f"bsk{n:013d}", source_id="bsky", kind="post", title=f"Post {n}", url="https://bsky.app/profile/someone.bsky.social/post/abc", raw={"handle": "someone.bsky.social"}, published_at=hours_ago(n))
        red = make_item(f"red{n:013d}", source_id="reddit", kind="post", title=f"Reddit {n}", raw={"subreddit": "IndianCountry"}, published_at=hours_ago(n))
        discussion = [(post, SRC["bsky"]), (red, SRC["reddit"])]
    return StoryView(story=story, lead=lead, lead_source=SRC["ict"], coverage=[(cov, SRC["navajo"])], discussion=discussion)


@pytest.fixture
def page_data() -> PageData:
    culture = [_story_view(n, "culture", n, with_discussion=(n != 4)) for n in range(1, 5)]
    news = [_story_view(n + 10, "news", n) for n in range(1, 3)]
    choctaw = [_story_view(n + 20, "choctaw", n) for n in range(1, 3)]
    pnw = [_story_view(n + 30, "pnw", n) for n in range(1, 3)]
    river = [make_item(f"riv{n:013d}", source_id=["ict", "bsky", "reddit", "yt"][n % 4], kind=["article", "post", "post", "video"][n % 4],
                       title=f"River item {n}" + (" <b>bold</b>" if n == 0 else ""), published_at=NOW.replace(minute=0) - __import__("datetime").timedelta(minutes=37 * n),
                       raw={"handle": "h.bsky.social", "subreddit": "IndianCountry"}) for n in range(70)]
    states = {"ict": SourceState("ict", last_success_at=NOW), "bsky": SourceState("bsky", consecutive_fails=24, last_error="http 500")}
    return PageData(updated_at=NOW, news=news, culture=culture, river=river, sources_by_id=SRC, states=states, site_url="", choctaw=choctaw, pnw=pnw)


@pytest.fixture
def rendered(page_data, tmp_path):
    paths = render_site(page_data, tmp_path)
    out = {p.name: p.read_text() for p in paths if p.suffix in (".html", ".xml")}
    out["_dir"] = tmp_path
    return out


def _text(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def test_outputs_and_static(rendered):
    d = rendered["_dir"]
    for name in ["index.html", "river.html", "sources.html", "about.html", "feed.xml", "static/site.css", "static/colors_and_type.css", "static/favicon.svg"]:
        assert (d / name).exists(), name


def test_index_structure(rendered):
    html = rendered["index.html"]
    main = html[html.index('<main'):html.index('</main>')]
    aside = html[html.index('<aside'):html.index('</aside>')]
    assert main.count('<article class="story') == 4 and main.count("story--top") == 1
    assert aside.count('<article class="story') == 6
    assert "NTVstreams" in html and "UPDATED 06:40 PT" in html
    assert html.index("Headline culture 1") < html.index("Headline culture 2") < html.index("Headline culture 4")
    assert html.index("Headline news 11") < html.index("Headline news 12")
    assert "Culture &amp; Sports" in main and "News" not in _text(main).replace("Navajo", "")
    assert "News" in aside and "Culture" not in aside
    assert aside.index("News") < aside.index("Choctaw") < aside.index("Pacific Northwest Tribes") < aside.index("River")
    assert html.index("Headline choctaw 21") < html.index("Headline pnw 31")
    assert 'class="current"' in html and ">Front Page<" in html


def test_more_and_discussion_lines(rendered):
    html = rendered["index.html"]
    story1 = html[html.index("Headline culture 1"):html.index("Headline culture 2")]
    assert "More:" in story1 and "Navajo Times" in story1
    assert "Discussion:" in story1 and "@someone.bsky.social" in story1 and "r/IndianCountry" in story1
    story4 = html[html.index("Headline culture 4"):html.index("<aside")]
    assert "Discussion:" not in story4
    assert "ICT" in story1 and "A. Reporter" in story1


def test_every_link_is_an_item_or_page(rendered, page_data):
    hrefs = set(re.findall(r'href="([^"]+)"', rendered["index.html"]))
    allowed = {"index.html", "river.html", "sources.html", "about.html", "feed.xml", "static/site.css", "static/colors_and_type.css", "static/favicon.svg"}
    item_urls = {i.url for sv in page_data.news + page_data.culture + page_data.choctaw + page_data.pnw for i in [sv.lead, *[c for c, _ in sv.coverage], *[d for d, _ in sv.discussion]]}
    item_urls |= {i.url for i in page_data.river}
    assert hrefs <= allowed | item_urls, hrefs - (allowed | item_urls)


def test_no_exclamation_or_script(rendered):
    for name in ["index.html", "river.html", "sources.html", "about.html"]:
        assert "<script" not in rendered[name]
        assert "!" not in _text(rendered[name]), name


def test_title_escaped(rendered):
    assert "&lt;b&gt;bold&lt;/b&gt;" in rendered["index.html"] and "<b>bold</b>" not in rendered["index.html"]


def test_river_limited_on_index_full_on_river_page(rendered):
    assert rendered["index.html"].count('class="river-item"') == 60
    assert rendered["river.html"].count('class="river-item"') == 70
    assert "6 AM" in rendered["index.html"]


def test_river_hour_groups_in_pt_across_dst():
    now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    labels = [hour_group(datetime(2026, 11, 1, h, 30, tzinfo=timezone.utc), now, PT) for h in (8, 9, 10)]
    assert labels == ["1 AM", "1 AM", "2 AM"]
    assert hour_group(datetime(2026, 10, 31, 23, 30, tzinfo=timezone.utc), now, PT) == "YESTERDAY 4 PM"


def test_social_label():
    assert social_label(make_item("a" * 16, raw={"handle": "x.bsky.social"}), SRC["bsky"]) == "@x.bsky.social"
    assert social_label(make_item("b" * 16, url="https://bsky.app/profile/y.bsky.social/post/1"), SRC["bsky"]) == "@y.bsky.social"
    assert social_label(make_item("c" * 16, raw={"subreddit": "NativeAmerican"}), SRC["reddit"]) == "r/NativeAmerican"
    assert social_label(make_item("d" * 16, author="Native America Calling"), SRC["yt"]) == "Native America Calling"
    assert social_label(make_item("e" * 16), SRC["ict"]) == "ICT"


def test_sources_page_marks_stale(rendered):
    html = rendered["sources.html"]
    bsky_row = html[html.index("Bluesky"):]
    assert "stale" in bsky_row[:600].lower()
    ict_row = html[html.index(">ICT<"):html.index("Navajo Times")]
    assert "stale" not in ict_row.lower()


def test_feed_xml_valid(rendered, page_data):
    root = ET.fromstring(rendered["feed.xml"])
    items = root.findall("./channel/item")
    assert len(items) == 10
    assert {i.findtext("guid") for i in items} == {sv.story.id for sv in page_data.news + page_data.culture + page_data.choctaw + page_data.pnw}
    assert items[0].findtext("link") == page_data.culture[0].lead.url
    assert "Navajo Times" in items[0].findtext("description")


def test_golden_index(rendered):
    golden = FIX / "golden_index.html"
    norm = lambda s: re.sub(r"\s+", " ", s).strip()
    if os.environ.get("UPDATE_GOLDEN"):
        golden.write_text(rendered["index.html"])
    assert golden.exists(), "run with UPDATE_GOLDEN=1 to create the golden file"
    assert norm(rendered["index.html"]) == norm(golden.read_text())


def test_empty_desks_show_placeholder(page_data, tmp_path):
    page_data.news, page_data.culture, page_data.choctaw, page_data.pnw = [], [], [], []
    render_site(page_data, tmp_path)
    html = (tmp_path / "index.html").read_text()
    main = html[html.index("<main"):html.index("</main>")]
    assert "No stories yet" in main and "River" in main
    aside = html[html.index("<aside"):html.index("</aside>")]
    assert "News" not in aside and "Choctaw" not in aside and "Pacific Northwest" not in aside


def test_regional_sections_hidden_when_empty(page_data, tmp_path):
    page_data.choctaw = []
    render_site(page_data, tmp_path)
    aside = (tmp_path / "index.html").read_text()
    aside = aside[aside.index("<aside"):aside.index("</aside>")]
    assert "Choctaw" not in aside and "Pacific Northwest Tribes" in aside and "News" in aside
