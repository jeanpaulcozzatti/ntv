from pathlib import Path

import pytest
from pydantic import ValidationError

from ntvstreams.sources import Source, load_sources


def test_bluesky_requires_query_or_actor():
    with pytest.raises(ValidationError):
        Source(id="x", name="x", kind="bluesky", tier=4)


def test_bluesky_rejects_both_query_and_actor():
    with pytest.raises(ValidationError):
        Source(id="x", name="x", kind="bluesky", tier=4, query="a", actor="b")


def test_rss_requires_url():
    with pytest.raises(ValidationError):
        Source(id="x", name="x", kind="rss", tier=1)


def test_reddit_requires_subreddit_and_youtube_channel_id():
    with pytest.raises(ValidationError):
        Source(id="x", name="x", kind="reddit", tier=4)
    with pytest.raises(ValidationError):
        Source(id="x", name="x", kind="youtube", tier=4)


def test_valid_sources_construct():
    assert Source(id="a", name="A", kind="rss", url="https://a/feed", tier=1).desk_hint == "any"
    assert Source(id="b", name="B", kind="bluesky", query="x", tier=4).active is True


def test_load_starter_sources():
    srcs = load_sources(Path("sources.yaml"))
    assert len(srcs) >= 30
    assert len({s.id for s in srcs}) == len(srcs)
    assert sum(1 for s in srcs if s.desk_hint == "culture") >= 6
    assert any(s.kind == "reddit" for s in srcs)
    assert any(s.kind == "bluesky" for s in srcs)
