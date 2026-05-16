"""Tagg-filtrering: get_articles(tag=...), list_tags och /t/{tag}-routen."""

from dataclasses import replace

from fastapi.testclient import TestClient

from kt_rss.db import connect, get_articles, list_tags, map_article, upsert_article
from kt_rss.main import app, get_conn_settings

BASE = "https://www.kyrkanstidning.se"


def test_get_articles_tag_matchar_hel_token(db_path, raw_articles):
    base = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    # En artikel taggad 'kyrka', en taggad 'svenska kyrkan'.
    upsert_article(conn, replace(base, id="a1", tags="kyrka, nyhet"))
    upsert_article(conn, replace(base, id="a2", tags="svenska kyrkan"))
    conn.commit()

    # 'kyrka' får inte träffa 'svenska kyrkan' som delsträng.
    assert {r["id"] for r in get_articles(conn, tag="kyrka")} == {"a1"}
    assert {r["id"] for r in get_articles(conn, tag="svenska kyrkan")} == {"a2"}
    assert get_articles(conn, tag="finns-inte") == []
    conn.close()


def test_list_tags_aggregerar(db_path, raw_articles):
    base = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    upsert_article(conn, replace(base, id="a1", tags="nyhet, kyrka"))
    upsert_article(conn, replace(base, id="a2", tags="nyhet"))
    conn.commit()

    tags = dict(list_tags(conn))
    conn.close()
    assert tags["nyhet"] == 2
    assert tags["kyrka"] == 1


def test_route_tag_renderar_och_404(db_path, settings, raw_articles):
    base = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    upsert_article(conn, replace(base, id="x1", tags="nyhet, kyrka"))
    conn.commit()
    conn.close()

    # Kör route-handlern utan lifespan (ingen scheduler, inget nät) genom
    # att override:a DB/settings-dependencyn.
    def _override():
        c = connect(db_path)
        try:
            yield c, settings
        finally:
            c.close()

    app.dependency_overrides[get_conn_settings] = _override
    try:
        client = TestClient(app)
        ok = client.get("/t/nyhet")
        assert ok.status_code == 200
        assert "tag-pill" in ok.text
        assert client.get("/t/finns-inte").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_route_feed_tag(db_path, settings, raw_articles):
    base = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    upsert_article(conn, replace(base, id="x1", tags="nyhet, kyrka"))
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
        atom = client.get("/feed/t/nyhet.xml")
        assert atom.status_code == 200
        assert "atom" in atom.headers["content-type"]
        rss = client.get("/feed/t/nyhet.xml?fmt=rss")
        assert rss.status_code == 200
        assert "rss" in rss.headers["content-type"]
        assert client.get("/feed/t/finns-inte.xml").status_code == 404
    finally:
        app.dependency_overrides.clear()
