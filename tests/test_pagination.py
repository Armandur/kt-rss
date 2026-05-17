"""Paginering: _paginate-aritmetik, get_articles offset, ?partial-fragment."""

from dataclasses import replace

from fastapi.testclient import TestClient

from kt_rss.db import connect, get_articles, map_article, upsert_article
from kt_rss.main import _paginate, app, get_conn_settings

BASE = "https://www.kyrkanstidning.se"


def test_paginate_aritmetik():
    # 50 artiklar, 20 per sida -> 3 sidor.
    pg = _paginate("/articles", 1, 50, 20)
    assert pg["total_pages"] == 3
    assert pg["offset"] == 0
    assert pg["next_page"] == 2

    pg = _paginate("/articles", 2, 50, 20)
    assert pg["offset"] == 20 and pg["next_page"] == 3

    # Sista sidan har inget next_page.
    assert _paginate("/articles", 3, 50, 20)["next_page"] is None

    # Out-of-bounds klampas till sista sidan.
    pg = _paginate("/articles", 999, 50, 20)
    assert pg["page"] == 3 and pg["next_page"] is None

    # Tomt urval ger alltid minst en sida.
    pg = _paginate("/articles", 1, 0, 20)
    assert pg["total_pages"] == 1 and pg["next_page"] is None


def test_get_articles_offset(db_path, raw_articles):
    base = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    for i in range(5):
        upsert_article(conn, replace(
            base, id=f"a{i}", published_at=f"2026-05-1{9 - i}T00:00:00"))
    conn.commit()
    first2 = [r["id"] for r in get_articles(conn, limit=2, offset=0)]
    next2 = [r["id"] for r in get_articles(conn, limit=2, offset=2)]
    conn.close()
    assert first2 == ["a0", "a1"]      # published_at DESC
    assert next2 == ["a2", "a3"]
    assert not set(first2) & set(next2)


def test_route_partial_ger_fragment(db_path, settings, raw_articles):
    base = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    upsert_article(conn, replace(base, id="x1"))
    conn.commit()
    conn.close()

    def _override():
        c = connect(db_path)
        try:
            yield c, settings
        finally:
            c.close()

    app.dependency_overrides[get_conn_settings] = _override
    try:
        client = TestClient(app)
        full = client.get("/articles")
        assert full.status_code == 200 and "<html" in full.text
        # Feed-autodiscovery: RSS-läsare hittar feeden via <link rel=alternate>.
        assert 'rel="alternate"' in full.text and "/feed.xml" in full.text
        frag = client.get("/articles?page=1&partial=1")
        assert frag.status_code == 200
        # Fragmentet är bara artikelrader - ingen sidram.
        assert "<html" not in frag.text
        assert "<article" in frag.text
    finally:
        app.dependency_overrides.clear()


def test_index_visar_senaste_artiklar(db_path, settings, raw_articles):
    base = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    upsert_article(conn, replace(base, id="x1"))
    conn.commit()
    conn.close()

    def _override():
        c = connect(db_path)
        try:
            yield c, settings
        finally:
            c.close()

    app.dependency_overrides[get_conn_settings] = _override
    try:
        client = TestClient(app)
        r = client.get("/")
        assert r.status_code == 200
        assert "Senaste artiklarna" in r.text
        assert "<article" in r.text
    finally:
        app.dependency_overrides.clear()
