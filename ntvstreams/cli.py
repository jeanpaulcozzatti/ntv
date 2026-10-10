"""Command line entry point: ntv run | fetch | curate | render | publish | check-sources."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import load_config
from .fetchers import USER_AGENT, get_fetcher
from .models import SourceState
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
    if args.command == "fetch":
        from .pipeline import fetch_stage

        with _http() as http:
            summary = fetch_stage(sources, store, http, now)
        print(f"new items: {summary.items_new}; errors: {len(summary.errors)}")
        for err in summary.errors:
            print(f"  {err['source_id']}: {err['message']}")
        return 1 if summary.errors else 0

    print(f"{args.command}: not implemented yet", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
