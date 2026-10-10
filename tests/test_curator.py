import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ntvstreams.config import Config
from ntvstreams.curator import (
    CULTURE_MAX,
    HEADLINE_MAX,
    NEWS_MAX,
    SYSTEM_PROMPT,
    CuratorError,
    CuratorResponse,
    PageOut,
    StoryOut,
    build_user_message,
    curate_stage,
    validate_page,
)
from ntvstreams.models import Item
from ntvstreams.sources import Source
from tests.conftest import NOW

FIX = Path(__file__).parent / "fixtures"
PAGE_OK = (FIX / "page_out_ok.json").read_text()
SOURCES = {s.id: s for s in [
    Source(id="ict", name="ICT", kind="rss", url="https://x/feed", tier=1, desk_hint="news"),
    Source(id="ict-culture", name="ICT Culture", kind="rss", url="https://x/feed", tier=1, desk_hint="culture"),
    Source(id="navajo-times", name="Navajo Times", kind="rss", url="https://x/feed", tier=1),
    Source(id="powwows", name="Powwows.com", kind="rss", url="https://x/feed", tier=1, desk_hint="culture"),
    Source(id="bsky-indian-country", name="Bluesky", kind="bluesky", query="x", tier=4),
    Source(id="reddit", name="Reddit", kind="reddit", subreddit="X", tier=4),
]}
CFG = Config()


def load_items() -> list[Item]:
    out = []
    for d in json.loads((FIX / "items_30.json").read_text()):
        d["published_at"] = datetime.fromisoformat(d["published_at"])
        d["fetched_at"] = datetime.fromisoformat(d["fetched_at"])
        out.append(Item(**d))
    return out


class FakeCurator:
    def __init__(self, responses, tokens=(1000, 300, 800)):
        self.responses = list(responses)
        self.calls = []
        self.tokens = tokens

    def complete(self, system: str, user: str) -> CuratorResponse:
        self.calls.append((system, user))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return CuratorResponse(text=r, input_tokens=self.tokens[0], output_tokens=self.tokens[1], cache_read_tokens=self.tokens[2])


@pytest.fixture
def seeded(store):
    store.upsert_items(load_items())
    return store


def test_skips_when_no_new_items(store):
    fake = FakeCurator([PAGE_OK])
    out = curate_stage(store, fake, CFG, SOURCES, "R1", NOW)
    assert out.curated is False and out.reason == "no_new_items" and fake.calls == []


def test_skips_when_ceiling_hit(seeded):
    run = seeded.start_run(NOW - timedelta(hours=1))
    run.input_tokens = CFG.monthly_input_token_ceiling
    run.finished_at = NOW - timedelta(hours=1)
    seeded.finish_run(run)
    fake = FakeCurator([PAGE_OK])
    out = curate_stage(seeded, fake, CFG, SOURCES, "R2", NOW)
    assert out.curated is False and out.reason == "token_ceiling" and fake.calls == []


def test_applies_valid_page(seeded):
    fake = FakeCurator([PAGE_OK])
    out = curate_stage(seeded, fake, CFG, SOURCES, "R1", NOW)
    assert out.curated is True and out.input_tokens == 1000 and out.output_tokens == 300 and out.cache_read_tokens == 800
    stories = seeded.active_stories()
    assert [s.desk for s in stories] == ["news", "news", "news", "culture", "culture"]
    assert stories[0].headline == "Supreme Court leaves tribal jurisdiction ruling in place" and stories[0].run_id == "R1"
    roles = {(m.item_id, m.role) for m in seeded.story_members(stories[0].id)}
    assert roles == {("item000000000001", "lead"), ("item000000000002", "coverage"), ("item000000000003", "coverage"),
                     ("item000000000004", "discussion"), ("item000000000005", "discussion")}
    assert len(fake.calls) == 1 and fake.calls[0][0] == SYSTEM_PROMPT


def test_unplaced_items_remain_in_window(seeded):
    curate_stage(seeded, FakeCurator([PAGE_OK]), CFG, SOURCES, "R1", NOW)
    ids = {i.id for i in seeded.items_in_window(NOW, 48)}
    assert {"item000000000009", "item000000000016"} <= ids


def test_retries_once_with_errors_then_keeps_previous_page(seeded):
    curate_stage(seeded, FakeCurator([PAGE_OK]), CFG, SOURCES, "R1", NOW - timedelta(hours=1))
    before = seeded.active_stories()
    seeded.upsert_items([replace(load_items()[0], id="item000000000099", url="https://example.org/99", canonical_url="https://example.org/99", fetched_at=NOW)])
    bad = json.dumps({"stories": [{"id": None, "desk": "news", "headline": "x", "lead_item_id": "nope", "coverage_item_ids": [], "discussion_item_ids": [], "rank": 1, "rationale": ""}], "unplaced_item_ids": []})
    fake = FakeCurator([bad, bad])
    out = curate_stage(seeded, fake, CFG, SOURCES, "R2", NOW)
    assert out.curated is False and out.reason == "invalid_response" and "nope" in out.error
    assert len(fake.calls) == 2 and "errors" in fake.calls[1][1] and "nope" in fake.calls[1][1]
    assert out.input_tokens == 2000
    assert seeded.active_stories() == before


def test_api_error_keeps_previous_page(seeded):
    fake = FakeCurator([CuratorError("rate limited")])
    out = curate_stage(seeded, fake, CFG, SOURCES, "R1", NOW)
    assert out.curated is False and out.reason == "api_error" and "rate limited" in out.error
    assert seeded.active_stories() == []


def _story(**kw) -> StoryOut:
    base = dict(id=None, desk="news", headline="A fine headline", lead_item_id="a", coverage_item_ids=[], discussion_item_ids=[], rank=1, rationale="r")
    base.update(kw)
    return StoryOut(**base)


def test_validate_ok():
    page = PageOut(stories=[_story(lead_item_id="a", coverage_item_ids=["b"]), _story(desk="culture", lead_item_id="c")], unplaced_item_ids=["d"])
    assert validate_page(page, {"a", "b", "c", "d"}, set()) == []


def test_validate_rejects_item_in_two_stories():
    page = PageOut(stories=[_story(lead_item_id="a"), _story(lead_item_id="b", coverage_item_ids=["a"], rank=2)], unplaced_item_ids=[])
    errs = validate_page(page, {"a", "b"}, set())
    assert any("a" in e and "more than one" in e for e in errs)


def test_validate_rejects_unknown_item_and_unknown_story_id():
    page = PageOut(stories=[_story(lead_item_id="zzz", id="S9")], unplaced_item_ids=[])
    errs = validate_page(page, {"a"}, {"S1"})
    assert any("zzz" in e for e in errs) and any("S9" in e for e in errs)


def test_validate_rejects_duplicate_story_id():
    page = PageOut(stories=[_story(id="S1", lead_item_id="a"), _story(id="S1", lead_item_id="b", rank=2)], unplaced_item_ids=[])
    assert any("S1" in e and "twice" in e for e in validate_page(page, {"a", "b"}, {"S1"}))


def test_validate_rejects_long_or_empty_headline():
    page = PageOut(stories=[_story(headline="x" * (HEADLINE_MAX + 1)), _story(headline="  ", lead_item_id="b", rank=2)], unplaced_item_ids=[])
    errs = validate_page(page, {"a", "b"}, set())
    assert len([e for e in errs if "headline" in e]) == 2


def test_validate_rejects_culture_over_cap_and_news_over_cap():
    ids = {f"i{n}" for n in range(60)}
    culture = [_story(desk="culture", lead_item_id=f"i{n}", rank=n + 1) for n in range(CULTURE_MAX + 1)]
    assert any("culture" in e for e in validate_page(PageOut(stories=culture, unplaced_item_ids=[]), ids, set()))
    news = [_story(desk="news", lead_item_id=f"i{n}", rank=n + 1) for n in range(NEWS_MAX + 1)]
    assert any("news" in e for e in validate_page(PageOut(stories=news, unplaced_item_ids=[]), ids, set()))


def test_validate_rejects_duplicate_rank_within_desk():
    page = PageOut(stories=[_story(lead_item_id="a", rank=1), _story(lead_item_id="b", rank=1)], unplaced_item_ids=[])
    assert any("rank" in e for e in validate_page(page, {"a", "b"}, set()))


def test_existing_story_keeps_id_and_created_at(seeded):
    t0 = NOW - timedelta(hours=2)
    curate_stage(seeded, FakeCurator([PAGE_OK]), CFG, SOURCES, "R1", t0)
    first = seeded.active_stories()[0]
    seeded.upsert_items([replace(load_items()[0], id="item000000000099", url="https://example.org/99", canonical_url="https://example.org/99", fetched_at=NOW)])
    page = json.loads(PAGE_OK)
    page["stories"] = [dict(page["stories"][0], id=first.id, headline="Updated headline", coverage_item_ids=["item000000000002", "item000000000003", "item000000000099"])]
    page["unplaced_item_ids"] = []
    out = curate_stage(seeded, FakeCurator([json.dumps(page)]), CFG, SOURCES, "R2", NOW)
    assert out.curated
    stories = seeded.active_stories()
    assert [s.id for s in stories] == [first.id]
    assert stories[0].created_at == t0 and stories[0].updated_at == NOW and stories[0].headline == "Updated headline"
    assert ("item000000000099", "coverage") in {(m.item_id, m.role) for m in seeded.story_members(first.id)}


def test_build_user_message_is_deterministic(seeded):
    items = load_items()
    msg1 = build_user_message([], {}, {}, items, SOURCES)
    msg2 = build_user_message([], {}, {}, list(reversed(items)), SOURCES)
    assert msg1 == msg2
    assert "item000000000001" in msg1 and "ICT" in msg1 and '"tier": 1' in msg1


def test_system_prompt_has_no_exclamation_or_emoji():
    assert "!" not in SYSTEM_PROMPT
    assert all(ord(ch) < 0x2600 for ch in SYSTEM_PROMPT)
    assert "unplaced_item_ids" in SYSTEM_PROMPT and str(HEADLINE_MAX) in SYSTEM_PROMPT


@pytest.mark.live
def test_live_prompt_prints_result(store):
    from ntvstreams.curator import AnthropicCurator

    store.upsert_items(load_items())
    out = curate_stage(store, AnthropicCurator(CFG.model, CFG.effort), CFG, SOURCES, "LIVE", NOW)
    print("\n", out)
    for s in store.active_stories():
        members = store.story_members(s.id)
        print(f"[{s.desk}] #{s.rank} {s.headline}  (lead {s.lead_item_id}, {len(members)} members)  -- {s.rationale}")


def test_anthropic_curator_wraps_any_sdk_exception():
    from ntvstreams.curator import AnthropicCurator

    class Msgs:
        def create(self, **kw):
            raise TypeError("Could not resolve authentication method")

    class Beta:
        messages = Msgs()

    class Client:
        beta = Beta()

    cur = AnthropicCurator("claude-opus-5-5", "medium", client=Client())
    with pytest.raises(CuratorError) as exc:
        cur.complete("sys", "user")
    assert "authentication" in str(exc.value)
