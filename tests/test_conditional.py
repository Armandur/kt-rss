"""Conditional GET på egna feeds: ETag och 304 Not Modified."""

from fastapi.testclient import TestClient

from kt_rss.db import connect, map_article, upsert_article
from kt_rss.main import app, get_conn_settings

BASE = "https://www.kyrkanstidning.se"


def test_feed_etag_och_304(db_path, settings, raw_articles):
    conn = connect(db_path)
    for raw in raw_articles[:5]:
        upsert_article(conn, map_article(raw, BASE))
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
        first = client.get("/feed.xml")
        assert first.status_code == 200
        etag = first.headers.get("etag")
        assert etag
        # Deterministisk ETag - ett andra anrop ger samma (skyddar mot att
        # build_feed börjar baka in en renderingstidsstämpel).
        assert client.get("/feed.xml").headers.get("etag") == etag
        # Samma ETag tillbaka i If-None-Match -> 304 utan kropp.
        cached = client.get("/feed.xml", headers={"If-None-Match": etag})
        assert cached.status_code == 304
        assert cached.content == b""
        assert cached.headers.get("etag") == etag
        # Fel ETag -> full 200.
        stale = client.get("/feed.xml", headers={"If-None-Match": '"fel"'})
        assert stale.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_etag_overlever_poll(db_path, settings, raw_articles):
    """En poll som bara bumpar last_seen får inte ändra feedens ETag."""
    conn = connect(db_path)
    for raw in raw_articles[:5]:
        upsert_article(conn, map_article(raw, BASE))
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
        before = client.get("/feed.xml").headers["etag"]
        # Simulera en poll: samma artiklar upsertas igen -> last_seen bumpas.
        conn = connect(db_path)
        for raw in raw_articles[:5]:
            upsert_article(conn, map_article(raw, BASE))
        conn.commit()
        conn.close()
        after = client.get("/feed.xml").headers["etag"]
        assert after == before
    finally:
        app.dependency_overrides.clear()
