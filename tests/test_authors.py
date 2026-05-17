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
    # Flerskribent-artikel: byline med två namn ska räknas som båda.
    upsert_article(conn, replace(base, id="c1", author="Anna Andersson, Per Persson"))
    conn.commit()
    # Tokenmatchning - en flerskribent-artikel träffas av var och en.
    assert {r["id"] for r in get_articles(conn, author="Anna Andersson")} == {"a1", "a2", "c1"}
    assert {r["id"] for r in get_articles(conn, author="Per Persson")} == {"b1", "c1"}
    # list_authors räknar varje enskilt namn för sig.
    assert dict(list_authors(conn)) == {"Anna Andersson": 3, "Per Persson": 2}
    conn.close()


def test_clean_authors_tvattar_byline(db_path, raw_articles):
    from kt_rss.db import _clean_authors

    # Inledande komma (tomt namn), inkonsekvent whitespace, dubbletter.
    assert _clean_authors(", Patrik Hagman") == "Patrik Hagman"
    assert _clean_authors("Anna  Andersson ,  Per Persson") == "Anna Andersson, Per Persson"
    assert _clean_authors("Anna, Anna") == "Anna"
    assert _clean_authors(None) == ""
    # ' och ' räknas som avgränsare (svensk byline-konvention).
    assert _clean_authors("Anna Andersson och Per Persson") == "Anna Andersson, Per Persson"
    assert _clean_authors("Anna A, Per P och Karl K") == "Anna A, Per P, Karl K"


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
