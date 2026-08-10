"""Gemensam HTTP-session mot KT med stöd för Labradors Wicketkeeper-PoW."""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Mapping
from typing import Any

import httpx

from kt_rss.config import IMAGE_API_BASE, Settings

logger = logging.getLogger("kt_rss.http")

CHALLENGE_URL = f"{IMAGE_API_BASE}/_labrador/pow/v0/challenge"
VERIFY_URL = f"{IMAGE_API_BASE}/_labrador/pow/v0/verify"
COOKIE_NAME = "LabPowToken"
MAX_DIFFICULTY = 6


class WicketkeeperError(httpx.HTTPError):
    """Challenge-protokollet misslyckades och ska inte transport-retryas."""


def is_wicketkeeper_challenge(response: httpx.Response) -> bool:
    """Identifierar Labradors HTTP 200-svar utan att klassa annan HTML som PoW."""
    content_type = response.headers.get("content-type", "").lower()
    if response.status_code != 200 or "text/html" not in content_type:
        return False
    body = response.content[:64_000]
    return b"Are we human?" in body and b"/_labrador/pow/" in body


def solve_pow(challenge: str, difficulty: int) -> tuple[str, str]:
    """Returnerar decimal nonce och SHA-256 för Wicketkeepers challenge."""
    if len(challenge) != 32 or any(c not in "0123456789abcdef" for c in challenge):
        raise ValueError("ogiltig Wicketkeeper-challenge")
    if difficulty < 1 or difficulty > MAX_DIFFICULTY:
        raise ValueError("orimlig Wicketkeeper-svårighetsgrad")

    prefix = "0" * difficulty
    nonce = 0
    while True:
        result = hashlib.sha256(f"{challenge}{nonce}".encode()).hexdigest()
        if result.startswith(prefix):
            return str(nonce), result
        nonce += 1


class KTClient:
    """Trådsäker, återanvänd HTTP-klient med en processgemensam PoW-låsning."""

    def __init__(
        self,
        settings: Settings,
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers=settings.request_headers,
            transport=transport,
        )
        self._challenge_lock = threading.Lock()
        self._verification_generation = 0

    @property
    def cookies(self) -> httpx.Cookies:
        return self._client.cookies

    def close(self) -> None:
        self._client.close()

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Gör GET och löser högst en challenge innan originalanropet upprepas."""
        response = self._client.get(url, params=params, headers=headers)
        if not is_wicketkeeper_challenge(response):
            return response

        generation = self._verification_generation
        with self._challenge_lock:
            if self._verification_generation != generation:
                # En annan tråd hann verifiera sig medan vi väntade.
                response = self._client.get(url, params=params, headers=headers)
                if not is_wicketkeeper_challenge(response):
                    return response

            try:
                self._verify_pow()
            except (httpx.HTTPError, ValueError) as exc:
                raise WicketkeeperError(
                    f"Wicketkeeper-verifiering misslyckades: {exc}"
                ) from exc
            self._verification_generation += 1
            return self._client.get(url, params=params, headers=headers)

    def _verify_pow(self) -> None:
        response = self._client.get(CHALLENGE_URL)
        response.raise_for_status()
        try:
            payload = response.json()
            challenge = payload["challenge"]
            difficulty = int(payload["difficulty"])
            token = payload["token"]
        except (KeyError, TypeError, ValueError) as exc:
            raise httpx.HTTPError("ogiltigt Wicketkeeper-svar") from exc
        if not isinstance(challenge, str) or not isinstance(token, str) or not token:
            raise httpx.HTTPError("ogiltiga fält i Wicketkeeper-svaret")

        nonce, result = solve_pow(challenge, difficulty)
        verified = self._client.get(
            VERIFY_URL,
            headers={
                "pow-nonce": nonce,
                "pow-token": token,
                "pow-result": result,
            },
        )
        verified.raise_for_status()
        if COOKIE_NAME not in self._client.cookies:
            raise httpx.HTTPError("Wicketkeeper satte ingen verifieringscookie")
        logger.info("Wicketkeeper verifierad, återanvänder cookie-sessionen")


_shared_lock = threading.Lock()
_shared_client: KTClient | None = None


def get_kt_client(settings: Settings) -> KTClient:
    """Returnerar processens gemensamma KT-session."""
    global _shared_client
    with _shared_lock:
        if _shared_client is None:
            _shared_client = KTClient(settings)
        return _shared_client


def close_kt_client() -> None:
    """Stänger och glömmer den gemensamma sessionen vid appavslut."""
    global _shared_client
    with _shared_lock:
        if _shared_client is not None:
            _shared_client.close()
            _shared_client = None
