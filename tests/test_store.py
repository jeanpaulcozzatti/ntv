from datetime import timedelta

from ntvstreams.models import SourceState, StoryMember
from tests.conftest import NOW, hours_ago, make_item, make_story


def test_upsert_counts_new_and_refreshes_engagement(store):
    a, b = make_item("a" * 16), make_item("b" * 16)
    assert store.upsert_items([a, b]) == 2
    a2 = make_item("a" * 16, engagement=9, title="changed")
    assert store.upsert_items([a2]) == 0
    got = store.get_items(["a" * 16])["a" * 16]
    assert got.engagement == 9
    assert got.title == "Title " + "a" * 16  # title is not refreshed


def test_items_in_window_excludes_old(store):
    store.upsert_items([make_item("1" * 16, published_at=hours_ago(47)), make_item("2" * 16, published_at=hours_ago(49))])
    ids = [i.id for i in store.items_in_window(NOW, 48)]
    assert ids == ["1" * 16]


def test_items_in_window_newest_first(store):
    store.upsert_items([make_item("1" * 16, published_at=hours_ago(5)), make_item("2" * 16, published_at=hours_ago(1))])
    assert [i.id for i in store.items_in_window(NOW, 48)] == ["2" * 16, "1" * 16]


def test_items_fetched_after(store):
    store.upsert_items([
        make_item("1" * 16, published_at=hours_ago(3), fetched_at=hours_ago(2)),
        make_item("2" * 16, published_at=hours_ago(3), fetched_at=hours_ago(0.5)),
    ])
    assert len(store.items_fetched_after(None, NOW, 48)) == 2
    assert [i.id for i in store.items_fetched_after(hours_ago(1), NOW, 48)] == ["2" * 16]


def test_discussion_links(store):
    store.upsert_items([make_item("p" * 16, kind="post"), make_item("a" * 16)])
    store.add_discussion_link("p" * 16, "a" * 16)
    store.add_discussion_link("p" * 16, "a" * 16)  # idempotent
    assert [i.id for i in store.discussion_for("a" * 16)] == ["p" * 16]


def test_replace_page_expires_missing_and_keeps_created_at(store):
    store.upsert_items([make_item("a" * 16), make_item("b" * 16)])
    s1 = make_story("S1", created_at=hours_ago(10), lead_item_id="a" * 16)
    s2 = make_story("S2", created_at=hours_ago(10), lead_item_id="b" * 16, rank=2)
    store.replace_page([s1, s2], [StoryMember("S1", "a" * 16, "lead"), StoryMember("S2", "b" * 16, "lead")], "R1", hours_ago(10))
    assert {s.id for s in store.active_stories()} == {"S1", "S2"}
    s1b = make_story("S1", created_at=NOW, headline="New", lead_item_id="a" * 16, run_id="R2")
    store.replace_page([s1b], [StoryMember("S1", "a" * 16, "lead"), StoryMember("S1", "b" * 16, "coverage")], "R2", NOW)
    active = store.active_stories()
    assert [s.id for s in active] == ["S1"]
    assert active[0].created_at == hours_ago(10)  # kept
    assert active[0].headline == "New" and active[0].run_id == "R2"
    assert {(m.item_id, m.role) for m in store.story_members("S1")} == {("a" * 16, "lead"), ("b" * 16, "coverage")}
    assert store.story_members("S2") == []


def test_active_stories_ordered_by_desk_then_rank(store):
    store.upsert_items([make_item(c * 16) for c in "abc"])
    stories = [
        make_story("C1", desk="culture", lead_item_id="c" * 16, rank=1),
        make_story("N2", desk="news", lead_item_id="b" * 16, rank=2),
        make_story("N1", desk="news", lead_item_id="a" * 16, rank=1),
    ]
    store.replace_page(stories, [StoryMember(s.id, s.lead_item_id, "lead") for s in stories], "R1", NOW)
    assert [s.id for s in store.active_stories()] == ["N1", "N2", "C1"]


def test_expire_stories_older_than(store):
    store.upsert_items([make_item("a" * 16, published_at=hours_ago(50)), make_item("b" * 16, published_at=hours_ago(1))])
    stories = [make_story("S1", lead_item_id="a" * 16), make_story("S2", lead_item_id="b" * 16, rank=2)]
    store.replace_page(stories, [StoryMember("S1", "a" * 16, "lead"), StoryMember("S2", "b" * 16, "lead")], "R1", NOW)
    assert store.expire_stories_older_than(NOW - timedelta(hours=48), NOW) == 1
    assert [s.id for s in store.active_stories()] == ["S2"]


def test_prune_removes_old_items_and_links(store):
    old, new, post = make_item("o" * 16, published_at=hours_ago(24 * 8)), make_item("n" * 16), make_item("p" * 16, kind="post", published_at=hours_ago(24 * 8))
    store.upsert_items([old, new, post])
    store.add_discussion_link("p" * 16, "o" * 16)
    store.prune(NOW)
    assert set(store.get_items(["o" * 16, "n" * 16, "p" * 16])) == {"n" * 16}
    assert store.discussion_for("o" * 16) == []


def test_prune_removes_long_expired_stories(store):
    store.upsert_items([make_item("a" * 16)])
    store.replace_page([make_story("S1")], [StoryMember("S1", "a" * 16, "lead")], "R1", hours_ago(24 * 40))
    store.replace_page([], [], "R2", hours_ago(24 * 35))  # expires S1 35 days ago
    store.prune(NOW)
    assert store.story_members("S1") == []
    assert store.active_stories() == []


def test_source_state_default_then_set(store):
    st = store.get_source_state("x")
    assert st == SourceState(source_id="x")
    store.set_source_state(SourceState("x", etag="e", last_success_at=NOW, consecutive_fails=2, last_error="boom"))
    got = store.get_source_state("x")
    assert got.etag == "e" and got.last_success_at == NOW and got.consecutive_fails == 2


def test_runs_last_curation_and_month_tokens(store):
    r1 = store.start_run(hours_ago(24 * 20))  # September 20
    r1.curated = True
    r1.input_tokens = 100
    r1.finished_at = hours_ago(24 * 20)
    store.finish_run(r1)
    r2 = store.start_run(hours_ago(2))
    r2.input_tokens = 50
    r2.finished_at = hours_ago(2)
    r2.errors = [{"source_id": "x", "message": "boom"}]
    store.finish_run(r2)
    assert store.last_run().id == r2.id
    assert store.last_run().errors[0]["source_id"] == "x"
    assert store.last_curation_at() == hours_ago(24 * 20)
    assert store.month_input_tokens(NOW) == 50
