"""Stage orchestration. The only module that knows the stage order."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import httpx

from .fetchers import fail_state, get_fetcher
from .sources import Source
from .store import Store


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
            summary.items_new += store.upsert_items(result.items)
            for post_id, article_id in result.links:
                store.add_discussion_link(post_id, article_id)
        if result.state is not None:
            store.set_source_state(result.state)
        if result.error:
            summary.errors.append({"source_id": source.id, "message": result.error})
    return summary
