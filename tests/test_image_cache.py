"""Persistent bildcache, validering och lokal bildroute."""

import httpx
import pytest
from fastapi.testclient import TestClient

from kt_rss import image_cache
from kt_rss.db import connect, map_article, upsert_article
from kt_rss.image_cache import (
    cached_image_url,
    fetch_image,
    image_cache_path,
)
from kt_rss.kt_client import WicketkeeperError
from kt_rss.main import app


def _webp(width: int, height: int) -> bytes:
    data = bytearray(100)
    data[:4] = b"RIFF"
    data[4:8] = (92).to_bytes(4, "little")
    data[8:12] = b"WEBP"
    data[12:16] = b"VP8X"
    data[16:20] = (10).to_bytes(4, "little")
    data[24:27] = (width - 1).to_bytes(3, "little")
    data[27:30] = (height - 1).to_bytes(3, "little")
    return bytes(data)


class _FakeClient:
    def __init__(self, response: httpx.Response):
        self.response = response
        self.calls = 0

    def get(self, url: str):
        self.calls += 1
        return self.response


def _response(content: bytes, content_type: str = "image/webp") -> httpx.Response:
    request = httpx.Request("GET", "https://image.kyrkanstidning.se/1.webp")
    return httpx.Response(
        200,
        headers={"content-type": content_type},
        content=content,
        request=request,
    )


def test_fetch_image_skriver_atomiskt_och_ateranvander_cache(settings):
    fake = _FakeClient(_response(_webp(480, 312)))
    assert fetch_image(settings, "123", "thumb", client=fake)

    path = image_cache_path(settings, "123", "thumb")
    assert path.read_bytes() == _webp(480, 312)
    assert cached_image_url(settings, "123", "thumb") == (
        "http://localhost:8000/images/123/thumb.webp"
    )
    assert fetch_image(settings, "123", "thumb", client=fake)
    assert fake.calls == 1

    conn = connect(settings.db_path)
    row = conn.execute(
        "SELECT status, size_bytes FROM image_cache "
        "WHERE image_id = '123' AND variant = 'thumb'"
    ).fetchone()
    conn.close()
    assert dict(row) == {"status": "cached", "size_bytes": 100}


def test_html_sparas_aldrig_som_bild(settings):
    fake = _FakeClient(_response(b"<title>Are we human?</title>", "text/html"))
    assert not fetch_image(settings, "456", "thumb", client=fake)
    assert not image_cache_path(settings, "456", "thumb").exists()

    conn = connect(settings.db_path)
    row = conn.execute(
        "SELECT status, next_attempt_at, last_error FROM image_cache "
        "WHERE image_id = '456' AND variant = 'thumb'"
    ).fetchone()
    conn.close()
    assert row["status"] == "error"
    assert row["next_attempt_at"]
    assert "svarstyp" in row["last_error"]


def test_lokal_bildroute_har_etag_och_strikt_path(settings):
    path = image_cache_path(settings, "789", "thumb")
    path.parent.mkdir(parents=True)
    path.write_bytes(_webp(480, 312))
    app.state.settings = settings
    client = TestClient(app)

    assert client.head("/images/789/thumb.webp").status_code == 200
    response = client.get("/images/789/thumb.webp")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert "immutable" in response.headers["cache-control"]
    etag = response.headers["etag"]
    assert client.get(
        "/images/789/thumb.webp", headers={"If-None-Match": etag}
    ).status_code == 304
    assert client.get("/images/789/unknown.webp").status_code == 404
    assert client.get("/images/not-a-number/thumb.webp").status_code == 404
    assert client.get("/images/999/thumb.webp").status_code == 404


def _seed_images(settings, raw_articles, count: int = 4) -> list[str]:
    conn = connect(settings.db_path)
    image_ids = []
    for raw in raw_articles[:count]:
        article = map_article(raw, "https://www.kyrkanstidning.se")
        upsert_article(conn, article)
        image_ids.append(article.image_id)
    conn.commit()
    conn.close()
    return image_ids


def test_backfillurval_hoppar_over_cache_och_cooldown(
    settings, raw_articles
):
    image_ids = _seed_images(settings, raw_articles)
    cached = image_cache_path(settings, image_ids[0], "thumb")
    cached.parent.mkdir(parents=True)
    cached.write_bytes(_webp(480, 312))

    conn = connect(settings.db_path)
    conn.execute(
        """
        INSERT INTO image_cache (
            image_id, variant, status, source_url, size_bytes,
            fetched_at, last_attempt_at, next_attempt_at, last_error
        ) VALUES (?, 'thumb', 'error', '', 0, NULL, ?, ?, 'cooldown')
        """,
        (image_ids[1], "2026-08-10T00:00:00+00:00", "2999-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    assert image_cache.select_backfill_items(
        settings, ["thumb"], limit=2
    ) == [(image_ids[2], "thumb"), (image_ids[3], "thumb")]


def test_backfill_ar_aterupptagbar_och_anvander_worker(
    settings, raw_articles, monkeypatch
):
    image_ids = _seed_images(settings, raw_articles)
    monkeypatch.setattr(image_cache, "MIN_REQUEST_INTERVAL", 0)

    def fake_fetch(settings, image_id, variant, **kwargs):
        path = image_cache_path(settings, image_id, variant)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_webp(480, 312))
        return True

    monkeypatch.setattr(image_cache, "fetch_image", fake_fetch)
    first = image_cache.run_image_backfill(
        settings, variants=["thumb"], limit=2
    )
    assert first.cached == 2
    assert not first.aborted

    remaining = image_cache.select_backfill_items(
        settings, ["thumb"], limit=2
    )
    assert remaining[0] == (image_ids[2], "thumb")


def test_backfill_avbryter_hela_kon_vid_challenge(
    settings, raw_articles, monkeypatch
):
    _seed_images(settings, raw_articles)
    monkeypatch.setattr(image_cache, "MIN_REQUEST_INTERVAL", 0)

    def challenge(*args, **kwargs):
        raise WicketkeeperError("challenge kvar")

    monkeypatch.setattr(image_cache, "fetch_image", challenge)
    result = image_cache.run_image_backfill(
        settings, variants=["thumb"], limit=3
    )
    assert result.aborted
    assert result.failed == 1
    assert result.skipped == 2
    assert "challenge kvar" in result.error


def test_challenge_oppnar_persistent_circuit_breaker(settings):
    class ChallengeClient:
        def get(self, url):
            raise WicketkeeperError("verifieringen misslyckades")

    with pytest.raises(WicketkeeperError):
        fetch_image(
            settings,
            "999",
            "thumb",
            client=ChallengeClient(),
            raise_on_challenge=True,
        )

    status = image_cache.image_cache_status(settings)
    assert status["circuit"]["open"]
    assert status["circuit"]["open_until"]
    assert "verifieringen misslyckades" in status["circuit"]["last_error"]
