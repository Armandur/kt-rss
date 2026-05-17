"""Liveuppdatering: count_articles_after och /latest-endpointen."""

from dataclasses import replace

from fastapi.testclient import TestClient

from kt_rss.db import connect, count_articles_after, map_article, upsert_article
from kt_rss.main import app, get_conn_settings

BASE = "https://www.kyrkanstidning.se"


def test_count_articles_after(db_path, raw_articles):
    base = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    upsert_article(conn, replace(base, id="o", published_at="2026-05-01T08:00:00+02:00"))
    upsert_article(conn, replace(base, id="n1", published_at="2026-05-10T08:00:00+02:00"))
    upsert_article(conn, replace(base, id="n2", published_at="2026-05-12T08:00:00+02:00"))
    conn.commit()
    assert count_articles_after(conn, "2026-05-05T00:00:00+02:00") == 2
    # Strikt efter - den nyaste artikelns egen tidsstämpel ger noll.
    assert count_articles_after(conn, "2026-05-12T08:00:00+02:00") == 0
    conn.close()


def test_route_latest(db_path, settings, raw_articles):
    base = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    upsert_article(conn, replace(base, id="a", published_at="2026-05-10T08:00:00+02:00"))
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
        # params= ser till att + i tidsstämpeln URL-enkodas korrekt.
        hit = client.get("/latest", params={"after": "2026-05-01T00:00:00+02:00"})
        assert hit.json() == {"count": 1}
        none = client.get("/latest", params={"after": "2026-12-01T00:00:00+02:00"})
        assert none.json() == {"count": 0}
        # Utan after -> 0.
        assert client.get("/latest").json() == {"count": 0}
    finally:
        app.dependency_overrides.clear()
