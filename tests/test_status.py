"""Status- och statistiksida (/status)."""

from fastapi.testclient import TestClient

from kt_rss.db import connect, log_poll, map_article, recent_polls, upsert_article
from kt_rss.main import _human_size, app, get_conn_settings

BASE = "https://www.kyrkanstidning.se"


def test_human_size():
    assert _human_size(512) == "512 B"
    assert _human_size(2048) == "2 KB"
    assert _human_size(5 * 1024 * 1024) == "5.0 MB"


def test_poll_log(db_path):
    conn = connect(db_path)
    log_poll(conn, status="ok", fetched=50, inserted=3, updated=1)
    log_poll(conn, status="skipped_304")
    rows = recent_polls(conn)
    # Nyaste rundan först.
    assert rows[0]["status"] == "skipped_304"
    assert rows[1]["status"] == "ok"
    assert rows[1]["fetched"] == 50 and rows[1]["inserted"] == 3
    conn.close()


def test_status_page(db_path, settings, raw_articles):
    conn = connect(db_path)
    for raw in raw_articles[:5]:
        upsert_article(conn, map_article(raw, BASE))
    conn.execute(
        """
        INSERT INTO image_cache (
            image_id, variant, status, source_url, size_bytes,
            fetched_at, last_attempt_at, next_attempt_at, last_error
        ) VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?),
            (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "100", "thumb", "cached", "", 2048,
            "2026-08-10T10:00:00+00:00",
            "2026-08-10T10:00:00+00:00", None, "",
            "101", "thumb", "error", "", 0, None,
            "2026-08-10T10:00:00+00:00",
            "2999-01-01T00:00:00+00:00", "challenge",
        ),
    )
    conn.execute(
        "UPDATE image_cache_state SET circuit_open_until = ?, "
        "last_error_at = ?, last_error = ? WHERE key = ?",
        (
            "2999-01-01T00:00:00+00:00",
            "2026-08-10T10:00:00+00:00",
            "challenge",
            "default",
        ),
    )
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
        assert "Pollhistorik" in r.text
        assert "Artiklar per månad" in r.text
        assert "Bildcache" in r.text
        assert "Cachade bild-id" in r.text
        assert "Cachade varianter" in r.text
        assert "2 KB" in r.text
        assert "Circuit breaker" in r.text
        assert "challenge" in r.text
    finally:
        app.dependency_overrides.clear()
