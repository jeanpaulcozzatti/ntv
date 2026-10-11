"""The curation step: one structured-output call to Claude per run.

Input: the current page plus items fetched since the last curation.
Output: a full new page state, validated here before it touches the store.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

import anthropic
from pydantic import BaseModel, ConfigDict, ValidationError
from ulid import ULID

from .config import Config
from .models import Item, Story, StoryMember
from .sources import Source
from .store import Store

NEWS_MAX = 8
CULTURE_MAX = 48
CHOCTAW_MAX = 8
PNW_MAX = 12
DESK_CAPS = {"news": NEWS_MAX, "culture": CULTURE_MAX, "choctaw": CHOCTAW_MAX, "pnw": PNW_MAX}
HEADLINE_MAX = 110
EXCERPT_MAX = 300
MAX_OUTPUT_TOKENS = 64000


class StoryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None
    desk: Literal["news", "culture", "choctaw", "pnw"]
    headline: str
    lead_item_id: str
    coverage_item_ids: list[str]
    discussion_item_ids: list[str]
    rank: int
    rationale: str


class PageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stories: list[StoryOut]
    unplaced_item_ids: list[str]


SYSTEM_PROMPT = f"""We are the automated editor of NTVstreams, a front page for Native American news and social media in the style of Techmeme. Each run we receive the page as it stands and the items that arrived since the last run, and we return the whole page again.

What we do, in priority order.

1. Group. Items that cover the same event are one story. A tribal paper, a wire story, and three Bluesky posts about one court ruling are one story. If two existing stories turn out to be the same event, merge them and keep the older id.

2. Desk. Every story is on one desk. Check the regional desks first, then the topic desks.
   choctaw: stories primarily about the Choctaw Nation of Oklahoma (CNO), the Mississippi Band of Choctaw Indians, the Jena Band of Choctaw Indians, or Choctaw people and institutions, whatever the topic. Tribal government, the Choctaw Cultural Center, Choctaw athletes and artists, and Choctaw language all belong here.
   pnw: stories primarily about the tribes of the Pacific Northwest: Washington, Oregon, Idaho, and the Salish Sea. Examples are Tulalip, Muckleshoot, Suquamish, Puyallup, Nisqually, Squaxin Island, Lummi, Swinomish, Snoqualmie, Yakama, Colville, Spokane, Umatilla, Warm Springs, Cowlitz, Makah, Quinault, and Coeur d'Alene. Treaty fisheries, salmon, and shared regional issues belong here when the story is about those tribes.
   A story merely mentioning a Choctaw or Northwest person in passing is not a regional story. Put it on a topic desk.
   news: sovereignty, land, water, treaty and federal policy, courts, elections, health, education, language, economy, environment.
   culture: film, TV, music, fashion, awards, sports, athletes, actors, musicians, influencers, creators, and powwows as events.
   When a story is both news and culture, it goes where its consequence is. When a story fits both regional desks, choose the one it is mostly about.

3. Lead. Prefer tier 1 Native press, then the original reporter over an aggregator, then the most complete account. A social post leads only when it is itself the news. The lead is the item whose headline and link anchor the story.

4. Rank within each desk by importance to Indian Country. Breadth of coverage is one signal, not the signal. Recency breaks ties. Rank 1 is the top story.

5. Headline. One sentence, present tense, names the actor, no adjectives, no exclamation marks, no emoji, under {HEADLINE_MAX} characters. Plain and quiet, Pacific Northwest tone. Do not quote; the lead line carries attribution. Use the reader's words, not the outlet's.

6. Stability. Keep an existing story's id and headline unless new coverage materially changes what is known. Attach new items to existing stories where they belong.

7. Every item we were given and did not place goes in unplaced_item_ids. We never drop one.

Limits the page enforces: at most {NEWS_MAX} news stories, {CULTURE_MAX} culture stories, {CHOCTAW_MAX} choctaw stories, and {PNW_MAX} pnw stories; ranks unique within a desk; every item id in at most one story; the lead is not repeated in coverage or discussion; coverage holds articles and videos, discussion holds social posts.

Items are given with their source, tier (1 Native press, 2 institutional, 3 wide net, 4 social), desk_hint (a hint, not a rule), kind, title, excerpt, time, and engagement. An item marked discussion_of links to another item id; it belongs in that item's story as discussion.

Return only the JSON object described by the schema: stories, each with id (existing id or null), desk, headline, lead_item_id, coverage_item_ids, discussion_item_ids, rank, and a one-line rationale; and unplaced_item_ids."""


@dataclass
class CuratorResponse:
    text: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int


class CuratorError(Exception):
    """Raised by CuratorClient.complete on API error or refusal."""


class CuratorClient(Protocol):
    def complete(self, system: str, user: str) -> CuratorResponse: ...


class AnthropicCurator:
    def __init__(self, model: str, effort: str, client: anthropic.Anthropic | None = None):
        self.model = model
        self.effort = effort
        self.client = client or anthropic.Anthropic()

    def complete(self, system: str, user: str) -> CuratorResponse:
        try:
            # The page is returned whole, including every unplaced item id, so a backlog run can be long.
            # Stream so a large max_tokens never trips the SDK's non-streaming timeout guard.
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": PageOut.model_json_schema()},
                },
            ) as stream:
                response = stream.get_final_message()
        except anthropic.RateLimitError as e:
            raise CuratorError(f"rate limited: {e}") from e
        except anthropic.APIStatusError as e:
            raise CuratorError(f"api status {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise CuratorError(f"connection: {e}") from e
        except Exception as e:  # anything else the SDK raises (missing credentials, bad params) must not break the page
            raise CuratorError(f"{e.__class__.__name__}: {e}") from e
        if response.stop_reason == "refusal":
            raise CuratorError("refusal")
        if response.stop_reason == "max_tokens":
            raise CuratorError(f"output truncated at {MAX_OUTPUT_TOKENS} tokens")
        text = next((b.text for b in response.content if b.type == "text"), "")
        usage = response.usage
        return CuratorResponse(
            text=text,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )


def _item_view(it: Item, sources_by_id: dict[str, Source], discussion_of: dict[str, str]) -> dict:
    src = sources_by_id.get(it.source_id)
    view = {
        "id": it.id,
        "source": src.name if src else it.source_id,
        "tier": src.tier if src else 4,
        "desk_hint": src.desk_hint if src else "any",
        "kind": it.kind,
        "title": it.title,
        "excerpt": (it.excerpt or "")[:EXCERPT_MAX] or None,
        "published_at": it.published_at.isoformat(timespec="minutes"),
        "engagement": it.engagement,
    }
    if it.id in discussion_of:
        view["discussion_of"] = discussion_of[it.id]
    return view


def build_user_message(
    current: list[Story],
    members: dict[str, list[StoryMember]],
    items: dict[str, Item],
    new_items: list[Item],
    sources_by_id: dict[str, Source],
    discussion_of: dict[str, str] | None = None,
) -> str:
    discussion_of = discussion_of or {}
    page = []
    for s in sorted(current, key=lambda s: (s.desk, s.rank, s.id)):
        lead = items.get(s.lead_item_id)
        page.append({
            "id": s.id,
            "desk": s.desk,
            "rank": s.rank,
            "headline": s.headline,
            "lead": {"source": sources_by_id[lead.source_id].name if lead and lead.source_id in sources_by_id else (lead.source_id if lead else None), "title": lead.title if lead else None, "id": s.lead_item_id},
            "member_item_ids": sorted(m.item_id for m in members.get(s.id, [])),
        })
    fresh = [_item_view(it, sources_by_id, discussion_of) for it in sorted(new_items, key=lambda i: i.id)]
    return (
        "CURRENT PAGE (stories, oldest rank first within desk):\n"
        + json.dumps(page, sort_keys=True, indent=1)
        + "\n\nNEW ITEMS since the last run:\n"
        + json.dumps(fresh, sort_keys=True, indent=1)
    )


def validate_page(page: PageOut, known_item_ids: set[str], existing_story_ids: set[str]) -> list[str]:
    errors: list[str] = []
    seen_items: Counter[str] = Counter()
    seen_story_ids: Counter[str] = Counter()
    ranks: dict[str, Counter[int]] = {desk: Counter() for desk in DESK_CAPS}
    desks = Counter(s.desk for s in page.stories)
    for desk, cap in DESK_CAPS.items():
        if desks[desk] > cap:
            errors.append(f"{desk} desk has {desks[desk]} stories; the limit is {cap}")
    for s in page.stories:
        label = s.id or f"new story ranked {s.rank} on {s.desk}"
        if s.id is not None:
            seen_story_ids[s.id] += 1
            if s.id not in existing_story_ids:
                errors.append(f"story id {s.id} does not exist; use null for a new story")
        hl = s.headline.strip()
        if not hl:
            errors.append(f"{label}: headline is empty")
        elif len(hl) > HEADLINE_MAX:
            errors.append(f"{label}: headline is {len(hl)} characters; the limit is {HEADLINE_MAX}")
        if not s.lead_item_id:
            errors.append(f"{label}: no lead item")
        ranks[s.desk][s.rank] += 1
        for iid in [s.lead_item_id, *s.coverage_item_ids, *s.discussion_item_ids]:
            if iid not in known_item_ids:
                errors.append(f"{label}: item id {iid} is unknown")
            seen_items[iid] += 1
        if s.lead_item_id in s.coverage_item_ids or s.lead_item_id in s.discussion_item_ids:
            errors.append(f"{label}: lead item {s.lead_item_id} is repeated in coverage or discussion")
    for sid, n in seen_story_ids.items():
        if n > 1:
            errors.append(f"story id {sid} appears twice")
    for iid, n in seen_items.items():
        if n > 1:
            errors.append(f"item {iid} appears in more than one story or role")
    for desk, c in ranks.items():
        dup = [r for r, n in c.items() if n > 1]
        if dup:
            errors.append(f"{desk} desk has duplicate rank values {sorted(dup)}")
    return errors


@dataclass
class CurateOutcome:
    curated: bool
    reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    error: str | None = None


def _apply(page: PageOut, store: Store, existing: dict[str, Story], run_id: str, now: datetime,
           discussion_of: dict[str, str] | None = None) -> None:
    stories: list[Story] = []
    members: list[StoryMember] = []
    discussion_of = discussion_of or {}
    named = {i for s in page.stories for i in [s.lead_item_id, *s.coverage_item_ids, *s.discussion_item_ids]}
    for s in page.stories:
        if s.id and s.id in existing:
            sid, created = s.id, existing[s.id].created_at
        else:
            sid, created = str(ULID.from_datetime(now)), now
        stories.append(Story(id=sid, run_id=run_id, desk=s.desk, headline=s.headline.strip(), lead_item_id=s.lead_item_id,
                             rank=s.rank, rationale=s.rationale.strip(), created_at=created, updated_at=now))
        members.append(StoryMember(sid, s.lead_item_id, "lead"))
        members.extend(StoryMember(sid, i, "coverage") for i in s.coverage_item_ids)
        members.extend(StoryMember(sid, i, "discussion") for i in s.discussion_item_ids)
        # posts that link to any article in this story follow it as discussion
        article_ids = {s.lead_item_id, *s.coverage_item_ids}
        for post_id, article_id in discussion_of.items():
            if article_id in article_ids and post_id not in named:
                members.append(StoryMember(sid, post_id, "discussion"))
                named.add(post_id)
    store.replace_page(stories, members, run_id, now)


def curate_stage(store: Store, client: CuratorClient, config: Config, sources_by_id: dict[str, Source],
                 run_id: str, now: datetime) -> CurateOutcome:
    last = store.last_curation_at()
    fresh = store.items_fetched_after(last, now, config.window_hours)
    if not fresh:
        return CurateOutcome(False, "no_new_items")
    if store.month_input_tokens(now) >= config.monthly_input_token_ceiling:
        return CurateOutcome(False, "token_ceiling")

    current = store.active_stories()
    existing = {s.id: s for s in current}
    members = {s.id: store.story_members(s.id) for s in current}
    placed = {m.item_id for ms in members.values() for m in ms}
    window_items = store.items_in_window(now, config.window_hours)
    items = {i.id: i for i in window_items}
    for s in current:
        for m in members[s.id]:
            if m.item_id not in items:
                items.update(store.get_items([m.item_id]))
    discussion_of: dict[str, str] = {}
    for it in window_items:
        if it.kind == "article":
            for post in store.discussion_for(it.id):
                discussion_of[post.id] = it.id
    # Offer every window item that is in no story. Articles and videos stay in play until they expire;
    # standalone posts are offered once (posts that link to an article follow it automatically in _apply).
    fresh_ids = {i.id for i in fresh}
    new_items = [i for i in window_items if i.id not in placed and (i.kind != "post" or i.id in fresh_ids or i.id in discussion_of)]
    known = set(items) | {i.id for i in new_items}

    user = build_user_message(current, members, items, new_items, sources_by_id, discussion_of)
    totals = [0, 0, 0]
    errors: list[str] = []
    for attempt in range(2):
        prompt = user if attempt == 0 else user + "\n\nYour previous response had these errors:\n" + "\n".join(f"- {e}" for e in errors)
        try:
            resp = client.complete(SYSTEM_PROMPT, prompt)
        except CuratorError as e:
            return CurateOutcome(False, "api_error", *totals, error=str(e))
        totals[0] += resp.input_tokens
        totals[1] += resp.output_tokens
        totals[2] += resp.cache_read_tokens
        try:
            page = PageOut.model_validate_json(resp.text)
        except ValidationError as e:
            errors = [f"response did not match the schema: {e.errors()[0].get('msg', 'invalid')} at {e.errors()[0].get('loc')}"]
            continue
        errors = validate_page(page, known, set(existing))
        if not errors:
            _apply(page, store, existing, run_id, now, discussion_of)
            return CurateOutcome(True, "ok", *totals)
    return CurateOutcome(False, "invalid_response", *totals, error="; ".join(errors))
