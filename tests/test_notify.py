"""Felnotisens tillståndsmaskin: övergång ok->fel och fel->ok."""

import pytest

from kt_rss import notify, poller
from kt_rss.api_client import FetchResult
from kt_rss.config import Settings
from kt_rss.db import STATUS_OK, STATUS_SANITY_FAILED, connect


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    """Fångar alla notiser i stället för att skicka dem."""
    calls: list[dict] = []

    def fake_send(settings, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(notify, "send", fake_send)
    return calls


@pytest.fixture
def notify_settings(db_path) -> Settings:
    return Settings(
        db_path=db_path,
        public_url="http://localhost:8000",
        ntfy_url="https://ntfy.example",
        ntfy_topic="svc_kt_rss",
        ntfy_token="tk_test",
        notify_after_failures=3,
    )


def _raw(article_id):
    return {
        "id": str(article_id),
        "published": "2026-05-16T09:00:00+02:00",
        "title": f"Rubrik {article_id}",
        "status": "P",
        "visibility_status": "P",
        "section_tag": "nyhet",
        "published_url": f"/nyhet/{article_id}",
    }


def _ok_result(*a, **k):
    return FetchResult(
        ok=True, status_code=200,
        data={"result": [_raw(i) for i in range(10)], "totalCount": 100},
    )


def _fail_result(*a, **k):
    return FetchResult(ok=False, status_code=503, error="tjänsten svarar inte")


def _alert_active(settings) -> bool:
    conn = connect(settings.db_path)
    try:
        row = conn.execute(
            "SELECT alert_active FROM fetch_state WHERE key = 'default'"
        ).fetchone()
        return bool(row["alert_active"])
    finally:
        conn.close()


def test_notis_forst_vid_troskeln_och_bara_en_gang(notify_settings, sent,
                                                   monkeypatch):
    monkeypatch.setattr(poller, "fetch_articles", _fail_result)

    poller.poll_once(notify_settings)
    poller.poll_once(notify_settings)
    assert sent == []          # två fel är under tröskeln - ingen notis

    poller.poll_once(notify_settings)
    assert len(sent) == 1
    assert sent[0]["priority"] == "3"
    assert "3 misslyckade" in sent[0]["message"]
    assert sent[0]["click"] == "http://localhost:8000/status"
    assert _alert_active(notify_settings)

    poller.poll_once(notify_settings)
    assert len(sent) == 1      # fortsatt fel spammar inte


def test_aterstallning_ger_en_notis_och_nollstaller(notify_settings, sent,
                                                   monkeypatch):
    monkeypatch.setattr(poller, "fetch_articles", _fail_result)
    for _ in range(3):
        poller.poll_once(notify_settings)
    assert len(sent) == 1

    monkeypatch.setattr(poller, "fetch_articles", _ok_result)
    assert poller.poll_once(notify_settings)["status"] == STATUS_OK
    assert len(sent) == 2
    assert sent[1]["priority"] == "2"
    assert not _alert_active(notify_settings)

    poller.poll_once(notify_settings)
    assert len(sent) == 2      # fortsatt frisk notifierar inte


def test_sanity_failed_raknas_som_fel(notify_settings, sent, monkeypatch):
    monkeypatch.setattr(poller, "fetch_articles", _ok_result)
    poller.poll_once(notify_settings)

    def tomt(*a, **k):
        return FetchResult(ok=True, status_code=200,
                           data={"result": [], "totalCount": 100})

    monkeypatch.setattr(poller, "fetch_articles", tomt)
    for _ in range(3):
        assert poller.poll_once(notify_settings)["status"] == STATUS_SANITY_FAILED

    assert len(sent) == 1
    assert "API-svaret ser trasigt ut" in sent[0]["message"]


def test_misslyckad_notis_lamnar_flaggan_orord(notify_settings, monkeypatch):
    calls: list[dict] = []

    def failing_send(settings, **kwargs):
        calls.append(kwargs)
        return False

    monkeypatch.setattr(notify, "send", failing_send)
    monkeypatch.setattr(poller, "fetch_articles", _fail_result)
    for _ in range(3):
        poller.poll_once(notify_settings)

    assert len(calls) == 1
    assert not _alert_active(notify_settings)

    # Nästa runda försöker igen i stället för att tro att notisen gått ut.
    poller.poll_once(notify_settings)
    assert len(calls) == 2


def test_avstangd_utan_topic_och_token(settings, sent, monkeypatch):
    # Grundfixturen saknar ntfy-konfiguration - inget får skickas.
    monkeypatch.setattr(poller, "fetch_articles", _fail_result)
    for _ in range(5):
        poller.poll_once(settings)
    assert sent == []
