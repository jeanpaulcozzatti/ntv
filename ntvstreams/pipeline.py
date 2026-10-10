"""Stage orchestration. The only module that knows the stage order."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from .config import Config
from .curator import CuratorClient, curate_stage
from .fetchers import fail_state, get_fetcher
from .models import Run
from .publish import Publisher
from .render import build_page_data, render_site
from .sources import Source
from .store import ITEM_DAYS, Store


def _safe_url(url: str) -> bool:
    return url.lower().startswith(("http://", "https://"))


@dataclass
class FetchSummary:
    items_new: int = 0
    errors: list[dict] = field(default_factory=list)


def fetch_stage(sources: list[Source], store: Store, http: httpx.Client, now: datetime) -> FetchSummary:
    summary = FetchSummary()
    for source in sources:
        if not source.active:
            continue
        state = store.get_source_state(source.id)
        try:
            result = get_fetcher(source.kind).fetch(source, state, http, now)
        except Exception as e:  # a fetcher bug never stops the run
            msg = f"{e.__class__.__name__}: {e}"
            store.set_source_state(fail_state(state, msg))
            summary.errors.append({"source_id": source.id, "message": msg})
            continue
        if result.items:
            # Feeds (Google News site: queries especially) serve items years old; keep only what retention would keep.
            cutoff = now - timedelta(days=ITEM_DAYS)
            fresh = [it for it in result.items if it.published_at >= cutoff and _safe_url(it.url) and _safe_url(it.canonical_url)]
            kept = {it.id for it in fresh}
            summary.items_new += store.upsert_items(fresh)
            for post_id, article_id in result.links:
                if post_id in kept and article_id in kept:
                    store.add_discussion_link(post_id, article_id)
        if result.state is not None:
            store.set_source_state(result.state)
        if result.error:
            summary.errors.append({"source_id": source.id, "message": result.error})
    return summary


# ── full run ───────────────────────────────────────────────────────────────
class AlreadyRunning(RuntimeError):
    """Another run holds the lock."""


@contextmanager
def run_lock(path: Path, stale_minutes: int = 30) -> Iterator[None]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        age_min = (time.time() - path.stat().st_mtime) / 60
        if age_min < stale_minutes:
            raise AlreadyRunning(f"lock {path} is {age_min:.0f} minutes old")
        path.unlink()
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as e:
        raise AlreadyRunning(f"lock {path} appeared concurrently") from e
    with os.fdopen(fd, "w") as fh:
        fh.write(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def write_status(run: Run, store: Store, sources: list[Source], out_dir: Path) -> Path:
    def ts(dt):
        return dt.isoformat() if dt else None

    status = {
        "run_id": run.id,
        "started_at": ts(run.started_at),
        "finished_at": ts(run.finished_at),
        "stage_reached": run.stage_reached,
        "items_new": run.items_new,
        "curated": run.curated,
        "tokens": {"input": run.input_tokens, "output": run.output_tokens, "cache_read": run.cache_read_tokens},
        "errors": run.errors,
        "sources": {},
    }
    for s in sources:
        st = store.get_source_state(s.id)
        status["sources"][s.id] = {
            "name": s.name,
            "active": s.active,
            "last_success_at": ts(st.last_success_at),
            "consecutive_fails": st.consecutive_fails,
            "last_error": st.last_error,
        }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "status.json"
    path.write_text(json.dumps(status, indent=1, sort_keys=True), encoding="utf-8")
    return path


def run_all(config: Config, sources: list[Source], store: Store, http: httpx.Client, curator: CuratorClient,
            publisher: Publisher, now: datetime, dry_run: bool = False) -> Run:
    run = store.start_run(now)
    sources_by_id = {s.id: s for s in sources}
    try:
        run.stage_reached = "fetch"
        summary = fetch_stage(sources, store, http, now)
        run.items_new = summary.items_new
        run.errors.extend(summary.errors)

        run.stage_reached = "curate"
        try:
            outcome = curate_stage(store, curator, config, sources_by_id, run.id, now)
        except Exception as e:  # the curator must never take the page down
            run.errors.append({"stage": "curate", "message": f"crash: {e.__class__.__name__}: {e}"})
        else:
            run.curated = outcome.curated
            run.input_tokens, run.output_tokens, run.cache_read_tokens = outcome.input_tokens, outcome.output_tokens, outcome.cache_read_tokens
            if outcome.error:
                run.errors.append({"stage": "curate", "message": f"{outcome.reason}: {outcome.error}"})
        # Stories age out at the end of every run, curated or not, so a stalled editor never leaves old news up.
        store.expire_stories_older_than(now - timedelta(hours=config.window_hours), now)

        run.stage_reached = "render"
        data = build_page_data(store, sources, config, now)
        render_site(data, config.out_dir)
        run.finished_at = now
        write_status(run, store, sources, config.out_dir)

        run.stage_reached = "publish"
        publisher.publish(config.out_dir, dry_run=dry_run)
        run.stage_reached = "done"
    except Exception as e:
        run.errors.append({"stage": run.stage_reached, "message": f"{e.__class__.__name__}: {e}"})
        run.finished_at = now
        store.finish_run(run)
        raise
    run.finished_at = now
    store.finish_run(run)
    store.prune(now)
    return run
