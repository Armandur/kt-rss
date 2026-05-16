"""Artikelsök: search_articles och /search-routen."""

from dataclasses import replace

from fastapi.testclient import TestClient

from kt_rss.db import connect, map_article, search_articles, upsert_article
from kt_rss.main import app, get_conn_settings

BASE = "https://www.kyrkanstidning.se"


def test_search_articles_titel_och_ingress(db_path, raw_articles):
    base = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    upsert_article(conn, replace(
        base, id="t", title="Biskopen besöker Lund", subtitle="kort"))
    upsert_article(conn, replace(
        base, id="s", title="Annan rubrik", subtitle="Om klimatet i stiftet"))
    upsert_article(conn, replace(base, id="n", title="Inget", subtitle="här"))
    conn.commit()
    # Träff i titel, versal-okänsligt.
    assert {r["id"] for r in search_articles(conn, "BISKOPEN")} == {"t"}
    # Träff i ingress.
    assert {r["id"] for r in search_articles(conn, "klimat")} == {"s"}
    # Tom sökterm ger inget.
    assert search_articles(conn, "   ") == []
    # %/_ behandlas som tecken, inte wildcards.
    assert search_articles(conn, "%") == []
    conn.close()


def test_route_search(db_path, settings, raw_articles):
    base = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    upsert_article(conn, replace(base, id="t", title="Unik rubriktext"))
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
        hit = client.get("/search?q=unik")
        assert hit.status_code == 200 and "<article" in hit.text
        miss = client.get("/search?q=finnsinte")
        assert miss.status_code == 200
        assert "Inga artiklar matchade" in miss.text
        # Utan q renderas sökformuläret ändå.
        assert client.get("/search").status_code == 200
    finally:
        app.dependency_overrides.clear()
