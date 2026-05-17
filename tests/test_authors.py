"""Författarvyer: get_articles(author=), list_authors och /a-routerna."""

from dataclasses import replace

from fastapi.testclient import TestClient

from kt_rss.db import connect, get_articles, list_authors, map_article, upsert_article
from kt_rss.main import app, get_conn_settings

BASE = "https://www.kyrkanstidning.se"


def test_get_articles_och_list_authors(db_path, raw_articles):
    base = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    upsert_article(conn, replace(base, id="a1", author="Anna Andersson"))
    upsert_article(conn, replace(base, id="a2", author="Anna Andersson"))
    upsert_article(conn, replace(base, id="b1", author="Per Persson"))
    conn.commit()
    # Exakt författarmatchning.
    assert {r["id"] for r in get_articles(conn, author="Anna Andersson")} == {"a1", "a2"}
    assert {r["id"] for r in get_articles(conn, author="Per Persson")} == {"b1"}
    # list_authors med artikelantal.
    authors = {r["author"]: r["count"] for r in list_authors(conn)}
    assert authors == {"Anna Andersson": 2, "Per Persson": 1}
    conn.close()


def test_route_author(db_path, settings, raw_articles):
    base = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    upsert_article(conn, replace(base, id="a1", author="Anna Andersson"))
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
        html = client.get("/a/Anna%20Andersson")
        assert html.status_code == 200 and "<article" in html.text
        feed = client.get("/feed/a/Anna%20Andersson.xml")
        assert feed.status_code == 200
        assert "atom" in feed.headers["content-type"]
        # Skribentöversikten listar författaren.
        index = client.get("/a")
        assert index.status_code == 200 and "Anna Andersson" in index.text
        # Okänd skribent -> 404 i båda vyerna.
        assert client.get("/a/Ingen%20Alls").status_code == 404
        assert client.get("/feed/a/Ingen%20Alls.xml").status_code == 404
    finally:
        app.dependency_overrides.clear()
