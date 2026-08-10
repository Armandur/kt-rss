"""Wicketkeeper-flödet körs offline mot httpx MockTransport."""

import json

import httpx

from kt_rss.api_client import fetch_articles
from kt_rss.kt_client import (
    KTClient,
    WicketkeeperError,
    is_wicketkeeper_challenge,
    solve_pow,
)


CHALLENGE_HTML = b"<title>Are we human?</title><script src='/_labrador/pow/slow.js'></script>"


def test_solve_pow_uppfyller_svarighetsgrad():
    nonce, result = solve_pow("a" * 32, 2)
    assert result.startswith("00")
    assert nonce.isdecimal()


def test_challenge_identifieras_strikt():
    request = httpx.Request("GET", "https://api.kyrkanstidning.se/article")
    challenge = httpx.Response(
        200,
        headers={"content-type": "text/html"},
        content=CHALLENGE_HTML,
        request=request,
    )
    ordinary_html = httpx.Response(
        200,
        headers={"content-type": "text/html"},
        content=b"<title>Vanlig sida</title>",
        request=request,
    )
    assert is_wicketkeeper_challenge(challenge)
    assert not is_wicketkeeper_challenge(ordinary_html)


def test_cookie_fran_bildserver_oppnar_artikel_api(settings):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/v0/challenge"):
            return httpx.Response(
                200,
                json={"challenge": "b" * 32, "difficulty": 1, "token": "jwt"},
            )
        if request.url.path.endswith("/v0/verify"):
            assert request.headers["pow-result"].startswith("0")
            return httpx.Response(
                200,
                json={"success": True},
                headers={
                    "set-cookie": (
                        "LabPowToken=verified; Domain=.kyrkanstidning.se; "
                        "Path=/; Secure; HttpOnly"
                    )
                },
            )
        if "LabPowToken=verified" in request.headers.get("cookie", ""):
            return httpx.Response(200, json={"result": [], "totalCount": 0})
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=CHALLENGE_HTML,
        )

    client = KTClient(settings, transport=httpx.MockTransport(handler))
    try:
        response = client.get(settings.api_url)
        assert response.json() == {"result": [], "totalCount": 0}
        assert "LabPowToken" in client.cookies
        assert calls.count("/_labrador/pow/v0/challenge") == 1
        assert calls.count("/_labrador/pow/v0/verify") == 1

        # Cookien återanvänds, ingen ny challenge ska lösas.
        assert client.get(settings.api_url).status_code == 200
        assert calls.count("/_labrador/pow/v0/challenge") == 1
    finally:
        client.close()


def test_ogiltigt_challenge_svar_avbryts(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v0/challenge"):
            return httpx.Response(200, content=json.dumps({"difficulty": 4}))
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=CHALLENGE_HTML,
        )

    client = KTClient(settings, transport=httpx.MockTransport(handler))
    try:
        try:
            client.get(settings.api_url)
        except httpx.HTTPError as exc:
            assert "ogiltigt Wicketkeeper-svar" in str(exc)
        else:
            raise AssertionError("ogiltigt challenge-svar accepterades")
    finally:
        client.close()


def test_artikelklienten_retryar_inte_challengefel(settings):
    class FailingClient:
        calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            raise WicketkeeperError("verifieringen misslyckades")

    client = FailingClient()
    result = fetch_articles(settings, limit=1, start=0, client=client)
    assert not result.ok
    assert result.status_code == 200
    assert client.calls == 1
