"""Schemaläggning av pollningsrundor (APScheduler).

En periodisk poll var KT_RSS_POLL_MINUTES; första körningen sker kort
efter uppstart så feeden inte är tom (spec SS8).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from kt_rss.config import Settings
from kt_rss.poller import poll_once

logger = logging.getLogger("kt_rss.scheduler")

# Kort fördröjning innan första pollen så appen hinner starta.
STARTUP_DELAY_SECONDS = 10


def create_scheduler(settings: Settings) -> BackgroundScheduler:
    """Skapar (men startar inte) schemaläggaren med poll-jobbet."""
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        poll_once,
        trigger="interval",
        minutes=settings.poll_minutes,
        args=[settings],
        id="poll",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc)
        + timedelta(seconds=STARTUP_DELAY_SECONDS),
    )
    logger.info(
        "schemaläggare skapad: poll var %d min, första om %d s",
        settings.poll_minutes, STARTUP_DELAY_SECONDS,
    )
    return scheduler
