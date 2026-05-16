"""Valfri manuell engångs-backfill (spec SS8.1).

    python -m kt_rss.backfill --pages N --delay S [--start OFFSET]

Paginerar bakåt via offset (start += limit) tills start >= totalCount eller
--pages-gränsen nås - samma terminering som webbklientens canPage (SS2.3).
AV som default, långsam och avbrytbar: senaste klarade offset sparas i en
sidecar-fil bredvid databasen så körningen kan återupptas. Ej del av driften.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

from kt_rss.api_client import fetch_articles
from kt_rss.config import get_settings
from kt_rss.db import connect, init_db, map_article, upsert_article

logger = logging.getLogger("kt_rss.backfill")

# Större sida än pollern - backfill är engångs, färre anrop är hövligare.
BACKFILL_LIMIT = 100
MIN_DELAY = 3.0


def _resume_file(db_path: str) -> Path:
    return Path(db_path + ".backfill")


def _done_file(db_path: str) -> Path:
    """Markör som skapas när hela arkivet är genomgånget - hindrar att
    uppstarts-backfill kör om allt vid varje containeromstart."""
    return Path(db_path + ".backfill-done")


def _is_public(raw: dict, allowed: set[str]) -> bool:
    """Samma publik-filter som pollern (spec SS8:6)."""
    if str(raw.get("status") or "") != "P":
        return False
    if str(raw.get("visibility_status") or "") != "P":
        return False
    if allowed and str(raw.get("section_tag") or "").lower() not in allowed:
        return False
    return True


def run_backfill(pages: int, delay: float, start: int | None) -> int:
    settings = get_settings()
    delay = max(MIN_DELAY, delay)
    init_db(settings.db_path)
    resume = _resume_file(settings.db_path)

    if start is None and resume.exists():
        try:
            start = int(resume.read_text().strip())
            logger.info("återupptar från sparad offset start=%d", start)
        except ValueError:
            start = 0
    if start is None:
        start = 0

    allowed = settings.allowed_sections
    conn = connect(settings.db_path)
    totals = {"inserted": 0, "updated": 0, "unchanged": 0}
    done = 0
    try:
        while done < pages:
            logger.info("backfill sida %d/%d (start=%d)", done + 1, pages, start)
            result = fetch_articles(settings, limit=BACKFILL_LIMIT, start=start)
            if not result.ok or not result.data:
                logger.error("avbryter: hämtning misslyckades (%s)", result.error)
                break
            articles = result.data.get("result") or []
            total_count = int(result.data.get("totalCount") or 0)
            if not articles:
                logger.info("inga fler artiklar - klart")
                resume.unlink(missing_ok=True)
                _done_file(settings.db_path).touch()
                break

            for raw in articles:
                if not _is_public(raw, allowed):
                    continue
                article = map_article(raw, settings.base_url)
                if not article.id or not article.published_at:
                    continue
                totals[upsert_article(conn, article)] += 1
            conn.commit()

            start += BACKFILL_LIMIT
            resume.write_text(str(start))
            done += 1

            if total_count and start >= total_count:
                logger.info("nådde totalCount=%d - klart", total_count)
                resume.unlink(missing_ok=True)
                _done_file(settings.db_path).touch()
                break
            time.sleep(delay)
    except KeyboardInterrupt:
        conn.commit()
        logger.warning(
            "avbruten - sparad offset start=%d, kör kommandot igen för "
            "att återuppta", start,
        )
    finally:
        conn.close()

    logger.info(
        "backfill klar: %d sidor, ny %d / uppd %d / of. %d",
        done, totals["inserted"], totals["updated"], totals["unchanged"],
    )
    return 0


def _should_backfill(settings) -> bool:
    """Sant om en uppstarts-backfill ska köras: aktiverad via env och
    arkivet inte redan genomgånget."""
    return settings.backfill_pages > 0 and not _done_file(settings.db_path).exists()


def maybe_start_backfill(settings) -> None:
    """Startar en bakgrunds-backfill vid uppstart om _should_backfill().

    Körs i en daemon-tråd så att appstart och /healthz inte blockeras - en
    full backfill tar tiotals minuter. run_backfill committar per sida och
    har en resume-sidecar, så en avbruten körning återupptas vid nästa start.
    """
    if not _should_backfill(settings):
        return
    logger.info(
        "backfill: startar bakgrundskörning, upp till %d sidor",
        settings.backfill_pages,
    )
    threading.Thread(
        target=run_backfill,
        args=(settings.backfill_pages, MIN_DELAY, None),
        daemon=True,
        name="backfill",
    ).start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Manuell engångs-backfill av KT-arkivet (spec SS8.1)."
    )
    parser.add_argument("--pages", type=int, default=1,
                        help="antal sidor att hämta (default 1)")
    parser.add_argument("--delay", type=float, default=MIN_DELAY,
                        help=f"sekunder mellan sidor (min {MIN_DELAY:.0f})")
    parser.add_argument("--start", type=int, default=None,
                        help="startoffset (default: återuppta sparad, annars 0)")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    return run_backfill(args.pages, args.delay, args.start)


if __name__ == "__main__":
    sys.exit(main())
