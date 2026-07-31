"""Felnotiser till privat ntfy (infra-policyn `ntfy-notifieringspolicy.md`).

Notifierar på TILLSTÅNDSÖVERGÅNG, aldrig per poll: en notis när pollningen
har misslyckats `notify_after_failures` rundor i rad, och en när den är
frisk igen. Tröskeln filtrerar bort enstaka blippar och gör att en
flappande tjänst inte kan spamma kanalen (policyns avsnitt 6 - därför
behövs ingen separat rate-limiter).

Modulen kastar aldrig vidare: en trasig notifieringskanal får inte påverka
pollningen. Allt loggas.

Vad detta INTE täcker: dör containern slutar schemaläggaren köra och ingen
kod här hinner larma. Det kräver en extern heartbeat-vakt (infra TASK-653).
"""

from __future__ import annotations

import logging

import httpx

from kt_rss.config import Settings
from kt_rss.db import (
    STATUS_OK,
    STATUS_SANITY_FAILED,
    connect,
    consecutive_failures,
    get_fetch_state,
    set_alert_active,
)

logger = logging.getLogger("kt_rss.notify")

# Kort timeout: anropet sker på schemaläggartråden och får inte fördröja
# nästa pollrunda om ntfy hänger.
NTFY_TIMEOUT_SECONDS = 5.0

# "Titta idag" enligt policyns nivåtabell - en feed-bro är inte något man
# går upp klockan tre för.
PRIORITY_PROBLEM = "3"
PRIORITY_RECOVERED = "2"


def send(
    settings: Settings,
    *,
    title: str,
    message: str,
    priority: str,
    tags: str,
    click: str | None = None,
) -> bool:
    """Skickar en notis. Returnerar False vid fel (som också loggas)."""
    if not settings.notify_enabled:
        logger.debug("notifiering av (ntfy_topic/ntfy_token saknas)")
        return False

    headers = {
        "Authorization": f"Bearer {settings.ntfy_token}",
        "Title": title,
        "Priority": priority,
        "Tags": tags,
    }
    if click:
        headers["X-Click"] = click
    try:
        response = httpx.post(
            f"{settings.ntfy_url}/{settings.ntfy_topic}",
            content=message.encode("utf-8"),
            headers=headers,
            timeout=NTFY_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception:
        logger.exception("kunde inte skicka ntfy-notis: %s", title)
        return False
    logger.info("ntfy-notis skickad: %s", title)
    return True


def handle_poll_status(settings: Settings, status: str) -> None:
    """Kör tillståndsmaskinen efter en avslutad pollrunda.

    Egen anslutning: körs på schemaläggartråden och SQLite-anslutningar
    delas inte mellan trådar.
    """
    if not settings.notify_enabled:
        return
    try:
        conn = connect(settings.db_path)
        try:
            _evaluate(settings, conn)
        finally:
            conn.close()
    except Exception:
        logger.exception("fel i notifieringskontrollen - pollningen påverkas inte")


def _evaluate(settings: Settings, conn) -> None:
    state = get_fetch_state(conn)
    alert_active = bool(state["alert_active"]) if state else False
    threshold = settings.notify_after_failures
    # Fönstret måste rymma tröskeln, annars kan den aldrig nås.
    fails = consecutive_failures(conn, limit=max(20, threshold + 1))
    status_url = f"{settings.public_url}/status"

    if fails == 0:
        if alert_active:
            last_status = state["last_status"] if state else STATUS_OK
            ok = send(
                settings,
                title="kt-rss / pollning",
                message=(
                    "Pollningen fungerar igen "
                    f"(senaste runda: {last_status})."
                ),
                priority=PRIORITY_RECOVERED,
                tags="white_check_mark,newspaper",
                click=status_url,
            )
            # Flaggan nollas bara om notisen faktiskt gick iväg - annars
            # försöker nästa runda igen.
            if ok:
                set_alert_active(conn, False)
        return

    if fails < threshold or alert_active:
        return

    last_status = state["last_status"] if state else "okänt"
    if last_status == STATUS_SANITY_FAILED:
        what = (
            "API-svaret ser trasigt ut (för få eller inga artiklar) - "
            "inget har skrivits till databasen."
        )
    else:
        what = "API:et går inte att hämta från."
    minutes = fails * settings.poll_minutes
    ok = send(
        settings,
        title="kt-rss / pollning",
        message=(
            f"{fails} misslyckade pollningar i rad (cirka {minutes} min). "
            f"{what} Feeden serverar allt äldre artiklar."
        ),
        priority=PRIORITY_PROBLEM,
        tags="warning,newspaper",
        click=status_url,
    )
    # Nådde notisen aldrig fram får nästa runda försöka igen - annars vore
    # nästa signal ett ensamt "fungerar igen" för ett fel ingen hört om.
    if ok:
        set_alert_active(conn, True)
