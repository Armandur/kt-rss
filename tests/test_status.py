"""Status- och statistiksida (/status)."""

from fastapi.testclient import TestClient

from kt_rss.db import connect, map_article, upsert_article
from kt_rss.main import _human_size, app, get_conn_settings

BASE = "https://www.kyrkanstidning.se"


def test_human_size():
    assert _human_size(512) == "512 B"
    assert _human_size(2048) == "2 KB"
    assert _human_size(5 * 1024 * 1024) == "5.0 MB"


def test_status_page(db_path, settings, raw_articles):
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
        r = client.get("/status")
        assert r.status_code == 200
        assert "Artiklar i arkivet" in r.text
        assert "Databasstorlek" in r.text
        assert "Vanligaste taggar" in r.text
    finally:
        app.dependency_overrides.clear()
