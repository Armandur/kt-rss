"""Explicit och återupptagbar uppvärmning av historiska artikelbilder.

Kör när webbappen är stoppad så att endast en bildworker använder KT-sessionen:

    python -m kt_rss.image_backfill --limit 100 --variant thumb

Redan cachade varianter och poster med aktiv cooldown hoppas över. En ny
körning fortsätter därför deterministiskt med nästa saknade variant.
"""

from __future__ import annotations

import argparse
import logging
import sys

from kt_rss.config import get_settings
from kt_rss.db import init_db
from kt_rss.image_cache import (
    VARIANTS,
    image_cache_status,
    run_image_backfill,
)
from kt_rss.kt_client import close_kt_client

logger = logging.getLogger("kt_rss.image_backfill")
DEFAULT_LIMIT = 100


def run_backfill(limit: int, variant: str) -> int:
    settings = get_settings()
    init_db(settings.db_path)
    variants = list(VARIANTS) if variant == "all" else [variant]

    before = image_cache_status(settings)
    if before["circuit"]["open"]:
        logger.error(
            "bildhämtning spärrad till %s: %s",
            before["circuit"]["open_until"],
            before["circuit"]["last_error"],
        )
        return 2

    logger.info(
        "startar bildbackfill: limit=%d, variant=%s", limit, variant
    )
    try:
        result = run_image_backfill(
            settings,
            variants=variants,
            limit=limit,
        )
    finally:
        close_kt_client()

    logger.info(
        "bildbackfill klar: valda %d, cachade %d, fel %d, överhoppade %d",
        result.selected,
        result.cached,
        result.failed,
        result.skipped,
    )
    if result.aborted:
        logger.error("avbröt efter Wicketkeeper-challenge: %s", result.error)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Värm historiska artikelbilder försiktigt. Kör när webbappen "
            "är stoppad; redan cachade bilder och aktiv cooldown hoppas över."
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"högsta antal bildvarianter i körningen (default {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--variant",
        choices=[*VARIANTS, "all"],
        default="thumb",
        help="bildvariant att värma (default thumb)",
    )
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit måste vara minst 1")
    logging.basicConfig(
        level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        return run_backfill(args.limit, args.variant)
    except KeyboardInterrupt:
        logger.warning("avbruten; nästa körning fortsätter vid nästa saknade bild")
        return 130


if __name__ == "__main__":
    sys.exit(main())
