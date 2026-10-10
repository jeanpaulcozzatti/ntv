import json
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from ntvstreams.config import Config
from ntvstreams.curator import CuratorError, CuratorResponse
from ntvstreams.pipeline import AlreadyRunning, run_all, run_lock, write_status
from ntvstreams.publish import LocalPublisher
from ntvstreams.sources import Source
from tests.conftest import NOW

FIX = Path(__file__).parent / "fixtures"
PAGE_OK = (FIX / "page_out_ok.json").read_text()
GOOD = Source(id="ict", name="ICT", kind="rss", url="https://good.example/feed", tier=1)
BAD = Source(id="bad", name="Bad", kind="rss", url="https://bad.example/feed", tier=1)


class FakeCurator:
    def __init__(self, text=None, error=None):
        self.text, self.error, self.calls = text, error, 0

    def complete(self, system, user):
        self.calls += 1
        if self.error:
            raise self.error
        return CuratorResponse(self.text, 1000, 300, 0)


def _page_for_fixture_items():
    # The rss_wordpress fixture yields 3 items; build a one-story page over them by id after fetch.
    return None


@pytest.fixture
def cfg(tmp_path):
    return Config(data_dir=tmp_path / "data", out_dir=tmp_path / "out")


def _mock_feeds(respx_mock):
    respx_mock.get(GOOD.url).respond(200, content=(FIX / "rss_wordpress.xml").read_bytes())
    respx_mock.get(BAD.url).respond(500)


class PageFromStore:
    """Curator fake that builds a valid one-story page from whatever items are in the store."""

    def __init__(self, store):
        self.store, self.calls = store, 0

    def complete(self, system, user):
        self.calls += 1
        items = self.store.items_in_window(NOW, 48)
        page = {"stories": [{"id": None, "desk": "news", "headline": "Council approves water settlement funding",
                             "lead_item_id": items[0].id, "coverage_item_ids": [i.id for i in items[1:]],
                             "discussion_item_ids": [], "rank": 1, "rationale": "r"}], "unplaced_item_ids": []}
        return CuratorResponse(json.dumps(page), 1000, 300, 0)


def test_run_all_end_to_end(tmp_path, cfg, store, respx_mock):
    _mock_feeds(respx_mock)
    site = tmp_path / "site"
    with httpx.Client() as http:
        run = run_all(cfg, [GOOD, BAD], store, http, PageFromStore(store), LocalPublisher(site), NOW)
    for name in ["index.html", "river.html", "sources.html", "about.html", "feed.xml", "status.json", "static/site.css"]:
        assert (site / name).exists(), name
    assert run.stage_reached == "done" and run.items_new == 3 and run.curated is True and run.input_tokens == 1000
    assert run.errors == [{"source_id": "bad", "message": "http 500"}]
    status = json.loads((site / "status.json").read_text())
    assert status["run_id"] == run.id and status["curated"] is True and status["items_new"] == 3
    assert status["sources"]["bad"]["consecutive_fails"] == 1 and status["sources"]["ict"]["last_success_at"] == NOW.isoformat()
    assert status["errors"] == run.errors
    assert "Council approves water settlement funding" in (site / "index.html").read_text()
    assert store.last_run().stage_reached == "done"


def test_curator_failure_still_publishes(tmp_path, cfg, store, respx_mock):
    _mock_feeds(respx_mock)
    site = tmp_path / "site"
    with httpx.Client() as http:
        run = run_all(cfg, [GOOD], store, http, FakeCurator(error=CuratorError("boom")), LocalPublisher(site), NOW)
    assert run.curated is False and run.stage_reached == "done"
    assert (site / "index.html").exists()
    assert {"stage": "curate", "message": "api_error: boom"} in run.errors
    assert json.loads((site / "status.json").read_text())["curated"] is False


def test_second_run_without_new_items_skips_curator(tmp_path, cfg, store, respx_mock):
    _mock_feeds(respx_mock)
    site = tmp_path / "site"
    fake = PageFromStore(store)
    with httpx.Client() as http:
        run_all(cfg, [GOOD], store, http, fake, LocalPublisher(site), NOW)
        run2 = run_all(cfg, [GOOD], store, http, fake, LocalPublisher(site), NOW + timedelta(hours=1))
    assert fake.calls == 1 and run2.curated is False and run2.stage_reached == "done"
    assert "07:40 PT" in (site / "index.html").read_text()


def test_render_failure_skips_publish_and_records_error(tmp_path, cfg, store, respx_mock, monkeypatch):
    _mock_feeds(respx_mock)
    site = tmp_path / "site"
    import ntvstreams.pipeline as p

    def boom(*a, **k):
        raise RuntimeError("template exploded")

    monkeypatch.setattr(p, "render_site", boom)
    with httpx.Client() as http, pytest.raises(RuntimeError):
        run_all(cfg, [GOOD], store, http, FakeCurator(text=PAGE_OK), LocalPublisher(site), NOW)
    assert not site.exists()
    last = store.last_run()
    assert last.stage_reached == "render" and any(e.get("stage") == "render" for e in last.errors)


def test_lock_blocks_second_run(tmp_path):
    lock = tmp_path / "lock"
    with run_lock(lock):
        with pytest.raises(AlreadyRunning):
            with run_lock(lock):
                pass
    assert not lock.exists()


def test_stale_lock_is_removed(tmp_path):
    lock = tmp_path / "lock"
    lock.write_text("123")
    old = time.time() - 31 * 60
    import os
    os.utime(lock, (old, old))
    with run_lock(lock, stale_minutes=30):
        assert lock.exists()
    assert not lock.exists()


def test_cli_run_exit_codes(tmp_path, respx_mock, monkeypatch):
    import ntvstreams.cli as cli

    _mock_feeds(respx_mock)
    (tmp_path / "config.toml").write_text(f'data_dir="{tmp_path / "data"}"\nout_dir="{tmp_path / "out"}"\n[publish]\nbackend="local"\ntarget="{tmp_path / "out"}"\n')
    (tmp_path / "sources.yaml").write_text("sources:\n  - {id: ict, name: ICT, kind: rss, tier: 1, url: https://good.example/feed}\n")
    monkeypatch.setattr(cli, "AnthropicCurator", lambda model, effort: FakeCurator(text=json.dumps({"stories": [], "unplaced_item_ids": []})))
    code = cli.main(["run", "--config", str(tmp_path / "config.toml"), "--sources", str(tmp_path / "sources.yaml")])
    assert code == 0
    assert (tmp_path / "out" / "index.html").exists()
    (tmp_path / "sources.yaml").write_text("sources:\n  - {id: bad, name: Bad, kind: rss, tier: 1, url: https://bad.example/feed}\n")
    assert cli.main(["run", "--config", str(tmp_path / "config.toml"), "--sources", str(tmp_path / "sources.yaml")]) == 1


def test_unexpected_curator_exception_still_publishes(tmp_path, cfg, store, respx_mock):
    _mock_feeds(respx_mock)
    site = tmp_path / "site"
    with httpx.Client() as http:
        run = run_all(cfg, [GOOD], store, http, FakeCurator(error=RuntimeError("sdk blew up")), LocalPublisher(site), NOW)
    assert run.stage_reached == "done" and run.curated is False
    assert (site / "index.html").exists()
    assert any(e.get("stage") == "curate" and "sdk blew up" in e["message"] for e in run.errors)
