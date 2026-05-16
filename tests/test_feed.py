"""Feed-serialisering: valid Atom/RSS, ALDRIG bodytext (spec SS7, SS9)."""

import xml.etree.ElementTree as ET

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


def test_tom_feed_ar_valid(settings):
    ET.fromstring(build_feed(settings, [], fmt="atom"))
    ET.fromstring(build_feed(settings, [], fmt="rss"))
