"""HTTP-klient mot KT:s artikel-API (spec SS2 + SS3).

Endast GET. Korrekt URL-enkodad query, ärlig User-Agent, conditional
requests om validatorer finns, kort timeout och få retries med
exponentiell backoff. Ger upp tyst (loggar) hellre än att hamra.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from kt_rss.config import Settings

logger = logging.getLogger("kt_rss.api")

# query=% AND lab_site_id:(2)  -  % matchar allt, lab_site_id:(2) = KT.
# httpx percent-enkodar värdet (%->%25, mellanslag, : etc); API:et
# avkodar det korrekt - bekräftat av inspect.py 2026-05-16.
QUERY = "% AND lab_site_id:(2)"

TIMEOUT = 15.0
MAX_RETRIES = 2
BACKOFF_BASE = 2.0  # sekunder; exponentiellt: 2, 4
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def build_params(limit: int, start: int | None) -> dict[str, str]:
    """Query-parametrar för ett artikel-anrop (spec SS2)."""
    params: dict[str, str] = {
        "limit": str(limit),
        "query": QUERY,
        "altText": "1",
        "orderBy": "published",
    }
    if start is not None:
        params["start"] = str(start)
    return params


def build_query_string(limit: int, start: int | None) -> str:
    """Den faktiska URL-enkodade query-strängen - för tester och felsökning."""
    return str(httpx.QueryParams(build_params(limit, start)))


@dataclass
class FetchResult:
    """Resultat av ett artikel-anrop. `ok` täcker både 200 och 304."""

    ok: bool
    not_modified: bool = False
    status_code: int | None = None
    data: dict | None = None
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None


def fetch_articles(
    settings: Settings,
    *,
    limit: int,
    start: int | None,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FetchResult:
    """Hämtar en sida artiklar. Kastar aldrig - returnerar alltid ett FetchResult."""
    params = build_params(limit, start)
    # Cache-buster: KT:s API ligger bakom CloudFront + Varnish och kan
    # annars svara med timmar gamla cachade resultat (osynkade edge-noder).
    # En unik parameter per anrop ger en unik URL -> cache-miss -> färskt
    # svar från origin. Bryt inte ut den - utan den missar pollern artiklar.
    params["_"] = str(time.time_ns())
    headers = dict(settings.request_headers)
    # Conditional requests: API:et skickar inte ETag/Last-Modified i dag
    # (se config.CONDITIONAL_REQUEST_SUPPORT), men skicka validatorer om
    # de någonsin sparas - kostar inget och är korrekt.
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    last_error: str | None = None
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        for attempt in range(MAX_RETRIES + 1):
            if attempt:
                delay = BACKOFF_BASE * (2 ** (attempt - 1))
                logger.info("retry %d/%d efter %.0fs", attempt, MAX_RETRIES, delay)
                time.sleep(delay)
            try:
                resp = client.get(settings.api_url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("anrop misslyckades (försök %d): %s",
                               attempt + 1, last_error)
                continue

            if resp.status_code == 304:
                return FetchResult(ok=True, not_modified=True, status_code=304)

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError as exc:
                    return FetchResult(ok=False, status_code=200,
                                       error=f"oparsbar JSON: {exc}")
                return FetchResult(
                    ok=True,
                    status_code=200,
                    data=data,
                    etag=resp.headers.get("ETag"),
                    last_modified=resp.headers.get("Last-Modified"),
                )

            if resp.status_code in RETRYABLE_STATUS:
                last_error = f"HTTP {resp.status_code}"
                logger.warning("retrybar status %d (försök %d)",
                               resp.status_code, attempt + 1)
                continue

            # Icke-retrybar status (t.ex. 4xx) - ge upp direkt.
            return FetchResult(ok=False, status_code=resp.status_code,
                               error=f"HTTP {resp.status_code}")

    return FetchResult(ok=False, error=last_error or "okänt fel efter retries")
