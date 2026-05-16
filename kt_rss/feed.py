"""Atom/RSS-serialisering med feedgen (spec SS9).

Feed-items innehåller ENDAST title, subtitle (som summary) och länk.
`bodytext` finns inte ens i datamodellen (se db.Article) - det är ett
medvetet upphovsrättsbeslut (spec SS7) och får inte återinföras här.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from urllib.parse import quote

from feedgen.feed import FeedGenerator

from kt_rss.config import Settings


def _parse(dt: str | None) -> datetime | None:
    """ISO 8601-sträng till tidszonsmedveten datetime."""
    if not dt:
        return None
    try:
        parsed = datetime.fromisoformat(dt)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _feed_updated(articles: list[sqlite3.Row]) -> datetime:
    """Feed-`updated` = senaste last_seen/published_at i urvalet (spec SS9)."""
    newest: datetime | None = None
    for a in articles:
        for candidate in (_parse(a["published_at"]), _parse(a["last_seen"])):
            if candidate and (newest is None or candidate > newest):
                newest = candidate
    return newest or datetime.now(timezone.utc)


def build_feed(
    settings: Settings,
    articles: list[sqlite3.Row],
    *,
    section: str | None = None,
    tag: str | None = None,
    fmt: str = "atom",
) -> bytes:
    """Bygger en Atom- (default) eller RSS-feed av artikelraderna."""
    fg = FeedGenerator()

    if tag:
        feed_url = f"{settings.public_url}/feed/t/{quote(tag)}.xml"
        title = f"Kyrkans Tidning - tagg: {tag}"
    elif section:
        feed_url = f"{settings.public_url}/feed/{section}.xml"
        title = f"Kyrkans Tidning - {section}"
    else:
        feed_url = f"{settings.public_url}/feed.xml"
        title = "Kyrkans Tidning"

    fg.id(feed_url)
    fg.title(title)
    fg.description("Inofficiella RSS-feeds för Kyrkans Tidning")
    fg.link(href=settings.base_url, rel="alternate")
    fg.link(href=feed_url, rel="self")
    fg.language("sv")
    fg.author({"name": "Kyrkans Tidning"})
    fg.updated(_feed_updated(articles))

    for a in articles:
        fe = fg.add_entry(order="append")
        fe.id(a["url"])
        fe.guid(a["url"], permalink=True)
        fe.title(a["title"] or "(utan rubrik)")
        fe.link(href=a["url"])
        published = _parse(a["published_at"]) or datetime.now(timezone.utc)
        fe.published(published)
        fe.updated(published)
        # summary = subtitle; faller tillbaka på kicker om subtitle är tom.
        summary = a["subtitle"] or a["kicker"]
        if summary:
            fe.summary(summary)
        if a["author"]:
            fe.author({"name": a["author"]})
        if a["section"]:
            fe.category(term=a["section"], label=a["section"])

    if fmt == "rss":
        return fg.rss_str(pretty=True)
    return fg.atom_str(pretty=True)
