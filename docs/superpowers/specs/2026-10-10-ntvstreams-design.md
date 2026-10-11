# NTVstreams — design spec

**Date:** 2026-10-10
**Status:** approved in conversation; awaiting written review
**Owner:** JP Cozzatti

## 1. Purpose

NTVstreams is a Techmeme-style aggregator for Native American news and
social media. One dense front page, regenerated hourly, groups coverage
of each story, names a lead source, lists every other outlet and social
thread covering it, and runs a river of everything recent alongside.

It starts as a personal daily read and is built from day one to become
a public site. Success in phase one: JP stops checking ten sites. Success
in phase three: readers and Native journalists bookmark and share it.

There is no newsroom. Curation is automated. The design therefore
optimizes for running unattended, degrading gracefully, and being
trustworthy enough to put a name on.

## 2. Scope

**In v1**

- Ingest from RSS/Atom news feeds, Bluesky (accounts and keyword
  searches), Reddit (subreddits), and YouTube (channel feeds).
- Canonicalize and deduplicate items; attach social posts that link to
  an article as discussion on that article.
- Claude-driven curation: cluster, pick lead, rank, write headline, and
  route each story to one of two desks: **News** and **Culture & Sports**.
- Render a static site in the PHYLO editorial design: front page, river,
  sources, about, RSS feed, status JSON.
- Run hourly on JP's laptop via launchd; publish to a local directory.
- Operational log of every run with token usage and per-source errors.

**Not in v1**

- X/Twitter, Facebook, Instagram, TikTok.
- Any server-side runtime, database in production, accounts, search,
  comments, or personalization.
- Alerting. The footer timestamp and `status.json` are the health check.
- Daily archive pages.
- Deployment to the Mac mini or to a public host. Both are phases 2 and 3
  below and need no design change.

## 3. Architecture

A batch pipeline that writes a static site. One command, `ntv run`,
executes four stages in order; each is also a subcommand.

```
sources.yaml ─▶ fetch ─▶ SQLite (items) ─▶ curate ─▶ SQLite (stories) ─▶ render ─▶ out/ ─▶ publish
                 │                           │                               │
            fixtures/tests            Claude Opus 5.5                  Jinja2 + PHYLO CSS
```

- **fetch**: for every active source, pull new items, canonicalize, upsert
  into SQLite. Network only here.
- **curate**: hand Claude the current page state plus items new since the
  last successful curation; receive a full new page state; validate;
  store. The only model call in the system.
- **render**: read stories and items from SQLite, render templates to
  `out/`. Pure function of database state.
- **publish**: copy `out/` to the configured target. Two backends behind
  one interface: `local` (phase 1 and 2) and `s3` (phase 3).

Each stage is independently testable without network or model.

## 4. Sources

### 4.1 Configuration

`sources.yaml` at repo root. Adding or removing a source is a YAML edit.

```yaml
sources:
  - id: navajo-times            # stable slug, used as foreign key
    name: Navajo Times
    kind: rss                   # rss | bluesky | reddit | youtube
    url: https://navajotimes.com/feed/
    tier: 1                     # 1 native-press, 2 institutional, 3 wide-net, 4 social
    desk_hint: news             # news | culture | any  (hint to the curator, not a rule)
    active: true
  - id: bsky-search-indian-country
    name: Bluesky · "Indian Country"
    kind: bluesky
    query: '"Indian Country"'    # bluesky: either query or actor
    tier: 4
    desk_hint: any
  - id: r-indiancountry
    name: r/IndianCountry
    kind: reddit
    subreddit: IndianCountry
    tier: 4
    desk_hint: any
  - id: yt-native-america-calling
    name: Native America Calling (YouTube)
    kind: youtube
    channel_id: UC…             # YouTube channel id, not handle
    tier: 4
    desk_hint: any
```

Fetchers per kind:

| kind | endpoint | auth |
|---|---|---|
| rss | the feed URL, via `feedparser`, with ETag / Last-Modified | none |
| bluesky | `https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=…` or `app.bsky.feed.getAuthorFeed?actor=…` | none |
| reddit | `https://www.reddit.com/r/{sub}/new.json?limit=50` with a descriptive User-Agent | none |
| youtube | `https://www.youtube.com/feeds/videos.xml?channel_id=…` | none |

### 4.2 Starter list

Every URL below is **unverified** at spec time. Task 1 of the
implementation plan fetches each one, drops dead feeds, and finds
replacements. The desk mix must leave the Culture & Sports desk, the main
column, with enough supply to fill twelve stories on a normal day.

**Tier 1, Native-run press (desk_hint news unless noted)**
ICT (ictnews.org), Native News Online, Indianz.com, Tribal Business News,
Navajo Times, Cherokee Phoenix, Osage News, Biskinik (Choctaw Nation),
Mvskoke Media, Tulalip News, Lakota Times, Alaska Native News, Underscore
News, Buffalo's Fire, Native America Calling, High Country News Indigenous
Affairs, Grist Indigenous. ICT and Native News Online arts and sports
section feeds with `desk_hint: culture`.

**Tier 2, institutional**
NCAI, Native American Rights Fund, NDN Collective, Indian Gaming
Association, Bureau of Indian Affairs newsroom, Indian Health Service
newsroom, Choctaw Nation news.

**Tier 3, wide net**
Two Google News RSS queries:
`"Native American" OR "tribal nation" OR "Indian Country"` (news) and
`"Native American" (actor OR singer OR athlete OR film OR series OR album
OR powwow)` (culture). APTN News (Canada) included but `active: false`
pending a decision on cross-border scope.

**Tier 4, social**
Bluesky searches: `"Indian Country"`, `"Native American"`, `#NDN`,
`#Indigenous`. Bluesky accounts: a seed list of Native journalists,
outlets, and prominent Native actors, musicians, athletes, and creators,
curated by JP during Task 1. Reddit: r/IndianCountry, r/NativeAmerican,
r/IndigenousPeoples. YouTube: ICT, Native America Calling, and two or
three culture channels chosen in Task 1.

**Culture supply (desk_hint culture)**
Powwows.com, Native Max Magazine, Native American Music Awards, plus the
culture Google News query and culture Bluesky/YouTube accounts above.

## 5. Data model

One SQLite file, `ntv.db`, in the repo's `data/` directory (gitignored).

```sql
items (
  id            TEXT PRIMARY KEY,   -- sha256(canonical_url)[:16]
  source_id     TEXT NOT NULL,
  url           TEXT NOT NULL,      -- as fetched
  canonical_url TEXT NOT NULL,
  kind          TEXT NOT NULL,      -- article | post | video
  title         TEXT NOT NULL,
  author        TEXT,
  excerpt       TEXT,               -- <= 300 chars, plain text
  published_at  TEXT NOT NULL,      -- ISO 8601 UTC
  fetched_at    TEXT NOT NULL,
  engagement    INTEGER DEFAULT 0,  -- likes / upvotes / views where available
  raw           TEXT                -- JSON of the fetched record
);
discussion_links (                  -- a social post that links to an article
  post_item_id    TEXT REFERENCES items(id),
  article_item_id TEXT REFERENCES items(id),
  PRIMARY KEY (post_item_id, article_item_id)
);
stories (
  id            TEXT PRIMARY KEY,   -- ulid, stable across runs while the story lives
  run_id        TEXT NOT NULL,      -- run that last touched it
  desk          TEXT NOT NULL,      -- news | culture
  headline      TEXT NOT NULL,
  lead_item_id  TEXT NOT NULL REFERENCES items(id),
  rank          INTEGER NOT NULL,   -- 1 = top, within desk
  rationale     TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  expired_at    TEXT                -- set when the story leaves the page
);
story_items (
  story_id TEXT REFERENCES stories(id),
  item_id  TEXT REFERENCES items(id),
  role     TEXT NOT NULL,           -- lead | coverage | discussion
  PRIMARY KEY (story_id, item_id)
);
source_state (
  source_id         TEXT PRIMARY KEY,
  etag              TEXT,
  last_modified     TEXT,
  last_success_at   TEXT,
  consecutive_fails INTEGER DEFAULT 0,
  last_error        TEXT
);
runs (
  id            TEXT PRIMARY KEY,   -- ulid
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  stage_reached TEXT,               -- fetch | curate | render | publish | done
  items_new     INTEGER,
  curated       INTEGER,            -- 0/1: did the model run
  input_tokens  INTEGER,
  output_tokens INTEGER,
  cache_read_tokens INTEGER,
  errors        TEXT                -- JSON list of {source_id|stage, message}
);
```

Retention: items older than 7 days and their links are pruned at the end
of every run. Expired stories are kept for 30 days for debugging, then
pruned.

## 6. Ingest and canonicalization

Canonicalization is a pure function `canonicalize(url) -> str`:

1. Resolve known shorteners (t.co, bit.ly, buff.ly, ow.ly) via HEAD with a
   two-second timeout; on failure keep the original.
2. Unwrap AMP (`/amp/`, `amp.` subdomain, Google AMP cache).
3. Lowercase scheme and host; drop `www.`; drop default ports.
4. Remove tracking params: `utm_*`, `fbclid`, `gclid`, `mc_cid`, `mc_eid`,
   `ref`, `source`, `s`, `ncid`, `igshid`. Sort the remaining query.
5. Strip fragment and trailing slash.

Item id is `sha256(canonical_url)[:16]`. Re-fetching an item is an upsert
that refreshes `engagement` and `raw` only.

**Social posts.** A Bluesky or Reddit post that links to an external URL
is stored as a `post` item *and* the linked page is stored as an
`article` item (title from the post's link card or the feed; excerpt
from the post text), with a `discussion_links` row. A post with no
outbound link is a plain `post` item eligible to be a story on its own.
A YouTube video is a `video` item.

**Excerpt.** HTML stripped, whitespace collapsed, cut at 300 characters
on a word boundary.

**Time zone.** Stored UTC; rendered America/Los_Angeles, labelled "PT".

## 7. Curation

### 7.1 Contract

One call per run. Input is two blocks:

- **Current page**: every unexpired story, as `{id, desk, headline, rank,
  lead: {source, title}, member_count, newest_item_at}`.
- **New items**: items with `fetched_at` after the last successful
  curation, within the 48-hour window, as `{id, source, tier, desk_hint,
  kind, title, excerpt, published_at, engagement, discussion_of}`.

Output is a full new page state, validated by pydantic via structured
output (`client.messages.parse`):

```python
class StoryOut(BaseModel):
    id: str | None              # existing story id, or null for a new story
    desk: Literal["news", "culture"]
    headline: str               # <= 110 chars
    lead_item_id: str
    coverage_item_ids: list[str]
    discussion_item_ids: list[str]
    rank: int                   # 1-based within desk
    rationale: str              # one line, stored, never shown

class PageOut(BaseModel):
    stories: list[StoryOut]
    unplaced_item_ids: list[str]
```

### 7.2 Editorial instructions (system prompt, static, cached)

In priority order:

1. **Group** items covering the same event. A tribal paper, a wire story,
   and three Bluesky posts about one ruling are one story. Merge existing
   stories if they turn out to be the same event; keep the older id.
2. **Desk.** `news`: sovereignty, land, water, treaty and federal policy,
   courts, elections, health, education, language, economy, environment.
   `culture`: film, TV, music, fashion, awards, sports, athletes, actors,
   musicians, influencers, creators, powwows as events. When a story is
   both, it goes where its consequence is.
3. **Lead.** Prefer tier 1 Native press, then the original reporter over
   an aggregator, then the most complete account. A social post leads only
   when it is itself the news.
4. **Rank** within each desk by importance to Indian Country. Breadth of
   coverage is one signal, not the signal. Recency breaks ties.
5. **Headline.** One sentence, present tense, names the actor, no
   adjectives, no exclamation marks, no emoji, under 110 characters.
   Voice is plain and PNW-quiet. Do not quote; the lead line carries
   attribution.
6. **Stability.** Keep an existing story's headline unless new coverage
   materially changes what is known.
7. Return every unplaced new item in `unplaced_item_ids`. Never drop one.

### 7.3 Hard limits (enforced in code, not trusted to the model)

- Culture & Sports desk: at most 32 stories; 12 shown on the front page.
- News desk: at most 8 stories; all shown. Never padded.
- Every `lead_item_id`, coverage id, and discussion id must exist in the
  database and belong to at most one story.
- Headline length over 110 characters, a story with no lead, or an
  unknown id rejects the response. One retry with the validation errors
  appended. A second failure keeps the previous page (see § 9).
- Stories whose newest item is older than 48 hours expire at the end of
  the run.

### 7.4 Model configuration

- `claude-opus-5-5` via the `anthropic` Python SDK. Thinking is on by
  default on this model and cannot be disabled; `output_config.effort`
  set explicitly to `medium`.
- Server-side refusal fallbacks enabled (`fallbacks: "default"` with the
  `server-side-fallback-2026-07-01` beta).
- System prompt is static and carries a `cache_control` breakpoint; the
  per-run page state and new items come after it.
- `max_tokens` 16000, non-streaming; a page state is a few thousand
  output tokens.
- Switching to `claude-sonnet-5-5` is a one-line config change and JP's
  call; it is not the default.

### 7.5 Cost controls

- Curation is skipped when zero new items arrived since the last
  successful curation.
- `config.toml` sets a monthly token ceiling (default 5,000,000 input
  tokens). When exceeded, curation is skipped and the run logs it; render
  and publish still run.
- The `runs` table records input, output, and cache-read tokens per run.
  Expected order of magnitude: a few cents per run, a dollar or so a
  day. Real numbers come from the first week's log.

## 8. The page

### 8.1 Design system

The PHYLO **editorial** subsystem, verbatim. `colors_and_type.css` is
copied from the design handoff bundle into `ntvstreams/static/` with an
attribution header, and `site.css` adds only layout and the components
below. Rules from the system that bind here: Newsreader serif for
headlines, IBM Plex Sans for body and nav, JetBrains Mono for kickers,
square corners, hairline rules instead of cards, white paper, no emoji,
no exclamation marks, italic-in-brand as the only emphasis device.

### 8.2 Layout

Max width 1200px, 24px gutters. Two columns, 62 / 38, 48px gap. Under
760px: one column, main first, then news, then river. No JavaScript.

**Masthead.** 2px ink rule above, 1px below, 18px / 16px padding.
Left: `NTVstreams` in Newsreader 500 at 22px, letter-spacing -0.014em.
Right: mono kicker `UPDATED 06:40 PT` then sans nav at 13.5px / 500:
Front Page · River · Sources · About. Current page in `--brand` with a
1px underline.

**Culture & Sports desk (main column).** Section kicker `CULTURE & SPORTS` in mono caps,
`--ls-mono`, `--muted`, under a 2px ink rule. Then up to 12 story blocks:

- Kicker line, mono 10.5px caps: lead source name in `--brand`, author if
  known, relative age (`2h`, `yesterday`), separated by middle dots.
- Headline in Newsreader 400, linked to the lead URL. Story 1 at 32px /
  line-height 1.05; stories 2 to 12 at 24px / 1.1. Color `--ink`; hover
  underlines in `--brand`.
- `More:` line, sans 13.5px, `--ink-soft`: every coverage outlet by name,
  each a link, comma separated.
- `Discussion:` line, same style: Bluesky as `@handle`, Reddit as
  `r/Sub`, YouTube as channel name, each a link. Omitted when empty.
- 1px `--rule-soft` below, 24px padding.

**News (right column, top).** Kicker `NEWS` under
a 2px ink rule. Up to 8 compact blocks: same kicker line; headline at 19px
Newsreader; one combined `More:` line that includes discussion; 16px
padding; hairline below.

**River (right column, below news).** Kicker `RIVER`. Every item from
the last 48 hours, newest first, grouped under hour markers (`6 AM`,
`5 AM`, `YESTERDAY 11 PM`) in mono. Each line: source kicker in mono
`--muted`, then the item title in sans 14.5px linked to the item. The
front page shows the latest 60; `river.html` shows all.

**Footer.** PHYLO footer treatment: 1px ink rule, mono kicker, sans
body in `--muted`. Text: "Headlines are written by an automated editor
from the linked sources and refreshed hourly. Every source is listed on
the Sources page." Links: Sources · About · RSS.

### 8.3 Pages and outputs

| path | content |
|---|---|
| `index.html` | front page as above |
| `river.html` | full 48-hour river |
| `sources.html` | generated from `sources.yaml` + `source_state`: name, kind, tier, last success, stale flag |
| `about.html` | what this is, how curation works, how to suggest a source (mailto) |
| `feed.xml` | RSS 2.0 of front-page stories: title = headline, link = lead URL, description = More/Discussion lines as text, guid = story id |
| `status.json` | last run id, started/finished, items_new, curated, tokens, errors, per-source last_success |
| `static/` | the two CSS files and favicon |

## 9. Operations

### 9.1 Runtime

- Host for phase 1: JP's laptop (`Neo`). launchd user agent
  `com.ntv.streams`, `StartInterval` 3600, `RunAtLoad` true. Logs to
  `~/Library/Logs/ntvstreams.out.log` and `.err.log`.
- Missed runs while the laptop sleeps are fine; the 48-hour window
  catches up on the next run.
- A lock file in `data/` prevents overlapping runs. A lock older than
  30 minutes is treated as stale and removed.
- Secrets: `ANTHROPIC_API_KEY` only, set in the plist's
  `EnvironmentVariables` or read from `~/.config/ntvstreams/env`. Never
  in the repo. Bluesky, Reddit, YouTube need no credentials.

**Addendum (2026-10-11).** Production moved off the laptop to a scheduled
GitHub Actions workflow (`.github/workflows/run.yml`, hourly) that
authenticates to Claude with Workload Identity Federation instead of an
API key: the job's GitHub OIDC token is exchanged for a short-lived token
acting as an Anthropic service account, with the federation rule pinned
to `repo:jeanpaulcozzatti/ntv:ref:refs/heads/main`. `data/ntv.db` is
carried between runs in the Actions cache; `out/` deploys to GitHub Pages
(`https://jeanpaulcozzatti.github.io/ntv/`). launchd remains the local
development runner. Phase 3 (S3 + CloudFront) can replace Pages without
changing the pipeline.

### 9.2 Publish backends

| backend | config | phase |
|---|---|---|
| `local` | `publish.target = "/Users/jeanpaul/Dev/ntv/out"` (default: render in place) or any directory | 1 and 2 |
| `s3` | `bucket`, `prefix`, `cloudfront_distribution_id` for invalidation; `--dry-run` prints the sync plan | 3 |

Reading in phase 1 is `file:///…/out/index.html` or `tailscale serve`
pointed at `out/` when Tailscale is on, which also gives phone access.

### 9.3 Failure modes

| failure | behaviour |
|---|---|
| One source errors or times out | logged to `runs.errors` and `source_state`; run continues |
| 24 consecutive failures on a source | `sources.html` marks it stale; it keeps being tried |
| Curator API error, refusal, or twice-invalid schema | previous stories kept; river updates; page republishes; error logged; `status.json` shows `curated: 0` |
| Zero new items | curator skipped; render and publish still run so the timestamp moves |
| Monthly token ceiling hit | same as zero new items, with a logged reason |
| Render or template error | run aborts before publish; previous `out/` untouched |
| Overlapping run | second run exits immediately |

### 9.4 Observability

`runs` table, `status.json`, per-source state on `sources.html`, and
the masthead timestamp. No alerting in v1.

## 10. Repository and stack

```
ntv/
  pyproject.toml            # uv-managed; python >= 3.12
  sources.yaml
  config.toml               # publish backend, token ceiling, model id, window hours
  ntvstreams/
    __init__.py
    cli.py                  # argparse: run | fetch | curate | render | publish
    config.py               # pydantic settings from config.toml + env
    store.py                # SQLite schema + queries
    canonical.py            # URL canonicalization, pure
    fetchers/
      base.py               # Fetcher protocol: fetch(source, state) -> (items, state)
      rss.py  bluesky.py  reddit.py  youtube.py
    curator.py              # build prompt, call Claude, validate, apply
    render.py               # Jinja2 environment + page builders
    publish.py              # local + s3 backends
    templates/              # base.html index.html river.html sources.html about.html feed.xml
    static/                 # colors_and_type.css (from PHYLO), site.css, favicon.svg
  infra/
    com.ntv.streams.plist
  data/                     # ntv.db, lock (gitignored)
  out/                      # rendered site (gitignored)
  tests/
    fixtures/               # recorded feeds and model responses
  docs/superpowers/specs/
```

Dependencies: `feedparser`, `httpx`, `pyyaml`, `jinja2`, `pydantic`,
`anthropic`, `python-ulid`. Dev: `pytest`, `pytest-httpx` or `respx`.
SQLite via the standard library. No frontend toolchain.

## 11. Testing

Each unit is testable without network or model.

- **Fetchers.** Recorded fixtures per kind. Assert fixture to normalized
  items; malformed or empty input yields zero items plus an error record,
  never an exception. One live smoke test per kind marked `slow`, skipped
  by default.
- **Canonicalization.** Table-driven: tracking params stripped, AMP
  unwrapped, trailing slash and `www.` normalized, shortener resolved
  from a stub, two syndications of one article hash equal, two different
  articles never do. Social post with outbound link yields article +
  discussion link.
- **Store.** Temp-file SQLite: insert, dedupe on re-insert, prune by age,
  items-since query, atomic story replacement.
- **Curator.** Fake client with canned `PageOut` responses. Assert: the
  culture cap of 32 and news cap of 8 are enforced, an item the model
  omits lands in the river, a story with no lead rejects the response
  and the previous page is kept, a once-invalid response is retried with
  errors appended, tokens land in `runs`. One opt-in live test runs the
  real prompt on a fixed 30-item set and prints the result for review,
  asserting nothing.
- **Renderer.** Fixture page state to HTML. Assert story counts, desk
  order, every link resolves to an item, headline text present. One
  golden `index.html` guards against accidental design drift; updated
  deliberately.
- **End to end.** All stages with fixtures and the fake curator against a
  temp directory. Assert `out/` contains the five pages, `feed.xml`, and
  a valid `status.json`.
- **Not tested.** The launchd plist; the S3 backend beyond `--dry-run`.

## 12. Phases

1. **Laptop, personal.** This spec. Done when `ntv run` has produced a
   front page from live sources on `Neo` three hours in a row under
   launchd.
2. **Mac mini.** Same code, same plist, `publish.local` pointed at a
   directory the mini's existing Tailscale Serve or Caddy exposes under a
   path. Requires the mini awake and SSH reachable.
3. **Public.** `publish.s3` to a bucket behind CloudFront on a domain JP
   chooses. Add the public domain to `about.html` and `feed.xml`.

## 13. Decisions recorded

- Name: **NTVstreams**. Repo `~/Dev/ntv`.
- Design: PHYLO editorial subsystem, tokens copied verbatim.
- Platforms: open APIs only (Bluesky, Reddit, YouTube). X deferred.
- Curation: cluster, rank, rewrite; two desks; Culture & Sports is the main
  column and gets four fifths of the page, News is a fixed side strip of one
  fifth, never mixed into the culture ranking. (Swapped from the original
  split on 2026-10-11.)
- Model: Claude Opus 5.5, effort medium, structured output, fallbacks on.
- Host: JP's laptop first; Mac mini when awake; public later.
- Cross-border (First Nations / APTN) coverage: source present but off
  until JP decides.
