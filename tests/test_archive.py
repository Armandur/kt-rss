"""Datumarkiv: get_articles(period=), list_archive_months och /archive."""

from dataclasses import replace

from fastapi.testclient import TestClient

from kt_rss.db import (
    connect,
    get_articles,
    list_archive_months,
    map_article,
    upsert_article,
)
from kt_rss.main import app, get_conn_settings

BASE = "https://www.kyrkanstidning.se"


def test_period_filter_och_list_archive_months(db_path, raw_articles):
    base = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    upsert_article(conn, replace(base, id="m1", published_at="2026-05-10T08:00:00+02:00"))
    upsert_article(conn, replace(base, id="m2", published_at="2026-05-28T08:00:00+02:00"))
    upsert_article(conn, replace(base, id="a1", published_at="2026-04-02T08:00:00+02:00"))
    conn.commit()
    # period filtrerar på publiceringsmånad via published_at-prefix.
    assert {r["id"] for r in get_articles(conn, period="2026-05")} == {"m1", "m2"}
    assert {r["id"] for r in get_articles(conn, period="2026-04")} == {"a1"}
    # list_archive_months grupperar per månad, nyaste först.
    months = [(r["year"], r["month"], r["count"]) for r in list_archive_months(conn)]
    assert months == [("2026", "05", 2), ("2026", "04", 1)]
    conn.close()


def test_route_archive(db_path, settings, raw_articles):
    base = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    upsert_article(conn, replace(base, id="m1", published_at="2026-05-10T08:00:00+02:00"))
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
        index = client.get("/archive")
        assert index.status_code == 200 and "2026" in index.text
        month = client.get("/archive/2026/5")
        assert month.status_code == 200 and "<article" in month.text
        assert "Maj 2026" in month.text
        # Period utan artiklar -> 404.
        assert client.get("/archive/2020/1").status_code == 404
    finally:
        app.dependency_overrides.clear()
