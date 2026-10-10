"""SQLite persistence. The only module that writes SQL."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

from ulid import ULID

from .models import Item, Run, SourceState, Story, StoryMember

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL, url TEXT NOT NULL, canonical_url TEXT NOT NULL,
  kind TEXT NOT NULL, title TEXT NOT NULL, author TEXT, excerpt TEXT,
  published_at TEXT NOT NULL, fetched_at TEXT NOT NULL, engagement INTEGER DEFAULT 0, raw TEXT
);
CREATE INDEX IF NOT EXISTS items_published ON items(published_at);
CREATE INDEX IF NOT EXISTS items_fetched ON items(fetched_at);
CREATE TABLE IF NOT EXISTS discussion_links (
  post_item_id TEXT NOT NULL, article_item_id TEXT NOT NULL,
  PRIMARY KEY (post_item_id, article_item_id)
);
CREATE TABLE IF NOT EXISTS stories (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, desk TEXT NOT NULL, headline TEXT NOT NULL,
  lead_item_id TEXT NOT NULL, rank INTEGER NOT NULL, rationale TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, expired_at TEXT
);
CREATE TABLE IF NOT EXISTS story_items (
  story_id TEXT NOT NULL, item_id TEXT NOT NULL, role TEXT NOT NULL,
  PRIMARY KEY (story_id, item_id)
);
CREATE TABLE IF NOT EXISTS source_state (
  source_id TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, last_success_at TEXT,
  consecutive_fails INTEGER DEFAULT 0, last_error TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, stage_reached TEXT,
  items_new INTEGER, curated INTEGER, input_tokens INTEGER, output_tokens INTEGER,
  cache_read_tokens INTEGER, errors TEXT
);
"""

ITEM_DAYS = 7
EXPIRED_STORY_DAYS = 30


def _ts(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _row_item(r: sqlite3.Row) -> Item:
    return Item(
        id=r["id"], source_id=r["source_id"], url=r["url"], canonical_url=r["canonical_url"],
        kind=r["kind"], title=r["title"], author=r["author"], excerpt=r["excerpt"],
        published_at=_dt(r["published_at"]), fetched_at=_dt(r["fetched_at"]),
        engagement=r["engagement"] or 0, raw=json.loads(r["raw"]) if r["raw"] else {},
    )


def _row_story(r: sqlite3.Row) -> Story:
    return Story(
        id=r["id"], run_id=r["run_id"], desk=r["desk"], headline=r["headline"],
        lead_item_id=r["lead_item_id"], rank=r["rank"], rationale=r["rationale"] or "",
        created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"]), expired_at=_dt(r["expired_at"]),
    )


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    # ── items ──────────────────────────────────────────────────────────────
    def upsert_items(self, items: list[Item]) -> int:
        """Insert new items; refresh engagement and raw on existing ones.

        A record first seen through a social link card (raw["via"]) is replaced by the
        first-party record when the outlet's own feed later serves the same URL.
        """
        new = 0
        with self.conn:
            for it in items:
                row = self.conn.execute("SELECT raw FROM items WHERE id=?", (it.id,)).fetchone()
                if row is None:
                    self.conn.execute(
                        """INSERT INTO items (id, source_id, url, canonical_url, kind, title, author, excerpt,
                           published_at, fetched_at, engagement, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (it.id, it.source_id, it.url, it.canonical_url, it.kind, it.title, it.author, it.excerpt,
                         _ts(it.published_at), _ts(it.fetched_at), it.engagement, json.dumps(it.raw, default=str)),
                    )
                    new += 1
                    continue
                existing_raw = json.loads(row["raw"]) if row["raw"] else {}
                if "via" in existing_raw and "via" not in it.raw:
                    self.conn.execute(
                        """UPDATE items SET source_id=?, url=?, kind=?, title=?, author=?, excerpt=?, published_at=?,
                           engagement=?, raw=? WHERE id=?""",
                        (it.source_id, it.url, it.kind, it.title, it.author, it.excerpt, _ts(it.published_at),
                         it.engagement, json.dumps(it.raw, default=str), it.id),
                    )
                elif "via" in it.raw and "via" not in existing_raw:
                    self.conn.execute("UPDATE items SET engagement=MAX(engagement, ?) WHERE id=?", (it.engagement, it.id))
                else:
                    self.conn.execute(
                        "UPDATE items SET engagement=?, raw=? WHERE id=?",
                        (it.engagement, json.dumps(it.raw, default=str), it.id),
                    )
        return new

    def add_discussion_link(self, post_item_id: str, article_item_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO discussion_links (post_item_id, article_item_id) VALUES (?,?)",
                (post_item_id, article_item_id),
            )

    def discussion_for(self, article_item_id: str) -> list[Item]:
        rows = self.conn.execute(
            """SELECT i.* FROM items i JOIN discussion_links d ON d.post_item_id = i.id
               WHERE d.article_item_id=? ORDER BY i.published_at DESC""",
            (article_item_id,),
        ).fetchall()
        return [_row_item(r) for r in rows]

    def get_items(self, ids: Iterable[str]) -> dict[str, Item]:
        ids = list(ids)
        out: dict[str, Item] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            q = f"SELECT * FROM items WHERE id IN ({','.join('?' * len(chunk))})"
            for r in self.conn.execute(q, chunk):
                out[r["id"]] = _row_item(r)
        return out

    def items_in_window(self, now: datetime, window_hours: int) -> list[Item]:
        cutoff = _ts(now - timedelta(hours=window_hours))
        rows = self.conn.execute(
            "SELECT * FROM items WHERE published_at >= ? ORDER BY published_at DESC", (cutoff,)
        ).fetchall()
        return [_row_item(r) for r in rows]

    def items_fetched_after(self, ts: datetime | None, now: datetime, window_hours: int) -> list[Item]:
        cutoff = _ts(now - timedelta(hours=window_hours))
        if ts is None:
            rows = self.conn.execute(
                "SELECT * FROM items WHERE published_at >= ? ORDER BY published_at DESC", (cutoff,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM items WHERE published_at >= ? AND fetched_at > ? ORDER BY published_at DESC",
                (cutoff, _ts(ts)),
            ).fetchall()
        return [_row_item(r) for r in rows]

    # ── stories ────────────────────────────────────────────────────────────
    def active_stories(self) -> list[Story]:
        rows = self.conn.execute(
            """SELECT * FROM stories WHERE expired_at IS NULL
               ORDER BY CASE desk WHEN 'news' THEN 0 ELSE 1 END, rank"""
        ).fetchall()
        return [_row_story(r) for r in rows]

    def story_members(self, story_id: str) -> list[StoryMember]:
        rows = self.conn.execute(
            "SELECT story_id, item_id, role FROM story_items WHERE story_id=? ORDER BY role, item_id", (story_id,)
        ).fetchall()
        return [StoryMember(r["story_id"], r["item_id"], r["role"]) for r in rows]

    def replace_page(self, stories: list[Story], members: list[StoryMember], run_id: str, now: datetime) -> None:
        keep = {s.id for s in stories}
        with self.conn:
            active_ids = [r["id"] for r in self.conn.execute("SELECT id FROM stories WHERE expired_at IS NULL")]
            for sid in active_ids:
                if sid not in keep:
                    self.conn.execute("UPDATE stories SET expired_at=? WHERE id=?", (_ts(now), sid))
                    self.conn.execute("DELETE FROM story_items WHERE story_id=?", (sid,))
            for s in stories:
                self.conn.execute(
                    """INSERT INTO stories (id, run_id, desk, headline, lead_item_id, rank, rationale,
                       created_at, updated_at, expired_at) VALUES (?,?,?,?,?,?,?,?,?,NULL)
                       ON CONFLICT(id) DO UPDATE SET run_id=excluded.run_id, desk=excluded.desk,
                       headline=excluded.headline, lead_item_id=excluded.lead_item_id, rank=excluded.rank,
                       rationale=excluded.rationale, updated_at=excluded.updated_at, expired_at=NULL""",
                    (s.id, run_id, s.desk, s.headline, s.lead_item_id, s.rank, s.rationale,
                     _ts(s.created_at), _ts(now)),
                )
                self.conn.execute("DELETE FROM story_items WHERE story_id=?", (s.id,))
            self.conn.executemany(
                "INSERT OR REPLACE INTO story_items (story_id, item_id, role) VALUES (?,?,?)",
                [(m.story_id, m.item_id, m.role) for m in members],
            )

    def expire_stories_older_than(self, cutoff: datetime, now: datetime) -> int:
        rows = self.conn.execute(
            """SELECT s.id, MAX(i.published_at) AS newest FROM stories s
               JOIN story_items si ON si.story_id = s.id JOIN items i ON i.id = si.item_id
               WHERE s.expired_at IS NULL GROUP BY s.id HAVING newest < ?""",
            (_ts(cutoff),),
        ).fetchall()
        with self.conn:
            for r in rows:
                self.conn.execute("UPDATE stories SET expired_at=? WHERE id=?", (_ts(now), r["id"]))
                self.conn.execute("DELETE FROM story_items WHERE story_id=?", (r["id"],))
        return len(rows)

    def prune(self, now: datetime) -> None:
        item_cutoff = _ts(now - timedelta(days=ITEM_DAYS))
        story_cutoff = _ts(now - timedelta(days=EXPIRED_STORY_DAYS))
        with self.conn:
            self.conn.execute(
                """DELETE FROM discussion_links WHERE post_item_id IN (SELECT id FROM items WHERE published_at < ?)
                   OR article_item_id IN (SELECT id FROM items WHERE published_at < ?)""",
                (item_cutoff, item_cutoff),
            )
            self.conn.execute("DELETE FROM items WHERE published_at < ?", (item_cutoff,))
            self.conn.execute("DELETE FROM story_items WHERE item_id NOT IN (SELECT id FROM items)")
            self.conn.execute(
                "DELETE FROM story_items WHERE story_id IN (SELECT id FROM stories WHERE expired_at < ?)", (story_cutoff,)
            )
            self.conn.execute("DELETE FROM stories WHERE expired_at < ?", (story_cutoff,))

    # ── source state ───────────────────────────────────────────────────────
    def get_source_state(self, source_id: str) -> SourceState:
        r = self.conn.execute("SELECT * FROM source_state WHERE source_id=?", (source_id,)).fetchone()
        if r is None:
            return SourceState(source_id=source_id)
        return SourceState(
            source_id=r["source_id"], etag=r["etag"], last_modified=r["last_modified"],
            last_success_at=_dt(r["last_success_at"]), consecutive_fails=r["consecutive_fails"] or 0,
            last_error=r["last_error"],
        )

    def set_source_state(self, state: SourceState) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO source_state
                   (source_id, etag, last_modified, last_success_at, consecutive_fails, last_error)
                   VALUES (?,?,?,?,?,?)""",
                (state.source_id, state.etag, state.last_modified, _ts(state.last_success_at),
                 state.consecutive_fails, state.last_error),
            )

    # ── runs ───────────────────────────────────────────────────────────────
    def start_run(self, now: datetime) -> Run:
        run = Run(id=str(ULID.from_datetime(now)), started_at=now)
        with self.conn:
            self.conn.execute(
                "INSERT INTO runs (id, started_at, stage_reached, curated) VALUES (?,?,?,0)",
                (run.id, _ts(now), run.stage_reached),
            )
        return run

    def finish_run(self, run: Run) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE runs SET finished_at=?, stage_reached=?, items_new=?, curated=?, input_tokens=?,
                   output_tokens=?, cache_read_tokens=?, errors=? WHERE id=?""",
                (_ts(run.finished_at), run.stage_reached, run.items_new, int(run.curated), run.input_tokens,
                 run.output_tokens, run.cache_read_tokens, json.dumps(run.errors), run.id),
            )

    def _row_run(self, r: sqlite3.Row) -> Run:
        return Run(
            id=r["id"], started_at=_dt(r["started_at"]), finished_at=_dt(r["finished_at"]),
            stage_reached=r["stage_reached"] or "fetch", items_new=r["items_new"] or 0,
            curated=bool(r["curated"]), input_tokens=r["input_tokens"] or 0, output_tokens=r["output_tokens"] or 0,
            cache_read_tokens=r["cache_read_tokens"] or 0, errors=json.loads(r["errors"]) if r["errors"] else [],
        )

    def last_run(self) -> Run | None:
        r = self.conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        return self._row_run(r) if r else None

    def last_curation_at(self) -> datetime | None:
        r = self.conn.execute(
            "SELECT finished_at FROM runs WHERE curated=1 AND finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        return _dt(r["finished_at"]) if r else None

    def month_input_tokens(self, now: datetime) -> int:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        r = self.conn.execute(
            "SELECT COALESCE(SUM(input_tokens),0) AS t FROM runs WHERE started_at >= ?", (_ts(start),)
        ).fetchone()
        return int(r["t"])
