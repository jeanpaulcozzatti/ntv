# NTVstreams

A Techmeme-style front page for Native American news and social media. A Python batch
pipeline fetches news feeds, Bluesky, Reddit, and YouTube, has Claude group the coverage
into stories with a plain headline, and renders a static site in the PHYLO editorial design.

- Design: [docs/superpowers/specs/2026-10-10-ntvstreams-design.md](docs/superpowers/specs/2026-10-10-ntvstreams-design.md)
- Plan: [docs/superpowers/plans/2026-10-10-ntvstreams.md](docs/superpowers/plans/2026-10-10-ntvstreams.md)

## Quickstart

```bash
uv sync
uv run ntv check-sources        # fetch every source once, report items and errors
export ANTHROPIC_API_KEY=...     # or put it in ~/.config/ntvstreams/env (see below)
uv run ntv run                   # fetch, curate, render, publish
open out/index.html
```

Stages can run alone: `ntv fetch`, `ntv curate`, `ntv render`, `ntv publish [--dry-run]`.
State lives in `data/ntv.db` (SQLite); the site is written to `out/`. Both are gitignored.

Without an API key the run still completes: fetch, render, and publish happen, the
curator logs an error, and the page shows the River with whatever stories it last had.

## Hourly on this Mac (launchd)

```bash
mkdir -p ~/.config/ntvstreams && chmod 700 ~/.config/ntvstreams
printf 'export ANTHROPIC_API_KEY=%s\n' 'sk-ant-...' > ~/.config/ntvstreams/env && chmod 600 ~/.config/ntvstreams/env
cp infra/com.ntv.streams.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ntv.streams.plist
```

The agent runs `infra/run.sh` every hour and once at load. It sources the env file if
present, so adding the key later needs no reinstall. Logs:

```bash
tail -f ~/Library/Logs/ntvstreams.out.log ~/Library/Logs/ntvstreams.err.log
```

Run now, stop, or remove:

```bash
launchctl kickstart -k gui/$(id -u)/com.ntv.streams
launchctl bootout gui/$(id -u)/com.ntv.streams
```

## Configuration

`config.toml` holds the model, effort, window, monthly token ceiling, paths, and the
publish backend. `sources.yaml` holds every source; adding one is a YAML edit. Run
`ntv check-sources` after editing. Kinds: `rss` (url), `bluesky` (query or actor),
`reddit` (subreddit, `A+B` for a multireddit), `youtube` (channel_id).

To publish to S3 behind CloudFront instead of a local directory:

```toml
[publish]
backend = "s3"
bucket = "your-bucket"
prefix = ""
cloudfront_distribution_id = "E..."
```

and set `site_url` to the public URL so the RSS feed links resolve.

## Tests

```bash
uv run pytest            # unit and end-to-end tests with fixtures, no network, no model
uv run pytest -m slow    # live feed smoke tests
uv run pytest -m live -s tests/test_curator.py   # one real Claude call, prints the page
```

## Observed cost and timing

A full pass over 45 sources takes about 35 seconds on a laptop. Token cost per curated
run is recorded in the `runs` table and in `out/status.json`; this section gets real
numbers once the API key is in place and three hourly runs have completed:

```bash
sqlite3 data/ntv.db "select started_at, items_new, curated, input_tokens, output_tokens, cache_read_tokens from runs order by started_at desc limit 5"
```
