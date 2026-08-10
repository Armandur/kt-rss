"""Persistent bildcache, validering och lokal bildroute."""

import httpx
from fastapi.testclient import TestClient

from kt_rss.db import connect
from kt_rss.image_cache import (
    cached_image_url,
    fetch_image,
    image_cache_path,
)
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
