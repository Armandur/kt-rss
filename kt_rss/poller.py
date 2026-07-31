"""Pollningslogik (spec SS8).

En runda: hämta nyaste sidan, sanity-kolla, filtrera på publik status,
upserta artiklar, skriv fetch_state. Rundan får ALDRIG kasta vidare till
schemaläggaren - allt är inkapslat i poll_once().
"""

from __future__ import annotations

import logging

from kt_rss.api_client import fetch_articles
from kt_rss.config import FIRST_PAGE_START, Settings
from kt_rss.db import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_SANITY_FAILED,
    STATUS_SKIPPED_304,
    connect,
    get_fetch_state,
    log_poll,
    map_article,
    save_fetch_state,
    touch_run,
    upsert_article,
)
from kt_rss.notify import handle_poll_status

logger = logging.getLogger("kt_rss.poller")

# Sanity: en runda med färre än SANITY_RATIO * förra rundans antal artiklar
# behandlas som misslyckad och skriver inget (spec SS8:5).
SANITY_RATIO = 0.4


def poll_once(settings: Settings) -> dict:
    """Kör en pollningsrunda. Returnerar en sammanfattning, kastar aldrig.

    Ett oväntat undantag får inte döda schemaläggaren - därför fångas allt
    här, stacktrace loggas, och nästa schemalagda körning fortsätter.
    """
    try:
        summary = _do_poll(settings)
    except Exception:
        logger.exception(
            "oväntat fel i pollningsrunda - schemaläggaren påverkas inte"
        )
        try:
            conn = connect(settings.db_path)
            try:
                touch_run(conn, STATUS_ERROR)
            finally:
                conn.close()
        except Exception:
            logger.exception("kunde inte skriva error-status till fetch_state")
        summary = {"status": STATUS_ERROR}

    # Pollhistorik för statussidan. Egen connection och egen try - en miss
    # i loggningen får inte påverka pollresultatet.
    try:
        conn = connect(settings.db_path)
        try:
            log_poll(
                conn,
                status=summary.get("status", STATUS_ERROR),
                fetched=summary.get("fetched", 0),
                inserted=summary.get("inserted", 0),
                updated=summary.get("updated", 0),
            )
        finally:
            conn.close()
    except Exception:
        logger.exception("kunde inte skriva poll_log")

    # Felnotis på tillståndsövergång. Egen felhantering i handle_poll_status
    # - en trasig notifieringskanal får inte påverka pollresultatet.
    handle_poll_status(settings, summary.get("status", STATUS_ERROR))

    return summary


def _do_poll(settings: Settings) -> dict:
    conn = connect(settings.db_path)
    try:
        state = get_fetch_state(conn)
        last_count = (state["last_count"] if state else 0) or 0

        result = fetch_articles(
            settings,
            limit=settings.max_fetch,
            start=FIRST_PAGE_START,
            etag=state["etag"] if state else None,
            last_modified=state["last_modified"] if state else None,
        )

        if result.not_modified:
            logger.info("poll: 304 Not Modified - inget nytt")
            touch_run(conn, STATUS_SKIPPED_304)
            return {"status": STATUS_SKIPPED_304}

        if not result.ok:
            # 200 med oparsbar kropp är en sanity-fråga; övrigt är transportfel.
            status = (
                STATUS_SANITY_FAILED
                if result.status_code == 200
                else STATUS_ERROR
            )
            logger.warning("poll: hämtning misslyckades (%s): %s",
                           status, result.error)
            touch_run(conn, status)
            return {"status": status, "error": result.error}

        data = result.data or {}
        articles_raw = data.get("result") or []
        total_count = int(data.get("totalCount") or 0)

        # --- Sanity-check (spec SS8:5) ---
        if not articles_raw:
            logger.warning("poll: sanity - tomt/saknat 'result', skriver inget")
            touch_run(conn, STATUS_SANITY_FAILED)
            return {"status": STATUS_SANITY_FAILED}
        if last_count > 0 and len(articles_raw) < last_count * SANITY_RATIO:
            logger.warning(
                "poll: sanity - %d artiklar < %.0f%% av förra rundans %d, "
                "skriver inget", len(articles_raw), SANITY_RATIO * 100, last_count
            )
            touch_run(conn, STATUS_SANITY_FAILED)
            return {"status": STATUS_SANITY_FAILED}

        # --- Filtrering (spec SS8:6): bara publika, synliga artiklar ---
        allowed = settings.allowed_sections
        kept: list[dict] = []
        for raw in articles_raw:
            if str(raw.get("status") or "") != "P":
                continue
            if str(raw.get("visibility_status") or "") != "P":
                continue
            if allowed and str(raw.get("section_tag") or "").lower() not in allowed:
                continue
            kept.append(raw)

        # --- Upsert (spec SS8:7) ---
        counts = {"inserted": 0, "updated": 0, "unchanged": 0, "skipped": 0}
        for raw in kept:
            article = map_article(raw, settings.base_url)
            if not article.id or not article.published_at:
                logger.warning("poll: hoppar över artikel utan id/published: %r",
                               raw.get("id"))
                counts["skipped"] += 1
                continue
            counts[upsert_article(conn, article)] += 1

        # --- Skriv fetch_state (spec SS8:8) ---
        save_fetch_state(
            conn,
            etag=result.etag,
            last_modified=result.last_modified,
            last_count=len(articles_raw),
            last_status=STATUS_OK,
            total_count=total_count,
        )
        logger.info(
            "poll ok: %d hämtade, %d behållna (ny %d / uppd %d / of. %d / "
            "skip %d), totalCount=%d",
            len(articles_raw), len(kept), counts["inserted"], counts["updated"],
            counts["unchanged"], counts["skipped"], total_count,
        )
        return {
            "status": STATUS_OK,
            "fetched": len(articles_raw),
            "kept": len(kept),
            "total_count": total_count,
            **counts,
        }
    finally:
        conn.close()
