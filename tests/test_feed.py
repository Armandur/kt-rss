"""Feed-serialisering: valid Atom/RSS, ALDRIG bodytext (spec SS7, SS9)."""

import xml.etree.ElementTree as ET
from pathlib import Path
from dataclasses import replace

from kt_rss.db import connect, get_articles, map_article, upsert_article
from kt_rss.feed import build_feed

ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _seed(db_path, raw_articles):
    conn = connect(db_path)
    for raw in raw_articles:
        upsert_article(conn, map_article(raw, "https://www.kyrkanstidning.se"))
    conn.commit()
    rows = get_articles(conn, limit=100)
    conn.close()
    for row in rows:
        if row["image_id"]:
            path = Path(db_path).parent / "images" / row["image_id"] / "feed.webp"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"cached-webp")
    return rows


def test_atom_ar_valid_xml(settings, db_path, raw_articles):
    rows = _seed(db_path, raw_articles)
    root = ET.fromstring(build_feed(settings, rows, fmt="atom"))
    entries = root.findall(f"{ATOM_NS}entry")
    assert len(entries) == len(rows)
    for entry in entries:
        assert entry.find(f"{ATOM_NS}title") is not None
        assert entry.find(f"{ATOM_NS}id") is not None
        assert entry.find(f"{ATOM_NS}link") is not None


def test_rss_ar_valid_xml(settings, db_path, raw_articles):
    rows = _seed(db_path, raw_articles)
    root = ET.fromstring(build_feed(settings, rows, fmt="rss"))
    items = root.findall(".//item")
    assert len(items) == len(rows)


def test_feed_logga(settings, db_path, raw_articles):
    rows = _seed(db_path, raw_articles)
    atom = build_feed(settings, rows, fmt="atom").decode()
    assert "<logo>" in atom and "<icon>" in atom
    assert "/static/kt-rss-256.png" in atom
    rss = build_feed(settings, rows, fmt="rss").decode()
    assert "<image>" in rss and "/static/kt-rss-256.png" in rss


def test_feed_kategorier_per_tagg(settings, db_path, raw_articles):
    base = map_article(raw_articles[0], "https://www.kyrkanstidning.se")
    conn = connect(db_path)
    upsert_article(conn, replace(
        base, id="k", section="kultur", tags="bok, recension"))
    conn.commit()
    rows = get_articles(conn)
    conn.close()
    entry = ET.fromstring(build_feed(settings, rows, fmt="atom")).find(
        f"{ATOM_NS}entry"
    )
    terms = {c.get("term") for c in entry.findall(f"{ATOM_NS}category")}
    # Sektionen och varje tvättad tagg blir en egen kategori.
    assert terms == {"kultur", "bok", "recension"}


def test_feeden_innehaller_aldrig_ordet_bodytext(settings, db_path, raw_articles):
    rows = _seed(db_path, raw_articles)
    atom = build_feed(settings, rows, fmt="atom").decode("utf-8").lower()
    rss = build_feed(settings, rows, fmt="rss").decode("utf-8").lower()
    assert "bodytext" not in atom
    assert "bodytext" not in rss


def test_feeden_lacker_inte_faktisk_brodtext(settings, db_path, raw_articles, fixture_response):
    """Starkare än ordkollen: ingen verklig bodytext-text får nå feeden."""
    rows = _seed(db_path, raw_articles)
    atom = build_feed(settings, rows, fmt="atom").decode("utf-8")
    checked = 0
    for raw in fixture_response["result"]:
        body = (raw.get("bodytext") or "").strip()
        if len(body) > 120:
            assert body[60:110] not in atom
            checked += 1
    assert checked > 0  # fixturen ska innehålla artiklar med brödtext


def test_section_feed_filtrerar(settings, db_path, raw_articles):
    _seed(db_path, raw_articles)
    conn = connect(db_path)
    rows = get_articles(conn, section="debatt", limit=100)
    conn.close()
    assert rows and all(row["section"] == "debatt" for row in rows)
    ET.fromstring(build_feed(settings, rows, section="debatt", fmt="atom"))


def test_tag_feed_filtrerar_och_titel(settings, db_path, raw_articles):
    _seed(db_path, raw_articles)
    conn = connect(db_path)
    rows = get_articles(conn, tag="svenska kyrkan", limit=100)
    conn.close()
    assert rows  # fixturen har artiklar taggade 'svenska kyrkan'
    xml = build_feed(settings, rows, tag="svenska kyrkan", fmt="atom").decode("utf-8")
    ET.fromstring(xml)
    assert "tagg: svenska kyrkan" in xml


def test_atom_enclosure_pa_artikel_med_bild(settings, db_path, raw_articles):
    rows = _seed(db_path, raw_articles)
    root = ET.fromstring(build_feed(settings, rows, fmt="atom"))
    entries = root.findall(f"{ATOM_NS}entry")
    assert entries
    for entry in entries:
        # feedgen-buggen tappar attributen; _fix_atom_enclosure_links lagar dem.
        enc = entry.find(f"{ATOM_NS}link[@rel='enclosure']")
        assert enc is not None
        assert enc.get("type") == "image/webp"
        assert enc.get("href").startswith("http://localhost:8000/images/")
        assert enc.get("href").endswith("/feed.webp")


def test_rss_enclosure_pa_artikel_med_bild(settings, db_path, raw_articles):
    rows = _seed(db_path, raw_articles)
    root = ET.fromstring(build_feed(settings, rows, fmt="rss"))
    items = root.findall(".//item")
    assert items
    for item in items:
        enc = item.find("enclosure")
        assert enc is not None
        assert enc.get("type") == "image/webp"
        assert enc.get("url").startswith("http://localhost:8000/images/")


def test_enclosure_av_via_settings(settings, db_path, raw_articles):
    rows = _seed(db_path, raw_articles)
    off = settings.model_copy(update={"include_image_enclosure": False})
    root = ET.fromstring(build_feed(off, rows, fmt="atom"))
    for entry in root.findall(f"{ATOM_NS}entry"):
        assert entry.find(f"{ATOM_NS}link[@rel='enclosure']") is None


def test_ingen_enclosure_utan_bild(settings, db_path, raw_articles):
    conn = connect(db_path)
    a = map_article(raw_articles[0], "https://www.kyrkanstidning.se")
    upsert_article(conn, replace(a, id="nobild", image_id=""))
    conn.commit()
    rows = get_articles(conn, limit=10)
    conn.close()
    assert len(rows) == 1
    root = ET.fromstring(build_feed(settings, rows, fmt="atom"))
    entry = root.find(f"{ATOM_NS}entry")
    assert entry.find(f"{ATOM_NS}link[@rel='enclosure']") is None


def test_tom_feed_ar_valid(settings):
    ET.fromstring(build_feed(settings, [], fmt="atom"))
    ET.fromstring(build_feed(settings, [], fmt="rss"))


def test_build_opml(settings):
    from kt_rss.feed import build_opml
    root = ET.fromstring(build_opml(settings, ["nyhet", "debatt"]))
    outlines = root.findall(".//outline")
    # En outline för "alla" + en per sektion.
    assert len(outlines) == 3
    urls = [o.get("xmlUrl") for o in outlines]
    assert f"{settings.public_url}/feed.xml" in urls
    assert f"{settings.public_url}/feed/nyhet.xml" in urls
    # Absoluta URL:er - en OPML importeras i en extern läsare.
    assert all(u.startswith("http") for u in urls)
