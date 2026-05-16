"""Pollerns rund-logik: sanity, filtrering, schemaläggar-skydd (spec SS8)."""

from kt_rss import poller
from kt_rss.api_client import FetchResult
from kt_rss.db import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_SANITY_FAILED,
    STATUS_SKIPPED_304,
    connect,
    count_articles,
)


def _result(articles, total=32735):
    return FetchResult(
        ok=True, status_code=200,
        data={"result": articles, "totalCount": total},
    )


def _raw(article_id, status="P", visibility="P", section="nyhet"):
    # API:et levererar id som sträng ("433146"); spegla det i testdatan.
    return {
        "id": str(article_id),
        "published": "2026-05-16T09:00:00+02:00",
        "title": f"Rubrik {article_id}",
        "subtitle": "",
        "status": status,
        "visibility_status": visibility,
        "section_tag": section,
        "published_url": f"/{section}/{article_id}",
    }


def _count(settings):
    conn = connect(settings.db_path)
    try:
        return count_articles(conn)
    finally:
        conn.close()


def test_tomt_resultat_ger_sanity_failed_utan_radering(settings, monkeypatch):
    good = [_raw(i) for i in range(10)]
    monkeypatch.setattr(poller, "fetch_articles", lambda *a, **k: _result(good))
    assert poller.poll_once(settings)["status"] == STATUS_OK
    assert _count(settings) == 10

    # Nästa runda: tomt result - inget får raderas eller blankas.
    monkeypatch.setattr(poller, "fetch_articles", lambda *a, **k: _result([]))
    assert poller.poll_once(settings)["status"] == STATUS_SANITY_FAILED
    assert _count(settings) == 10


def test_for_litet_resultat_ger_sanity_failed(settings, monkeypatch):
    monkeypatch.setattr(poller, "fetch_articles",
                        lambda *a, **k: _result([_raw(i) for i in range(20)]))
    poller.poll_once(settings)  # last_count = 20

    # 5 < 20 * 0.4 -> sanity_failed
    monkeypatch.setattr(poller, "fetch_articles",
                        lambda *a, **k: _result([_raw(i) for i in range(5)]))
    assert poller.poll_once(settings)["status"] == STATUS_SANITY_FAILED


def test_endast_publicerade_synliga_lagras(settings, monkeypatch):
    arts = [
        _raw(1, "P", "P"),
        _raw(2, "X", "P"),   # ej publicerad
        _raw(3, "P", "X"),   # ej synlig
        _raw(4, "P", "P"),
    ]
    monkeypatch.setattr(poller, "fetch_articles", lambda *a, **k: _result(arts))
    assert poller.poll_once(settings)["status"] == STATUS_OK

    conn = connect(settings.db_path)
    ids = {row["id"] for row in conn.execute("SELECT id FROM articles")}
    conn.close()
    assert ids == {"1", "4"}


def test_pollfel_dodar_inte_schemalaggaren(settings, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("injicerat fel mitt i rundan")

    monkeypatch.setattr(poller, "fetch_articles", boom)
    # poll_once får INTE kasta vidare - då skulle APScheduler-jobbet dö.
    result = poller.poll_once(settings)
    assert result["status"] == STATUS_ERROR


def test_304_hoppar_over(settings, monkeypatch):
    monkeypatch.setattr(
        poller, "fetch_articles",
        lambda *a, **k: FetchResult(ok=True, not_modified=True, status_code=304),
    )
    assert poller.poll_once(settings)["status"] == STATUS_SKIPPED_304
