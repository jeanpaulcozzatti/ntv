"""Command line entry point: ntv run | fetch | curate | render | publish | check-sources."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import load_config
from .curator import AnthropicCurator, curate_stage
from .fetchers import USER_AGENT, get_fetcher
from .models import SourceState
from .pipeline import AlreadyRunning, fetch_stage, run_all, run_lock, write_status
from .publish import get_publisher
from .render import build_page_data, render_site
from .sources import load_sources
from .store import Store


def _http() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True)


def check_sources(sources, now: datetime) -> int:
    failed_active = 0
    print(f"{'id':28} {'kind':8} {'items':>5}  status")
    with _http() as http:
        for s in sources:
            try:
                r = get_fetcher(s.kind).fetch(s, SourceState(source_id=s.id), http, now)
                error, n = r.error, len(r.items)
            except Exception as e:
                error, n = f"{e.__class__.__name__}: {e}", 0
            flag = "" if s.active else " (inactive)"
            print(f"{s.id:28} {s.kind:8} {n:>5}  {error or 'ok'}{flag}")
            if s.active and error:
                failed_active += 1
    return 1 if failed_active else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ntv")
    ap.add_argument("command", choices=["run", "fetch", "curate", "render", "publish", "check-sources"])
    ap.add_argument("--config", type=Path, default=Path("config.toml"))
    ap.add_argument("--sources", type=Path, default=Path("sources.yaml"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    config = load_config(args.config)
    sources = load_sources(args.sources)
    now = datetime.now(timezone.utc)

    if args.command == "check-sources":
        return check_sources(sources, now)

    store = Store(config.data_dir / "ntv.db")
    sources_by_id = {s.id: s for s in sources}

    if args.command == "run":
        try:
            with run_lock(config.data_dir / "lock"):
                with _http() as http:
                    run = run_all(config, sources, store, http, AnthropicCurator(config.model, config.effort),
                                  get_publisher(config), now, dry_run=args.dry_run)
        except AlreadyRunning as e:
            print(f"already running: {e}", file=sys.stderr)
            return 3
        print(f"run {run.id}: {run.stage_reached}; new items {run.items_new}; curated {run.curated}; "
              f"tokens in/out/cached {run.input_tokens}/{run.output_tokens}/{run.cache_read_tokens}; errors {len(run.errors)}")
        for err in run.errors:
            print(f"  {err.get('source_id') or err.get('stage')}: {err['message']}")
        return 1 if run.errors else 0

    if args.command == "fetch":
        with _http() as http:
            summary = fetch_stage(sources, store, http, now)
        print(f"new items: {summary.items_new}; errors: {len(summary.errors)}")
        for err in summary.errors:
            print(f"  {err['source_id']}: {err['message']}")
        return 1 if summary.errors else 0

    if args.command == "curate":
        run = store.start_run(now)
        outcome = curate_stage(store, AnthropicCurator(config.model, config.effort), config, sources_by_id, run.id, now)
        run.curated, run.finished_at, run.stage_reached = outcome.curated, now, "curate"
        run.input_tokens, run.output_tokens, run.cache_read_tokens = outcome.input_tokens, outcome.output_tokens, outcome.cache_read_tokens
        if outcome.error:
            run.errors.append({"stage": "curate", "message": f"{outcome.reason}: {outcome.error}"})
        store.finish_run(run)
        print(f"curate: {outcome.reason}; tokens in/out/cached {outcome.input_tokens}/{outcome.output_tokens}/{outcome.cache_read_tokens}"
              + (f"; error: {outcome.error}" if outcome.error else ""))
        return 0 if outcome.curated or outcome.reason in ("no_new_items", "token_ceiling") else 1

    if args.command == "render":
        paths = render_site(build_page_data(store, sources, config, now), config.out_dir)
        last = store.last_run()
        if last:
            write_status(last, store, sources, config.out_dir)
        print(f"rendered {len(paths)} files to {config.out_dir}")
        return 0

    if args.command == "publish":
        for action in get_publisher(config).publish(config.out_dir, dry_run=args.dry_run):
            print(action)
        return 0

    print(f"{args.command}: not implemented", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
