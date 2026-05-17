"""Artikelsök: FTS5-fulltextsök, query-sanering och /search-routen."""

from dataclasses import replace

from fastapi.testclient import TestClient

from kt_rss.db import (
    _fts_query,
    connect,
    map_article,
    search_articles,
    upsert_article,
)
from kt_rss.main import app, get_conn_settings

BASE = "https://www.kyrkanstidning.se"


def test_fts_query_sanering():
    # Varje ord citeras - FTS5-operatorer blir literala termer.
    assert _fts_query("kyrka kris") == '"kyrka" "kris"'
    assert _fts_query("OR") == '"OR"'
    # Tokens med citattecken släpps; tomt ger None.
    assert _fts_query('a"b') is None
    assert _fts_query("   ") is None


def test_search_articles_fulltext(db_path, raw_articles):
    base = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    upsert_article(conn, replace(
        base, id="t", title="Biskopen besöker Lund", subtitle="kort"))
    upsert_article(conn, replace(
        base, id="s", title="Annan rubrik", subtitle="Om klimat i stiftet"))
    upsert_article(conn, replace(
        base, id="g", title="Recension", subtitle="x", tags="kultur, bok"))
    conn.commit()
    # Ordmatchning, versal-okänsligt.
    assert {r["id"] for r in search_articles(conn, "BISKOPEN")} == {"t"}
    # Träff i ingress.
    assert {r["id"] for r in search_articles(conn, "klimat")} == {"s"}
    # Träff i taggar (FTS indexerar även tags och author).
    assert {r["id"] for r in search_articles(conn, "bok")} == {"g"}
    # Flera ord bildar en AND-sökning.
    assert {r["id"] for r in search_articles(conn, "biskopen lund")} == {"t"}
    assert search_articles(conn, "biskopen klimat") == []
    # Tom sökterm ger inget.
    assert search_articles(conn, "   ") == []
    conn.close()


def test_search_articles_svenska_tecken(db_path, raw_articles):
    base = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    upsert_article(conn, replace(base, id="a", title="Ärkebiskopen i Växjö"))
    conn.commit()
    # FTS5:s unicode61-tokenizer versal-okänslig även för å/ä/ö.
    assert {r["id"] for r in search_articles(conn, "ÄRKEBISKOPEN")} == {"a"}
    assert {r["id"] for r in search_articles(conn, "växjö")} == {"a"}
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
