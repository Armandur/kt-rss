"""Dedup-logik: first_seen rörs inte, last_seen + ändrade fält uppdateras (SS6, SS8:7)."""

from dataclasses import replace

import kt_rss.db as db
from kt_rss.db import connect, map_article, upsert_article

BASE = "https://www.kyrkanstidning.se"


def test_samma_id_ger_ett_item(db_path, raw_articles):
    a = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    assert upsert_article(conn, a) == "inserted"
    assert upsert_article(conn, a) == "unchanged"
    conn.commit()
    count = conn.execute("SELECT COUNT(*) AS c FROM articles").fetchone()["c"]
    conn.close()
    assert count == 1


def test_last_seen_uppdateras_first_seen_orors(db_path, raw_articles, monkeypatch):
    a = map_article(raw_articles[0], BASE)
    conn = connect(db_path)

    monkeypatch.setattr(db, "now_utc", lambda: "2026-01-01T00:00:00+00:00")
    upsert_article(conn, a)
    monkeypatch.setattr(db, "now_utc", lambda: "2026-01-02T12:00:00+00:00")
    upsert_article(conn, a)
    conn.commit()

    row = conn.execute(
        "SELECT first_seen, last_seen FROM articles WHERE id = ?", (a.id,)
    ).fetchone()
    conn.close()
    assert row["first_seen"] == "2026-01-01T00:00:00+00:00"
    assert row["last_seen"] == "2026-01-02T12:00:00+00:00"


def test_andrad_titel_subtitle_modified_uppdateras(db_path, raw_articles):
    a = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    upsert_article(conn, a)

    changed = replace(
        a, title="Ny rubrik", subtitle="Ny ingress", modified_at="2099-01-01T00:00:00+00:00"
    )
    assert upsert_article(conn, changed) == "updated"
    conn.commit()

    row = conn.execute(
        "SELECT title, subtitle, modified_at FROM articles WHERE id = ?", (a.id,)
    ).fetchone()
    conn.close()
    assert row["title"] == "Ny rubrik"
    assert row["subtitle"] == "Ny ingress"
    assert row["modified_at"] == "2099-01-01T00:00:00+00:00"


def test_andrade_tags_uppdateras(db_path, raw_articles):
    a = map_article(raw_articles[0], BASE)
    conn = connect(db_path)
    upsert_article(conn, a)

    changed = replace(a, tags="helt, nya, taggar")
    assert upsert_article(conn, changed) == "updated"
    conn.commit()

    row = conn.execute("SELECT tags FROM articles WHERE id = ?", (a.id,)).fetchone()
    conn.close()
    assert row["tags"] == "helt, nya, taggar"
