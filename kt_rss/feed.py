"""Atom/RSS-serialisering med feedgen (spec SS9).

Feed-items innehåller ENDAST title, subtitle (som summary) och länk.
`bodytext` finns inte ens i datamodellen (se db.Article) - det är ett
medvetet upphovsrättsbeslut (spec SS7) och får inte återinföras här.
"""

from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote

from feedgen.feed import FeedGenerator

from kt_rss.config import IMAGE_API_BASE, Settings


def build_image_url(
    image_id: str,
    *,
    width: int | None = None,
    height: int | None = None,
    fmt: str = "webp",
) -> str:
    """Bygger en bild-URL mot KT:s bild-API (spec SS2.2).

    Crop låses till full bild (0/0/100/100) - feeden och webui vill ha hela
    motivet, inte en panorama-crop. `width`/`height` ger en nedskalad
    rendition (KT:s egen listvy använder 240x156); utan dem svarar API:t med
    en standardstorlek. Verifierat 200 + image/webp i bootstrap
    (tests/fixtures/image-api-findings.md).
    """
    iid = str(image_id).strip()
    url = (
        f"{IMAGE_API_BASE}/{iid}.{fmt}?imageId={iid}"
        f"&x=0&y=0&cropw=100&croph=100"
        f"&heightx=0&heighty=0&heightw=100&heighth=100"
    )
    if width:
        url += f"&width={width}"
    if height:
        url += f"&height={height}"
    return f"{url}&format={fmt}"


_ATOM_NS = "http://www.w3.org/2005/Atom"


def _fix_atom_enclosure_links(xml: bytes) -> bytes:
    """Sätter rel/type/length på Atom-entryns bild-enclosure-länkar.

    feedgen 1.0.0 (entry.py:140) skuggar dict-variabeln `link` med ett nytt
    XML-element, så rel/type/length tappas på alla Atom-entry-länkar. Bild-
    länkarna pekar på bild-API:t och kan därför identifieras säkert. Guarden
    `not link.get("rel")` gör fixen till en no-op om feedgen rättar buggen.
    """
    ET.register_namespace("", _ATOM_NS)
    root = ET.fromstring(xml)
    for link in root.iter(f"{{{_ATOM_NS}}}link"):
        href = link.get("href", "")
        if href.startswith(IMAGE_API_BASE) and not link.get("rel"):
            link.set("rel", "enclosure")
            link.set("type", "image/webp")
            link.set("length", "0")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


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
    """Feed-`updated` = senaste publicering/ändring i urvalet (spec SS9).

    Bygger på published_at och modified_at, inte last_seen: last_seen bumpas
    vid varje poll och skulle få feedens <updated> - och därmed ETag:en i
    _feed_response - att skifta utan att innehållet faktiskt ändrats.
    """
    newest: datetime | None = None
    for a in articles:
        for candidate in (_parse(a["published_at"]), _parse(a["modified_at"])):
            if candidate and (newest is None or candidate > newest):
                newest = candidate
    return newest or datetime.now(timezone.utc)


def build_feed(
    settings: Settings,
    articles: list[sqlite3.Row],
    *,
    section: str | None = None,
    tag: str | None = None,
    tags: list[str] | None = None,
    author: str | None = None,
    mode: str = "or",
    fmt: str = "atom",
) -> bytes:
    """Bygger en Atom- (default) eller RSS-feed av artikelraderna."""
    fg = FeedGenerator()

    if tags:
        joined = quote(",".join(tags))
        feed_url = f"{settings.public_url}/feed/tags.xml?t={joined}&mode={mode}"
        sep = " och " if mode == "and" else " eller "
        title = f"Kyrkans Tidning - taggar: {sep.join(tags)}"
    elif author:
        feed_url = f"{settings.public_url}/feed/a/{quote(author)}.xml"
        title = f"Kyrkans Tidning - {author}"
    elif tag:
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
        # Bild-enclosure (spec SS2.2). RSS får ett korrekt <enclosure>;
        # Atom-länken lagas i efterhand pga en feedgen-bug (se
        # _fix_atom_enclosure_links).
        if settings.include_image_enclosure and a["image_id"]:
            fe.link(
                href=build_image_url(a["image_id"]),
                rel="enclosure",
                type="image/webp",
                length="0",
            )

    if fmt == "rss":
        return fg.rss_str(pretty=True)
    return _fix_atom_enclosure_links(fg.atom_str(pretty=True))


def build_opml(settings: Settings, sections: list[str]) -> bytes:
    """OPML 2.0 med projektets feeds (totalt + per sektion) för import i en
    RSS-läsare i ett svep. URL:erna är absoluta via `public_url`.
    """
    base = settings.public_url
    opml = ET.Element("opml", version="2.0")
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = "KT-RSS - Kyrkans Tidning"
    body = ET.SubElement(opml, "body")
    ET.SubElement(
        body, "outline", text="Alla artiklar", type="rss",
        xmlUrl=f"{base}/feed.xml", htmlUrl=f"{base}/articles",
    )
    for section in sections:
        ET.SubElement(
            body, "outline", text=section, type="rss",
            xmlUrl=f"{base}/feed/{quote(section)}.xml",
            htmlUrl=f"{base}/s/{quote(section)}",
        )
    return ET.tostring(opml, encoding="utf-8", xml_declaration=True)
