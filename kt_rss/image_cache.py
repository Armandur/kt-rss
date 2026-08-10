"""Persistent och rate-limitad lokal cache för KT:s artikelbilder."""

from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from kt_rss.config import IMAGE_API_BASE, Settings
from kt_rss.db import connect
from kt_rss.kt_client import (
    KTClient,
    WicketkeeperError,
    get_kt_client,
    is_wicketkeeper_challenge,
)

logger = logging.getLogger("kt_rss.images")

IMAGE_ID_RE = re.compile(r"^[0-9]+$")
MIN_IMAGE_BYTES = 100
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MIN_REQUEST_INTERVAL = 3.0
FAILURE_COOLDOWN = timedelta(hours=6)


@dataclass(frozen=True)
class ImageVariant:
    width: int
    height: int | None


VARIANTS = {
    "thumb": ImageVariant(width=480, height=312),
    "feed": ImageVariant(width=1200, height=None),
}


@dataclass(frozen=True)
class BackfillResult:
    selected: int
    cached: int
    failed: int
    skipped: int
    aborted: bool
    error: str = ""


def build_image_url(
    image_id: str,
    *,
    width: int | None = None,
    height: int | None = None,
    fmt: str = "webp",
) -> str:
    """Bygger KT:s fullbildscrop med valfri målbredd och målhöjd."""
    iid = str(image_id).strip()
    url = (
        f"{IMAGE_API_BASE}/{iid}.{fmt}?imageId={iid}"
        f"&x=0&y=0&cropw=100&croph=100"
        f"&heightx=0&heighty=0&heightw=100&heighth=100"
    )
    if width:
        url += f"&width={width}"
    if height:
        url += f"&height={height}"
    return f"{url}&format={fmt}"


def image_cache_root(settings: Settings) -> Path:
    """Lägger bilder bredvid SQLite-filen, normalt under /data/images."""
    return Path(settings.db_path).expanduser().parent / "images"


def image_cache_path(settings: Settings, image_id: str, variant: str) -> Path:
    if not IMAGE_ID_RE.fullmatch(str(image_id)):
        raise ValueError("ogiltigt bild-id")
    if variant not in VARIANTS:
        raise ValueError("ogiltig bildvariant")
    return image_cache_root(settings) / str(image_id) / f"{variant}.webp"


def local_image_url(settings: Settings, image_id: str, variant: str) -> str:
    image_cache_path(settings, image_id, variant)
    return f"{settings.public_url}/images/{image_id}/{variant}.webp"


def cached_image_url(
    settings: Settings, image_id: str, variant: str
) -> str | None:
    try:
        path = image_cache_path(settings, image_id, variant)
    except ValueError:
        return None
    return local_image_url(settings, image_id, variant) if path.is_file() else None


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    kind = data[12:16]
    if kind == b"VP8X":
        return (
            1 + int.from_bytes(data[24:27], "little"),
            1 + int.from_bytes(data[27:30], "little"),
        )
    if kind == b"VP8 ":
        marker = data.find(b"\x9d\x01\x2a", 20)
        if marker >= 0 and len(data) >= marker + 7:
            return (
                int.from_bytes(data[marker + 3:marker + 5], "little") & 0x3FFF,
                int.from_bytes(data[marker + 5:marker + 7], "little") & 0x3FFF,
            )
    if kind == b"VP8L" and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF))
    return None


def _validate_webp(data: bytes, variant: str) -> tuple[int, int]:
    if not MIN_IMAGE_BYTES <= len(data) <= MAX_IMAGE_BYTES:
        raise ValueError("orimlig bildstorlek")
    dimensions = _webp_dimensions(data)
    if dimensions is None:
        raise ValueError("svaret är inte en giltig WebP")
    expected = VARIANTS[variant]
    if dimensions[0] != expected.width:
        raise ValueError(f"oväntad bildbredd {dimensions[0]}")
    if expected.height is not None and dimensions[1] != expected.height:
        raise ValueError(f"oväntad bildhöjd {dimensions[1]}")
    return dimensions


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _may_attempt(settings: Settings, image_id: str, variant: str) -> bool:
    if _circuit_status(settings)["open"]:
        return False
    conn = connect(settings.db_path)
    try:
        row = conn.execute(
            "SELECT next_attempt_at FROM image_cache "
            "WHERE image_id = ? AND variant = ?",
            (image_id, variant),
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["next_attempt_at"]:
        return True
    try:
        return datetime.fromisoformat(row["next_attempt_at"]) <= datetime.now(
            timezone.utc
        )
    except ValueError:
        return True


def _circuit_status(settings: Settings) -> dict:
    conn = connect(settings.db_path)
    try:
        row = conn.execute(
            "SELECT circuit_open_until, last_error_at, last_error "
            "FROM image_cache_state WHERE key = 'default'"
        ).fetchone()
    finally:
        conn.close()
    open_until = row["circuit_open_until"] if row else None
    is_open = False
    if open_until:
        try:
            is_open = datetime.fromisoformat(open_until) > datetime.now(timezone.utc)
        except ValueError:
            pass
    return {
        "open": is_open,
        "open_until": open_until,
        "last_error_at": row["last_error_at"] if row else None,
        "last_error": row["last_error"] if row else "",
    }


def _open_circuit(settings: Settings, error: str) -> None:
    now = _now_iso()
    open_until = (datetime.now(timezone.utc) + FAILURE_COOLDOWN).isoformat()
    conn = connect(settings.db_path)
    try:
        conn.execute(
            "UPDATE image_cache_state SET circuit_open_until = ?, "
            "last_error_at = ?, last_error = ? WHERE key = 'default'",
            (open_until, now, error[:500]),
        )
        conn.commit()
    finally:
        conn.close()


def _record_result(
    settings: Settings,
    image_id: str,
    variant: str,
    source_url: str,
    *,
    status: str,
    size_bytes: int = 0,
    error: str = "",
) -> None:
    now = _now_iso()
    next_attempt = (
        (datetime.now(timezone.utc) + FAILURE_COOLDOWN).isoformat()
        if status == "error"
        else None
    )
    conn = connect(settings.db_path)
    try:
        conn.execute(
            """
            INSERT INTO image_cache (
                image_id, variant, status, source_url, size_bytes,
                fetched_at, last_attempt_at, next_attempt_at, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(image_id, variant) DO UPDATE SET
                status = excluded.status,
                source_url = excluded.source_url,
                size_bytes = excluded.size_bytes,
                fetched_at = excluded.fetched_at,
                last_attempt_at = excluded.last_attempt_at,
                next_attempt_at = excluded.next_attempt_at,
                last_error = excluded.last_error
            """,
            (
                image_id,
                variant,
                status,
                source_url,
                size_bytes,
                now if status == "cached" else None,
                now,
                next_attempt,
                error[:500],
            ),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_image(
    settings: Settings,
    image_id: str,
    variant: str,
    *,
    client: KTClient | None = None,
    raise_on_challenge: bool = False,
) -> bool:
    """Hämtar och skriver en variant atomiskt. Returnerar sant vid cacheträff."""
    path = image_cache_path(settings, image_id, variant)
    if path.is_file():
        return True
    if not _may_attempt(settings, image_id, variant):
        return False

    spec = VARIANTS[variant]
    source_url = build_image_url(
        image_id,
        width=spec.width,
        height=spec.height,
    )
    try:
        response = (client or get_kt_client(settings)).get(source_url)
        if is_wicketkeeper_challenge(response):
            raise WicketkeeperError("Wicketkeeper-challenge kvar efter verifiering")
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code}")
        if not response.headers.get("content-type", "").lower().startswith(
            "image/webp"
        ):
            raise ValueError("oväntad svarstyp")
        _validate_webp(response.content, variant)

        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_bytes(response.content)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        _record_result(
            settings,
            image_id,
            variant,
            source_url,
            status="cached",
            size_bytes=len(response.content),
        )
        logger.info(
            "bild cachad: %s/%s (%d byte)",
            image_id,
            variant,
            len(response.content),
        )
        return True
    except (httpx.HTTPError, OSError, ValueError) as exc:
        _record_result(
            settings,
            image_id,
            variant,
            source_url,
            status="error",
            error=str(exc),
        )
        if isinstance(exc, WicketkeeperError):
            _open_circuit(settings, str(exc))
        logger.warning("bildhämtning misslyckades: %s/%s: %s", image_id, variant, exc)
        if raise_on_challenge and isinstance(exc, WicketkeeperError):
            raise
        return False


class ImageCacheWorker:
    """En enda daemontråd med deduplicerad FIFO-kö och global rate limit."""

    def __init__(self, settings: Settings, *, abort_on_challenge: bool = False) -> None:
        self.settings = settings
        self._queue: queue.Queue[tuple[str, str] | None] = queue.Queue()
        self._queued: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="image-cache",
        )
        self._last_request = 0.0
        self._active: tuple[str, str] | None = None
        self._completed = 0
        self._failed = 0
        self._skipped = 0
        self._abort_on_challenge = abort_on_challenge
        self._abort_error = ""

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._queue.put(None)
            self._thread.join(timeout=5)

    def wait(self) -> None:
        self._queue.join()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self._thread.is_alive() and not self._stop.is_set(),
                "queued": max(0, len(self._queued) - (1 if self._active else 0)),
                "active": self._active,
                "completed": self._completed,
                "failed": self._failed,
                "skipped": self._skipped,
                "abort_error": self._abort_error,
            }

    def enqueue_many(self, image_ids: list[str]) -> None:
        valid = [
            str(image_id)
            for image_id in image_ids
            if IMAGE_ID_RE.fullmatch(str(image_id))
        ]
        # Alla thumbnails först så startsidan fylls innan feedvarianterna.
        for variant in VARIANTS:
            for image_id in valid:
                self.enqueue(image_id, variant)

    def enqueue_items(self, items: list[tuple[str, str]]) -> None:
        for image_id, variant in items:
            self.enqueue(image_id, variant)

    def enqueue(self, image_id: str, variant: str) -> None:
        try:
            path = image_cache_path(self.settings, image_id, variant)
        except ValueError:
            return
        if path.is_file():
            return
        item = (str(image_id), variant)
        with self._lock:
            if item in self._queued:
                return
            self._queued.add(item)
        self._queue.put(item)

    def _run(self) -> None:
        while not self._stop.is_set():
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            image_id, variant = item
            try:
                with self._lock:
                    self._active = item
                path = image_cache_path(self.settings, image_id, variant)
                if path.is_file() or not _may_attempt(
                    self.settings, image_id, variant
                ):
                    with self._lock:
                        self._skipped += 1
                    continue
                delay = MIN_REQUEST_INTERVAL - (time.monotonic() - self._last_request)
                if delay > 0 and self._stop.wait(delay):
                    return
                self._last_request = time.monotonic()
                try:
                    cached = fetch_image(
                        self.settings,
                        image_id,
                        variant,
                        raise_on_challenge=self._abort_on_challenge,
                    )
                except WicketkeeperError as exc:
                    with self._lock:
                        self._failed += 1
                        self._abort_error = str(exc)
                    self._drain_queue()
                    return
                with self._lock:
                    if cached:
                        self._completed += 1
                    else:
                        self._failed += 1
            finally:
                with self._lock:
                    self._queued.discard(item)
                    self._active = None
                self._queue.task_done()

    def _drain_queue(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if item is not None:
                with self._lock:
                    self._queued.discard(item)
                    self._skipped += 1
            self._queue.task_done()


_worker_lock = threading.Lock()
_worker: ImageCacheWorker | None = None


def image_cache_status(settings: Settings) -> dict:
    conn = connect(settings.db_path)
    try:
        totals = conn.execute(
            "SELECT COUNT(*) AS variants, COUNT(DISTINCT image_id) AS images, "
            "COALESCE(SUM(size_bytes), 0) AS size_bytes "
            "FROM image_cache WHERE status = 'cached'"
        ).fetchone()
        latest = conn.execute(
            "SELECT image_id, variant, last_attempt_at, last_error "
            "FROM image_cache WHERE status = 'error' "
            "ORDER BY last_attempt_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    circuit = _circuit_status(settings)
    with _worker_lock:
        worker = _worker
    queue_status = (
        worker.snapshot()
        if worker is not None and worker.settings.db_path == settings.db_path
        else {
            "running": False,
            "queued": 0,
            "active": None,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "abort_error": "",
        }
    )
    return {
        "cached_variants": totals["variants"],
        "cached_images": totals["images"],
        "size_bytes": totals["size_bytes"],
        "latest_error": dict(latest) if latest else None,
        "circuit": circuit,
        "queue": queue_status,
    }


def select_backfill_items(
    settings: Settings, variants: list[str], limit: int
) -> list[tuple[str, str]]:
    if limit < 1:
        raise ValueError("limit måste vara minst 1")
    if not variants or any(variant not in VARIANTS for variant in variants):
        raise ValueError("ogiltig bildvariant")
    conn = connect(settings.db_path)
    try:
        image_ids = [
            row["image_id"]
            for row in conn.execute(
                "SELECT image_id, MAX(published_at) AS newest "
                "FROM articles WHERE image_id <> '' "
                "GROUP BY image_id ORDER BY newest DESC, image_id DESC"
            )
            if IMAGE_ID_RE.fullmatch(row["image_id"])
        ]
    finally:
        conn.close()

    selected: list[tuple[str, str]] = []
    for variant in variants:
        for image_id in image_ids:
            if image_cache_path(settings, image_id, variant).is_file():
                continue
            if not _may_attempt(settings, image_id, variant):
                continue
            selected.append((image_id, variant))
            if len(selected) >= limit:
                return selected
    return selected


def run_image_backfill(
    settings: Settings, *, variants: list[str], limit: int
) -> BackfillResult:
    items = select_backfill_items(settings, variants, limit)
    if not items:
        return BackfillResult(0, 0, 0, 0, False)
    worker = ImageCacheWorker(settings, abort_on_challenge=True)
    worker.start()
    try:
        worker.enqueue_items(items)
        worker.wait()
        result = worker.snapshot()
    finally:
        worker.stop()
    return BackfillResult(
        selected=len(items),
        cached=result["completed"],
        failed=result["failed"],
        skipped=result["skipped"],
        aborted=bool(result["abort_error"]),
        error=result["abort_error"],
    )


def start_image_worker(settings: Settings) -> ImageCacheWorker:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = ImageCacheWorker(settings)
            _worker.start()
        return _worker


def enqueue_images(settings: Settings, image_ids: list[str]) -> None:
    with _worker_lock:
        worker = _worker
    if worker is not None and worker.settings.db_path == settings.db_path:
        try:
            worker.enqueue_many(image_ids)
        except Exception:
            logger.exception("kunde inte köa artikelbilder")


def stop_image_worker() -> None:
    global _worker
    with _worker_lock:
        worker = _worker
        _worker = None
    if worker is not None:
        worker.stop()
