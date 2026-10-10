from datetime import datetime, timedelta, timezone

import pytest

from ntvstreams.models import Item, Story, StoryMember

NOW = datetime(2026, 10, 10, 13, 40, tzinfo=timezone.utc)  # 06:40 PT


def make_item(
    id: str = "a" * 16,
    source_id: str = "src",
    published_at: datetime = NOW,
    fetched_at: datetime | None = None,
    **kw,
) -> Item:
    base = dict(
        id=id,
        source_id=source_id,
        url=f"https://example.com/{id}",
        canonical_url=f"https://example.com/{id}",
        kind="article",
        title=f"Title {id}",
        author=None,
        excerpt=None,
        published_at=published_at,
        fetched_at=fetched_at or published_at,
    )
    base.update(kw)
    return Item(**base)


def make_story(id: str = "S1", run_id: str = "R1", desk: str = "news", lead_item_id: str = "a" * 16,
              rank: int = 1, created_at: datetime = NOW, **kw) -> Story:
    base = dict(
        id=id, run_id=run_id, desk=desk, headline=f"Headline {id}", lead_item_id=lead_item_id,
        rank=rank, rationale="because", created_at=created_at, updated_at=created_at,
    )
    base.update(kw)
    return Story(**base)


def hours_ago(h: float) -> datetime:
    return NOW - timedelta(hours=h)


@pytest.fixture
def store(tmp_path):
    from ntvstreams.store import Store

    return Store(tmp_path / "t.db")
