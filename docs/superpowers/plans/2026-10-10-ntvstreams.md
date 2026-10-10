# NTVstreams Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python batch pipeline that ingests Native American news and social feeds, has Claude cluster and headline them into two desks, and renders a static Techmeme-style site in the PHYLO editorial design, running hourly under launchd on JP's laptop.

**Architecture:** Four stages behind one CLI (`ntv run` = fetch → curate → render → publish), sharing a SQLite store. Fetchers are pluggable per source kind; the curator is one structured-output call to Claude behind a protocol so tests inject a fake; the renderer is a pure function of database state; publishers are `local` now and `s3` later.

**Tech Stack:** Python 3.12, uv, pydantic v2, feedparser, httpx, PyYAML, Jinja2, anthropic SDK, python-ulid, sqlite3 (stdlib), pytest + respx.

**Spec:** `docs/superpowers/specs/2026-10-10-ntvstreams-design.md`

## Global Constraints

- Python `>= 3.12`, managed by `uv`; run everything as `uv run …`.
- All datetimes are timezone-aware UTC in code and storage (ISO 8601 strings in SQLite); rendered in `America/Los_Angeles`, labelled `PT`.
- Every stage takes `now: datetime` as a parameter; nothing calls `datetime.now()` except `cli.py`.
- Item id = `sha256(canonical_url).hexdigest()[:16]`. Story and run ids are ULIDs.
- Desk caps: news ≤ 32 stories (12 shown), culture ≤ 8 (all shown). Headline ≤ 110 chars. Window 48 h. Prune items > 7 days, expired stories > 30 days.
- Model `claude-opus-5-5`, `output_config.effort = "medium"`, `max_tokens = 16000`, fallbacks on, static system prompt with `cache_control`.
- Design: PHYLO editorial. Newsreader serif headlines, IBM Plex Sans body, JetBrains Mono kickers, square corners, hairline rules, white paper. **No emoji, no exclamation marks** anywhere in templates, copy, or prompt output. Site name is `NTVstreams`.
- No JavaScript in the rendered site. Jinja autoescape on.
- Secrets only from environment (`ANTHROPIC_API_KEY`). `data/`, `out/`, `.env` gitignored.
- Commit after every task with a conventional message ending in the `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` line.

## Review Focus

1. **Feed entry with no date** (common in small WordPress feeds): item must be stored with `published_at = fetched_at`, not dropped or crashed. Test in Task 4.
2. **A feed URL that returns an HTML page with HTTP 200** (dead feed, soft 404): fetcher returns zero items and an error string, never raises. Test in Task 4.
3. **Model places one item in two stories, or reuses a story id twice**: `validate_page` rejects both. Test in Task 9.
4. **Naive datetimes in feeds** (no tz suffix): treated as UTC, and the river's hour grouping is computed in PT after conversion, including across a DST boundary. Tests in Tasks 2 and 10.
5. **Empty or whitespace title** on a social post with no text (image-only Bluesky post): item title falls back to `"{source name} post"` so the river never renders a blank link. Test in Task 5.

---

## File structure

```
ntv/
  pyproject.toml  config.toml  sources.yaml  README.md
  ntvstreams/
    __init__.py  cli.py  config.py  sources.py  models.py  canonical.py
    store.py  curator.py  render.py  publish.py  pipeline.py
    fetchers/__init__.py  base.py  rss.py  bluesky.py  reddit.py  youtube.py
    templates/base.html index.html river.html sources.html about.html feed.xml
    static/colors_and_type.css  site.css  favicon.svg
  infra/com.ntv.streams.plist
  tests/  conftest.py  test_*.py  fixtures/
```

`models.py` holds the shared dataclasses every module imports; `store.py` is the only module that writes SQL; `pipeline.py` is the only module that knows the stage order.

---

### Task 1: Scaffold, config, sources

**Files:**
- Create: `pyproject.toml`, `config.toml`, `sources.yaml`, `ntvstreams/__init__.py`, `ntvstreams/config.py`, `ntvstreams/sources.py`, `tests/conftest.py`, `tests/test_config.py`, `tests/test_sources.py`

**Interfaces:**
- Produces:
  ```python
  # config.py
  class PublishConfig(BaseModel):
      backend: Literal["local", "s3"] = "local"
      target: str = "out"                 # local: directory
      bucket: str | None = None
      prefix: str = ""
      cloudfront_distribution_id: str | None = None
  class Config(BaseModel):
      model: str = "claude-opus-5-5"
      effort: Literal["low", "medium", "high"] = "medium"
      window_hours: int = 48
      monthly_input_token_ceiling: int = 5_000_000
      data_dir: Path = Path("data")
      out_dir: Path = Path("out")
      site_url: str = ""                  # "" in phase 1; absolute URL in phase 3
      timezone: str = "America/Los_Angeles"
      publish: PublishConfig = PublishConfig()
  def load_config(path: Path = Path("config.toml")) -> Config   # tomllib; missing file -> defaults
  # sources.py
  Kind = Literal["rss", "bluesky", "reddit", "youtube"]
  class Source(BaseModel):
      id: str; name: str; kind: Kind
      url: str | None = None; query: str | None = None; actor: str | None = None
      subreddit: str | None = None; channel_id: str | None = None
      tier: Literal[1, 2, 3, 4]; desk_hint: Literal["news", "culture", "any"] = "any"
      active: bool = True
      # model_validator: rss needs url; bluesky needs exactly one of query/actor;
      # reddit needs subreddit; youtube needs channel_id
  def load_sources(path: Path = Path("sources.yaml")) -> list[Source]   # all, including inactive
  ```

- [ ] **Step 1: Create `pyproject.toml`** with project name `ntvstreams`, `requires-python = ">=3.12"`, dependencies `feedparser`, `httpx`, `pyyaml`, `jinja2`, `pydantic>=2`, `anthropic`, `python-ulid`; dev group `pytest`, `respx`; script `ntv = "ntvstreams.cli:main"`; `[tool.pytest.ini_options] markers = ["slow: live network", "live: calls the Claude API"]` and `addopts = "-m 'not slow and not live'"`. Run `uv sync` and confirm `.venv` exists.
- [ ] **Step 2: Write failing tests** in `tests/test_config.py` and `tests/test_sources.py`:
  ```python
  def test_defaults_when_file_missing(tmp_path):
      cfg = load_config(tmp_path / "nope.toml")
      assert cfg.model == "claude-opus-5-5" and cfg.window_hours == 48 and cfg.publish.backend == "local"
  def test_reads_toml(tmp_path):
      (tmp_path / "c.toml").write_text('model="claude-sonnet-5-5"\n[publish]\nbackend="s3"\nbucket="b"\n')
      cfg = load_config(tmp_path / "c.toml")
      assert cfg.model == "claude-sonnet-5-5" and cfg.publish.bucket == "b"
  def test_bluesky_requires_query_or_actor():
      with pytest.raises(ValidationError): Source(id="x", name="x", kind="bluesky", tier=4)
  def test_load_starter_sources():
      srcs = load_sources(Path("sources.yaml"))
      assert len(srcs) >= 30 and len({s.id for s in srcs}) == len(srcs)
      assert sum(1 for s in srcs if s.desk_hint == "culture") >= 6
  ```
- [ ] **Step 3: Run, expect ImportError.** `uv run pytest tests/test_config.py tests/test_sources.py -v`
- [ ] **Step 4: Implement `config.py` and `sources.py`** per the Interfaces block.
- [ ] **Step 5: Write `sources.yaml`** with the spec § 4.2 starter list: every tier-1 and tier-2 outlet named, the two Google News queries, APTN with `active: false`, four Bluesky searches, three subreddits, and the culture sources. Use your best-known feed URL for each (`/feed/` for WordPress sites); Task 8 verifies them. Leave YouTube `channel_id` entries out until Task 8 finds them. Write `config.toml` with the defaults spelled out.
- [ ] **Step 6: Run tests, expect PASS.** Same command.
- [ ] **Step 7: Commit** `feat: scaffold project, config and source loading`.

### Task 2: Models and canonicalization

**Files:**
- Create: `ntvstreams/models.py`, `ntvstreams/canonical.py`, `tests/test_canonical.py`

**Interfaces:**
- Produces:
  ```python
  # models.py (all @dataclass(frozen=True) unless noted)
  class Item: id: str; source_id: str; url: str; canonical_url: str
      kind: Literal["article","post","video"]; title: str; author: str | None
      excerpt: str | None; published_at: datetime; fetched_at: datetime
      engagement: int = 0; raw: dict = field(default_factory=dict)
  class Story: id: str; run_id: str; desk: Literal["news","culture"]; headline: str
      lead_item_id: str; rank: int; rationale: str; created_at: datetime; updated_at: datetime
      expired_at: datetime | None = None
  class StoryMember: story_id: str; item_id: str; role: Literal["lead","coverage","discussion"]
  class SourceState: source_id: str; etag: str | None = None; last_modified: str | None = None
      last_success_at: datetime | None = None; consecutive_fails: int = 0; last_error: str | None = None
  class Run: id: str; started_at: datetime; finished_at: datetime | None = None
      stage_reached: str = "fetch"; items_new: int = 0; curated: bool = False
      input_tokens: int = 0; output_tokens: int = 0; cache_read_tokens: int = 0
      errors: list[dict] = field(default_factory=list)
  # canonical.py
  TRACKING_PARAMS: frozenset[str]   # utm_* prefix handled separately + fbclid gclid mc_cid mc_eid ref source s ncid igshid
  SHORTENER_HOSTS: frozenset[str]   # t.co bit.ly buff.ly ow.ly
  def canonicalize(url: str, resolve: Callable[[str], str | None] | None = None) -> str
  def item_id(canonical_url: str) -> str
  def make_excerpt(html: str | None, limit: int = 300) -> str | None
  def ensure_utc(dt: datetime | None, fallback: datetime) -> datetime   # naive -> UTC; None -> fallback
  ```

- [ ] **Step 1: Write failing table-driven tests** in `tests/test_canonical.py`:
  ```python
  @pytest.mark.parametrize("raw,expected", [
    ("https://www.navajotimes.com/a/b/?utm_source=x&utm_medium=y", "https://navajotimes.com/a/b"),
    ("http://Example.com:80/x/amp/", "http://example.com/x"),
    ("https://amp.example.com/x?fbclid=1#frag", "https://example.com/x"),
    ("https://example.com/x?b=2&a=1&s=99", "https://example.com/x?a=1&b=2"),
    ("https://example.com/x/", "https://example.com/x"),
  ])
  def test_canonicalize(raw, expected): assert canonicalize(raw) == expected
  def test_shortener_uses_resolver():
      assert canonicalize("https://t.co/abc", resolve=lambda u: "https://example.com/real/") == "https://example.com/real"
  def test_shortener_resolver_failure_keeps_original():
      assert canonicalize("https://t.co/abc", resolve=lambda u: None) == "https://t.co/abc"
  def test_syndications_hash_equal():
      assert item_id(canonicalize("https://x.com/p?utm_campaign=a")) == item_id(canonicalize("https://www.x.com/p/"))
  def test_item_id_len(): assert len(item_id("https://example.com/x")) == 16
  def test_excerpt_strips_html_and_cuts_on_word():
      out = make_excerpt("<p>Hello&nbsp;<b>world</b> " + "word " * 100 + "</p>", limit=40)
      assert "<" not in out and len(out) <= 40 and not out.endswith(" ")
  def test_ensure_utc_naive_is_utc():
      assert ensure_utc(datetime(2026,10,10,6,0), fallback=NOW).tzinfo == timezone.utc
  def test_ensure_utc_none_uses_fallback(): assert ensure_utc(None, fallback=NOW) == NOW
  ```
- [ ] **Step 2: Run, expect ImportError.** `uv run pytest tests/test_canonical.py -v`
- [ ] **Step 3: Implement `models.py` and `canonical.py`.** `canonicalize`: resolve shortener first (only when host in `SHORTENER_HOSTS` and `resolve` given), then unwrap AMP (`/amp` path segment, `amp.` host prefix, `*.cdn.ampproject.org` → decode path), lowercase scheme and host, drop `www.` and default ports, drop tracking params and any `utm_*`, sort remaining query, strip fragment and trailing slash. `make_excerpt`: `html.unescape` after tag strip, collapse whitespace, cut at last space before `limit`.
- [ ] **Step 4: Run tests, expect PASS.**
- [ ] **Step 5: Commit** `feat: shared models and URL canonicalization`.

### Task 3: SQLite store

**Files:**
- Create: `ntvstreams/store.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: `models.py` dataclasses.
- Produces:
  ```python
  class Store:
      def __init__(self, db_path: Path): ...           # creates schema (spec § 5) if missing; WAL mode
      def upsert_items(self, items: list[Item]) -> int  # returns number newly inserted; existing rows refresh engagement+raw only
      def add_discussion_link(self, post_item_id: str, article_item_id: str) -> None
      def discussion_for(self, article_item_id: str) -> list[Item]
      def get_items(self, ids: Iterable[str]) -> dict[str, Item]
      def items_in_window(self, now: datetime, window_hours: int) -> list[Item]   # published_at desc
      def items_fetched_after(self, ts: datetime | None, now: datetime, window_hours: int) -> list[Item]  # ts None -> all in window
      def active_stories(self) -> list[Story]                                      # expired_at IS NULL, by desk, rank
      def story_members(self, story_id: str) -> list[StoryMember]
      def replace_page(self, stories: list[Story], members: list[StoryMember], run_id: str, now: datetime) -> None
          # one transaction: stories not in list -> expired_at=now; others upsert (keep created_at); story_items rewritten
      def expire_stories_older_than(self, cutoff: datetime, now: datetime) -> int   # newest member published_at < cutoff
      def prune(self, now: datetime) -> None                                       # items >7d (+links, orphan story_items), expired stories >30d
      def get_source_state(self, source_id: str) -> SourceState
      def set_source_state(self, state: SourceState) -> None
      def start_run(self, now: datetime) -> Run
      def finish_run(self, run: Run) -> None
      def last_run(self) -> Run | None
      def last_curation_at(self) -> datetime | None       # finished_at of last run with curated=1
      def month_input_tokens(self, now: datetime) -> int  # sum input_tokens for runs started in now's UTC month
  ```

- [ ] **Step 1: Write failing tests** in `tests/test_store.py` using a `tmp_path` db and an `item()` factory in `tests/conftest.py` (`def make_item(id="a"*16, source_id="src", published_at=NOW, **kw) -> Item`):
  ```python
  def test_upsert_counts_new_and_refreshes_engagement(store): ...  # insert 2 -> 2; re-insert one with engagement=9 -> 0 new, engagement 9
  def test_items_in_window_excludes_old(store): ...               # 47h in, 49h out
  def test_items_fetched_after(store): ...                         # ts None returns all; ts after one item's fetched_at returns the other
  def test_replace_page_expires_missing_and_keeps_created_at(store): ...
  def test_story_members_roundtrip(store): ...
  def test_expire_stories_older_than(store): ...
  def test_prune_removes_old_items_and_links(store): ...
  def test_source_state_default_then_set(store): ...               # unknown id -> SourceState(source_id=..., consecutive_fails=0)
  def test_runs_last_curation_and_month_tokens(store): ...         # two runs, one curated; month sum ignores previous month
  ```
- [ ] **Step 2: Run, expect ImportError.**
- [ ] **Step 3: Implement `store.py`** with the spec § 5 DDL in a `SCHEMA` string executed with `executescript`. Dates stored as `isoformat()`; parsed back with `datetime.fromisoformat`. `raw` and `errors` as JSON text.
- [ ] **Step 4: Run tests, expect PASS.**
- [ ] **Step 5: Commit** `feat: sqlite store`.

### Task 4: Fetcher protocol and RSS fetcher

**Files:**
- Create: `ntvstreams/fetchers/__init__.py`, `ntvstreams/fetchers/base.py`, `ntvstreams/fetchers/rss.py`, `tests/test_fetch_rss.py`, `tests/fixtures/rss_wordpress.xml`, `tests/fixtures/rss_nodate.xml`, `tests/fixtures/soft404.html`

**Interfaces:**
- Consumes: `Source`, `SourceState`, `Item`, `canonicalize`, `item_id`, `make_excerpt`, `ensure_utc`.
- Produces:
  ```python
  # base.py
  @dataclass
  class FetchResult:
      items: list[Item]
      links: list[tuple[str, str]]       # (post_item_id, article_item_id)
      state: SourceState
      error: str | None = None
  class Fetcher(Protocol):
      def fetch(self, source: Source, state: SourceState, http: httpx.Client, now: datetime) -> FetchResult: ...
  USER_AGENT = "NTVstreams/0.1 (+https://github.com/jpcozzatti/ntv)"
  def get_fetcher(kind: Kind) -> Fetcher        # in fetchers/__init__.py; registry dict
  def ok_state(state, now) -> SourceState         # last_success_at=now, consecutive_fails=0, last_error=None
  def fail_state(state, error) -> SourceState     # consecutive_fails+1, last_error=error
  def make_resolver(http: httpx.Client) -> Callable[[str], str | None]
      # HEAD with follow_redirects, timeout 2 s; returns final URL or None on any error; fetchers pass this to canonicalize
  # rss.py
  class RssFetcher:  # sends If-None-Match / If-Modified-Since from state; 304 -> no items, ok_state
  ```
  Item mapping: `kind="article"`, `url=entry.link`, `canonical_url=canonicalize(url)`, `title=entry.title` stripped (fallback `f"{source.name} item"`), `author=entry.get("author")`, `excerpt=make_excerpt(entry.summary or content)`, `published_at=ensure_utc(published_parsed or updated_parsed, now)`, `fetched_at=now`, `raw=entry` (JSON-safe subset: link,title,author,summary,published).

- [ ] **Step 1: Create fixtures.** `rss_wordpress.xml`: a 3-entry RSS 2.0 feed with authors, `pubDate`, HTML in `<description>`, one link carrying `?utm_source=rss`. `rss_nodate.xml`: 1 entry with no `pubDate`/`updated`. `soft404.html`: `<html><body>Not found</body></html>`.
- [ ] **Step 2: Write failing tests** using `respx` to mock `source.url`:
  ```python
  def test_rss_maps_entries(respx_mock): ... # 3 items, kind article, first item canonical_url has no utm, excerpt has no '<', author set
  def test_rss_missing_date_uses_now(respx_mock): ... # published_at == NOW
  def test_rss_sends_conditional_headers_and_handles_304(respx_mock): ... # state.etag="e1" -> request has If-None-Match; 304 -> items == [] and error is None
  def test_rss_stores_etag_from_response(respx_mock): ... # result.state.etag == "e2"
  def test_rss_soft_404_html_returns_error(respx_mock): ... # 200 with soft404.html -> items == [], error contains "no entries", consecutive_fails == 1
  def test_rss_network_error_returns_error(respx_mock): ... # side_effect httpx.ConnectError -> error set, no raise
  ```
- [ ] **Step 3: Run, expect ImportError.**
- [ ] **Step 4: Implement `base.py`, `rss.py`, registry in `__init__.py`.** Use `feedparser.parse(response.content)`; treat `bozo` with zero entries as the soft-404 error. Timeout 20 s.
- [ ] **Step 5: Run tests, expect PASS.**
- [ ] **Step 6: Commit** `feat: fetcher protocol and rss fetcher`.

### Task 5: Bluesky fetcher

**Files:**
- Create: `ntvstreams/fetchers/bluesky.py`, `tests/test_fetch_bluesky.py`, `tests/fixtures/bsky_search.json`, `tests/fixtures/bsky_author_feed.json`

**Interfaces:**
- Produces: `class BlueskyFetcher` registered as `"bluesky"`. Endpoints: `https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?q={query}&limit=50&sort=latest` when `source.query`; `…/app.bsky.feed.getAuthorFeed?actor={actor}&limit=50&filter=posts_no_replies` when `source.actor`. Post URL = `https://bsky.app/profile/{author.handle}/post/{rkey}` where `rkey` is the last segment of `post.uri`. Post item: `kind="post"`, `title = record.text` collapsed to one line, cut at 140 chars; empty → `f"{source.name} post"`; `author = author.displayName or handle`; `engagement = likeCount + repostCount`; `published_at = record.createdAt`. If `post.embed` (or `record.embed`) is `app.bsky.embed.external` with a non-bsky URI: also emit an `article` item (`title = external.title or host`, `excerpt = make_excerpt(external.description)`, `published_at` = post's) and a `(post_id, article_id)` link.

- [ ] **Step 1: Create fixtures** from the real API shape: `bsky_search.json` with 3 posts in `posts[]`, one carrying an external embed to `https://ictnews.org/news/x?utm_source=bsky`, one with empty text and an image embed. `bsky_author_feed.json` with 2 entries in `feed[].post`.
- [ ] **Step 2: Write failing tests:**
  ```python
  def test_search_posts_mapped(respx_mock): ... # 3 post items + 1 article item; 1 link; article canonical has no utm
  def test_empty_text_title_fallback(respx_mock): ... # title == "Bluesky · \"Indian Country\" post" style: f"{source.name} post"
  def test_author_feed_endpoint_used(respx_mock): ... # source.actor -> getAuthorFeed called, 2 items
  def test_http_error_returns_error(respx_mock): ... # 500 -> error, no raise
  ```
- [ ] **Step 3: Run, expect failure. Step 4: Implement. Step 5: Run, expect PASS.**
- [ ] **Step 6: Commit** `feat: bluesky fetcher`.

### Task 6: Reddit fetcher

**Files:**
- Create: `ntvstreams/fetchers/reddit.py`, `tests/test_fetch_reddit.py`, `tests/fixtures/reddit_new.json`

**Interfaces:**
- Produces: `class RedditFetcher` registered as `"reddit"`. Endpoint `https://www.reddit.com/r/{subreddit}/new.json?limit=50&raw_json=1`, header `User-Agent: USER_AGENT`. For each `data.children[].data`: post item `kind="post"`, `url = "https://www.reddit.com" + permalink`, `title`, `author`, `excerpt = make_excerpt(selftext)`, `published_at = created_utc`, `engagement = score`. If `is_self` is false and `url` host is not `reddit.com`/`redd.it`/`i.redd.it`/`v.redd.it`: also emit an `article` item with `title` = post title and a link.

- [ ] **Step 1: Fixture** with 3 children: one self post, one link post to `https://www.navajotimes.com/x/`, one image post to `i.redd.it`.
- [ ] **Step 2: Failing tests:** `test_reddit_mapped` (3 posts + 1 article + 1 link), `test_reddit_user_agent_sent`, `test_reddit_429_returns_error`.
- [ ] **Step 3–5: fail → implement → pass.**
- [ ] **Step 6: Commit** `feat: reddit fetcher`.

### Task 7: YouTube fetcher

**Files:**
- Create: `ntvstreams/fetchers/youtube.py`, `tests/test_fetch_youtube.py`, `tests/fixtures/youtube_feed.xml`

**Interfaces:**
- Produces: `class YoutubeFetcher` registered as `"youtube"`. Endpoint `https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}`; parse with feedparser; `kind="video"`, `url = entry.link`, `title`, `author = entry.author`, `excerpt = make_excerpt(media_description)`, `published_at = published_parsed`, `engagement = int(media_statistics.views)` when present else 0.

- [ ] **Step 1: Fixture**: 2-entry YouTube Atom feed including `media:group` with `media:description` and `media:community/media:statistics views="1234"`.
- [ ] **Step 2: Failing tests:** `test_youtube_mapped` (2 video items, engagement 1234 on first), `test_youtube_error`.
- [ ] **Step 3–5: fail → implement → pass.**
- [ ] **Step 6: Commit** `feat: youtube fetcher`.

### Task 8: Fetch stage, source check command, live source verification

**Files:**
- Create: `ntvstreams/pipeline.py` (fetch stage only for now), `ntvstreams/cli.py`, `tests/test_fetch_stage.py`
- Modify: `sources.yaml`

**Interfaces:**
- Produces:
  ```python
  # pipeline.py
  @dataclass
  class FetchSummary: items_new: int; errors: list[dict]   # {"source_id":..., "message":...}
  def fetch_stage(sources: list[Source], store: Store, http: httpx.Client, now: datetime) -> FetchSummary
      # active sources only; per source: get_fetcher(kind).fetch -> upsert_items, add links, set_source_state; one failure never stops the loop
  # cli.py
  def main(argv: list[str] | None = None) -> int
      # subcommands: run | fetch | curate | render | publish | check-sources ; flags --config PATH, --dry-run
      # check-sources: fetch every source (active or not) once with fresh state, print a table: id, kind, items, error, and exit 1 if any active source errored
  ```

- [ ] **Step 1: Failing tests** in `tests/test_fetch_stage.py`: two rss sources via respx, one good and one 500 → `items_new == 3`, `errors` has one entry for the bad id, `store.get_source_state(bad).consecutive_fails == 1`, good source `last_success_at == NOW`. Inactive source is never requested (`respx` route `call_count == 0`).
- [ ] **Step 2: Run, expect failure. Step 3: Implement `fetch_stage` and `cli.py` with `check-sources` and `fetch`** (other subcommands print "not implemented" and exit 2 for now). **Step 4: Run tests, expect PASS.**
- [ ] **Step 5: Live verification.** Run `uv run ntv check-sources`. For each failing source: find the correct feed URL (try `/feed/`, `/rss`, `/feed/rss`, the site's `<link rel="alternate" type="application/rss+xml">`), or remove the source and note it in the commit message. Find `channel_id` for ICT, Native America Calling, and two or three culture channels (view-source of the channel page, `"channelId"`), and add them. Repeat until every active source returns items and the culture desk has at least six supplying sources. Expected: exit 0.
- [ ] **Step 6: Commit** `feat: fetch stage and check-sources; verify starter sources`.

### Task 9: Curator

**Files:**
- Create: `ntvstreams/curator.py`, `tests/test_curator.py`, `tests/fixtures/page_out_ok.json`, `tests/fixtures/items_30.json`

**Interfaces:**
- Consumes: `Store`, `Config`, `Item`, `Story`, `StoryMember`, `Source` (for tier/desk_hint/name lookup: pass `sources_by_id: dict[str, Source]`).
- Produces:
  ```python
  NEWS_MAX, CULTURE_MAX, HEADLINE_MAX = 32, 8, 110
  class StoryOut(BaseModel):
      id: str | None; desk: Literal["news","culture"]; headline: str; lead_item_id: str
      coverage_item_ids: list[str]; discussion_item_ids: list[str]; rank: int; rationale: str
      model_config = ConfigDict(extra="forbid")
  class PageOut(BaseModel):
      stories: list[StoryOut]; unplaced_item_ids: list[str]
      model_config = ConfigDict(extra="forbid")
  SYSTEM_PROMPT: str      # spec § 7.2 verbatim, plus the output schema description; contains no dates or ids
  def build_user_message(current: list[Story], members: dict[str, list[StoryMember]],
                         items: dict[str, Item], new_items: list[Item],
                         sources_by_id: dict[str, Source]) -> str   # two JSON blocks, keys sorted
  @dataclass
  class CuratorResponse: text: str; input_tokens: int; output_tokens: int; cache_read_tokens: int
  class CuratorError(Exception): ...   # raised by CuratorClient.complete on API error or refusal
  class CuratorClient(Protocol):
      def complete(self, system: str, user: str) -> CuratorResponse: ...
  class AnthropicCurator:
      def __init__(self, model: str, effort: str, client: anthropic.Anthropic | None = None): ...
      def complete(self, system, user) -> CuratorResponse
  def validate_page(page: PageOut, known_item_ids: set[str], existing_story_ids: set[str]) -> list[str]
      # errors for: unknown item id; item in two stories or two roles; lead also in coverage; headline > 110 or empty;
      # id not in existing_story_ids; duplicate story id; news > 32; culture > 8; duplicate rank within desk
  @dataclass
  class CurateOutcome: curated: bool; reason: str; input_tokens: int = 0; output_tokens: int = 0; cache_read_tokens: int = 0; error: str | None = None
  def curate_stage(store: Store, client: CuratorClient, config: Config, sources_by_id: dict[str, Source],
                   run_id: str, now: datetime) -> CurateOutcome
  ```
  `AnthropicCurator.complete` calls:
  ```python
  self.client.beta.messages.create(
      model=self.model, max_tokens=16000,
      betas=["server-side-fallback-2026-07-01"], fallbacks="default",
      system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
      messages=[{"role": "user", "content": user}],
      output_config={"effort": self.effort,
                     "format": {"type": "json_schema", "schema": PageOut.model_json_schema()}},
  )
  ```
  then `text = next(b.text for b in response.content if b.type == "text")`; tokens from `response.usage.input_tokens / output_tokens / cache_read_input_tokens or 0`. If `response.stop_reason == "refusal"` raise `CuratorError("refusal")`. Catch `anthropic.RateLimitError`, `anthropic.APIStatusError`, `anthropic.APIConnectionError` separately and re-raise as `CuratorError(str(e))`.

  `curate_stage` flow: `new_items = store.items_fetched_after(store.last_curation_at(), now, window)`; if empty → `CurateOutcome(False, "no_new_items")`. If `store.month_input_tokens(now) >= ceiling` → `(False, "token_ceiling")`. Build message; `client.complete`; `PageOut.model_validate_json`; `validate_page`; on pydantic or validation errors, retry **once** with the user message plus `"\n\nYour previous response had these errors:\n- …"`; on second failure → `(False, "invalid_response", error=…)` leaving the page untouched. On success: map `StoryOut` → `Story` (new id = ULID; existing keeps `created_at`; `updated_at=now`; `run_id`), build members (lead/coverage/discussion), `store.replace_page`, `store.expire_stories_older_than(now - window)`. Return tokens summed over both attempts.

- [ ] **Step 1: Fixtures.** `items_30.json`: 30 realistic items across sources and both desks (hand-written, titles only need to be plausible). `page_out_ok.json`: a valid `PageOut` over a subset of those ids with 3 news and 2 culture stories and 4 unplaced ids.
- [ ] **Step 2: Failing tests** with `class FakeCurator: def __init__(self, responses: list[str]) ...; complete() pops next; records calls`:
  ```python
  def test_skips_when_no_new_items(store, cfg): ...                      # reason == "no_new_items"; fake never called
  def test_skips_when_ceiling_hit(store, cfg): ...                        # seed a run with input_tokens=ceiling
  def test_applies_valid_page(store, cfg): ...                            # 5 active stories, members roles correct, tokens recorded
  def test_unplaced_items_remain_in_window(store, cfg): ...               # items_in_window still contains unplaced ids
  def test_retries_once_with_errors_then_keeps_previous_page(store, cfg): # responses [bad, bad]; curated False; second call's user text contains "errors"; previous stories unchanged
  def test_validate_rejects_item_in_two_stories(): ...
  def test_validate_rejects_duplicate_story_id(): ...
  def test_validate_rejects_long_headline(): ...
  def test_validate_rejects_culture_over_8(): ...
  def test_existing_story_keeps_id_and_created_at(store, cfg): ...        # second curate with id reused
  def test_build_user_message_is_deterministic(): ...                     # same inputs -> identical string
  def test_system_prompt_has_no_exclamation_or_emoji(): ...
  @pytest.mark.live
  def test_live_prompt_prints_result(): ...                               # AnthropicCurator on items_30; print; assert nothing
  ```
- [ ] **Step 3: Run, expect failure. Step 4: Implement `curator.py`.** Write `SYSTEM_PROMPT` from spec § 7.2 in full, in the "we" voice, ending with the JSON contract and the sentence "Return every item you did not place in unplaced_item_ids." **Step 5: Run tests, expect PASS.**
- [ ] **Step 6: One live call.** With `ANTHROPIC_API_KEY` set: `uv run pytest -m live -s tests/test_curator.py`. Read the printed page: headlines in register, desks sensible, no exclamation marks. If the SDK rejects `fallbacks="default"`, switch to `betas=["server-side-fallback-2026-06-01"], fallbacks=[{"model": "claude-opus-4-8"}]` and note it in the commit.
- [ ] **Step 7: Commit** `feat: curator with structured output and validation`.

### Task 10: Renderer, templates, PHYLO styles

**Files:**
- Create: `ntvstreams/render.py`, `ntvstreams/templates/{base,index,river,sources,about}.html`, `ntvstreams/templates/feed.xml`, `ntvstreams/static/colors_and_type.css` (copied from `~/Dev/cozzatticom-handoff-ref/bii-phylo-design-system/project/colors_and_type.css` with a 3-line attribution comment on top), `ntvstreams/static/site.css`, `ntvstreams/static/favicon.svg`, `tests/test_render.py`, `tests/fixtures/golden_index.html`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class StoryView: story: Story; lead: Item; lead_source: Source; coverage: list[tuple[Item, Source]]
                   discussion: list[tuple[Item, Source]]
  @dataclass
  class PageData: updated_at: datetime; news: list[StoryView]; culture: list[StoryView]
                  river: list[Item]; sources_by_id: dict[str, Source]; states: dict[str, SourceState]
                  site_url: str
  def build_page_data(store: Store, sources: list[Source], config: Config, now: datetime) -> PageData
      # news = top 12 by rank; culture = all (<=8); river = items_in_window newest first
  def render_site(data: PageData, out_dir: Path, static_src: Path | None = None) -> list[Path]
      # writes index.html river.html sources.html about.html feed.xml and copies static/; returns paths
  # Jinja filters: pt_time(dt)->"06:40 PT"; age(dt, now)->"2h"|"yesterday"|"3d"; hour_group(dt)->"6 AM"|"YESTERDAY 11 PM";
  #   social_label(item, source)->"@handle"|"r/Sub"|channel name ; river limited to 60 on index
  ```
  Layout and copy are spec § 8.2–8.3 exactly. `base.html` carries the masthead with `NTVstreams` lockup, mono `UPDATED {{ pt_time }}` kicker, nav with `current` class, and the footer disclosure sentence verbatim.

- [ ] **Step 1: Copy the PHYLO CSS and write `site.css`** for: `.page` (max-width 1200, 24px gutters), `.grid` (62/38, 48px gap; single column under 760px), `.masthead` (2px/1px ink rules per the design kit), `.desk-head` (2px rule + mono kicker), `.story` sizes (32/24/19 serif), `.kicker`, `.more`, `.river`, `.hour`, `.footer`. Only tokens from `colors_and_type.css`; no new colors.
- [ ] **Step 2: Failing tests** with a fixture `PageData` built from 4 news stories, 2 culture stories, 15 river items, two sources with one stale state:
  ```python
  def test_index_structure(rendered): ... # 4 .story in main, first has class story--top, 2 in culture; order by rank; "UPDATED" present
  def test_every_link_is_an_item_or_page(rendered): ...
  def test_more_and_discussion_lines(rendered): ... # "More:" lists coverage source names; "Discussion:" shows "@handle" and "r/IndianCountry"; omitted when empty
  def test_no_exclamation_or_script(rendered): ... # "!" not in any html text node (allow <!doctype>), "<script" absent
  def test_river_hour_groups_in_pt_across_dst(): ... # DST ends 2026-11-01 09:00Z. hour_group of 08:30Z, 09:30Z, 10:30Z == ["1 AM", "1 AM", "2 AM"] (a fixed -7 offset would wrongly give "2 AM" for 09:30Z)
  def test_sources_page_marks_stale(rendered): ... # consecutive_fails >= 24 -> text "stale"
  def test_feed_xml_valid(rendered): ... # parses with xml.etree; item count == 6; guid == story id
  def test_golden_index(rendered): ... # compare to fixtures/golden_index.html after normalizing whitespace; regenerate with UPDATE_GOLDEN=1
  def test_title_escaped(rendered): ... # a title containing "<b>" renders as "&lt;b&gt;"
  ```
- [ ] **Step 3: Run, expect failure. Step 4: Implement `render.py` and templates.** Use `zoneinfo.ZoneInfo(config.timezone)` for all PT conversions. **Step 5: Run tests, expect PASS; generate the golden file once and review it in a browser** (`open out/index.html` on a render of the fixture) against spec § 8.2 before committing it.
- [ ] **Step 6: Commit** `feat: renderer and PHYLO editorial templates`.

### Task 11: Publishers

**Files:**
- Create: `ntvstreams/publish.py`, `tests/test_publish.py`

**Interfaces:**
- Produces:
  ```python
  class Publisher(Protocol):
      def publish(self, out_dir: Path, dry_run: bool = False) -> list[str]: ...   # human-readable actions
  class LocalPublisher:  def __init__(self, target: Path)   # if target.resolve()==out_dir.resolve(): no-op; else mirror (copy new/changed, delete removed)
  class S3Publisher:     def __init__(self, bucket: str, prefix: str, distribution_id: str | None, runner: Callable[[list[str]], int] = subprocess.call)
      # runs ["aws","s3","sync",str(out_dir),f"s3://{bucket}/{prefix}","--delete"] then, if distribution_id,
      # ["aws","cloudfront","create-invalidation","--distribution-id",id,"--paths","/*"]; dry_run returns the commands without running
  def get_publisher(config: Config) -> Publisher
  ```

- [ ] **Step 1: Failing tests:** `test_local_mirror_copies_and_deletes`, `test_local_same_dir_is_noop`, `test_s3_dry_run_lists_commands` (no runner call), `test_s3_runs_sync_then_invalidation` (fake runner records argv), `test_get_publisher_by_backend`.
- [ ] **Step 2–4: fail → implement → pass.**
- [ ] **Step 5: Commit** `feat: local and s3 publishers`.

### Task 12: Pipeline run, status.json, lock, CLI, end-to-end

**Files:**
- Modify: `ntvstreams/pipeline.py`, `ntvstreams/cli.py`
- Create: `tests/test_pipeline.py`

**Interfaces:**
- Produces:
  ```python
  @contextmanager
  def run_lock(path: Path, stale_minutes: int = 30) -> Iterator[None]   # raises AlreadyRunning if a fresh lock exists; removes stale lock
  def write_status(run: Run, store: Store, sources: list[Source], out_dir: Path) -> Path   # out_dir/status.json per spec § 8.3
  def run_all(config: Config, sources: list[Source], store: Store, http: httpx.Client,
              curator: CuratorClient, publisher: Publisher, now: datetime, dry_run: bool = False) -> Run
      # start_run -> fetch_stage -> curate_stage -> build_page_data+render_site -> write_status -> publisher.publish -> finish_run -> store.prune
      # curate failure or skip never stops render/publish; render exception sets stage_reached="render", records error, skips publish, re-raises after finish_run
  ```
  `cli.py main`: builds `Config`, `Store(config.data_dir/"ntv.db")`, `httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True)`, `AnthropicCurator(config.model, config.effort)`, `get_publisher(config)`, `now=datetime.now(timezone.utc)`; dispatches `run` → `run_all`; `fetch`/`curate`/`render`/`publish` run just that stage against the store. Exit 0 on success, 1 on any recorded error, 3 on `AlreadyRunning`.

- [ ] **Step 1: Failing tests:**
  ```python
  def test_run_all_end_to_end(tmp_path, respx_mock): ...   # 2 rss sources mocked + FakeCurator(page_out_ok) + LocalPublisher(tmp_path/"site");
      # assert site has index.html river.html sources.html about.html feed.xml status.json static/site.css; run.stage_reached=="done"; status.json["curated"] is True
  def test_curator_failure_still_publishes(tmp_path, respx_mock): ...  # FakeCurator raising CuratorError -> curated False, index.html exists, run.errors has stage "curate"
  def test_lock_blocks_second_run(tmp_path): ...
  def test_stale_lock_is_removed(tmp_path): ...
  def test_cli_run_exit_codes(tmp_path, monkeypatch): ...  # monkeypatch AnthropicCurator to FakeCurator; exit 0
  ```
- [ ] **Step 2: Run, expect failure. Step 3: Implement. Step 4: Run the whole suite:** `uv run pytest -v` → all PASS.
- [ ] **Step 5: First real run on this laptop.** `ANTHROPIC_API_KEY=… uv run ntv run` then `open out/index.html`. Check the page against spec § 8.2 and the `runs` row for tokens. Fix anything visibly wrong in templates or prompt as small follow-up commits.
- [ ] **Step 6: Commit** `feat: pipeline run, status.json, lock and cli`.

### Task 13: launchd on the laptop, README, phase 1 acceptance

**Files:**
- Create: `infra/com.ntv.streams.plist`, `README.md`
- Modify: `.gitignore` (ensure `data/`, `out/`, `.env`)

- [ ] **Step 1: Write the plist.** Label `com.ntv.streams`; `ProgramArguments` = `/opt/homebrew/bin/uv run --project /Users/jeanpaul/Dev/ntv ntv run` (confirm `which uv` and adjust); `WorkingDirectory` `/Users/jeanpaul/Dev/ntv`; `StartInterval` 3600; `RunAtLoad` true; `EnvironmentVariables` with `PATH` including `/opt/homebrew/bin:/usr/bin:/bin` and `ANTHROPIC_API_KEY` read at install time from `~/.config/ntvstreams/env` (document: user creates that file, mode 600; the committed plist contains a placeholder `__ANTHROPIC_API_KEY__` that the install command substitutes with `sed` into `~/Library/LaunchAgents/`, never back into the repo). `StandardOutPath`/`StandardErrorPath` → `~/Library/Logs/ntvstreams.out.log` / `.err.log`.
- [ ] **Step 2: Write `README.md`**: what it is, quickstart (`uv sync`, `ntv check-sources`, `ntv run`, `open out/index.html`), the install command for the plist, logs, how to add a source, how to switch publish backend, link to spec and plan.
- [ ] **Step 3: Install and verify.** Run the install command; `launchctl list | grep com.ntv.streams` shows it; `tail -f ~/Library/Logs/ntvstreams.out.log` shows the first run. Confirm `git status` shows no secret and `grep -r sk-ant . --exclude-dir=.venv` is empty.
- [ ] **Step 4: Phase 1 acceptance.** After three consecutive hourly runs: `sqlite3 data/ntv.db "select id, stage_reached, items_new, curated, input_tokens from runs order by started_at desc limit 3"` shows three `done` rows; `out/index.html` masthead timestamp advanced each hour. Record the three rows' token totals in the README under "Observed cost".
- [ ] **Step 5: Commit** `chore: launchd agent and README; phase 1 live`.
