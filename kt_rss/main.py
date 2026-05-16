"""FastAPI-app: feeds, styleat webbgränssnitt, healthz (spec SS9).

URL-schema:
    /                     HTML-startsida (sektionskort + feed-länkar)
    /articles             HTML-lista, alla artiklar
    /s/{section}          HTML-lista per sektion
    /feed.xml             Atom, alla sektioner (?fmt=rss för RSS 2.0)
    /feed/{section}.xml   Atom per sektion (?fmt=rss för RSS 2.0)
    /healthz              JSON-status
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from kt_rss.config import Settings, get_settings
from kt_rss.db import (
    connect,
    count_articles,
    get_articles,
    get_fetch_state,
    init_db,
    list_sections,
    list_tags,
)
from kt_rss.db import STATUS_OK, STATUS_SKIPPED_304
from kt_rss.feed import build_feed, build_image_url
from kt_rss.scheduler import create_scheduler

logger = logging.getLogger("kt_rss")

_BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))

SV_MONTHS = [
    "", "januari", "februari", "mars", "april", "maj", "juni",
    "juli", "augusti", "september", "oktober", "november", "december",
]

# Tidsstämplar lagras i UTC men visas i svensk tid i gränssnittet.
SV_TZ = ZoneInfo("Europe/Stockholm")


def _to_sv(iso: str | None) -> datetime | None:
    """Parsar ISO 8601 och konverterar till svensk tid (Europe/Stockholm)."""
    try:
        d = datetime.fromisoformat(iso)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(SV_TZ)


def _sv_date(iso: str | None) -> str:
    """ISO 8601 -> '16 maj 2026' i svensk tid."""
    d = _to_sv(iso)
    return f"{d.day} {SV_MONTHS[d.month]} {d.year}" if d else ""


def _sv_datetime(iso: str | None) -> str:
    """ISO 8601 -> '16 maj 2026, 17:39' i svensk tid."""
    d = _to_sv(iso)
    return f"{d.day} {SV_MONTHS[d.month]} {d.year}, {d:%H:%M}" if d else ""


templates.env.filters["sv_date"] = _sv_date
templates.env.filters["sv_datetime"] = _sv_datetime
templates.env.globals["image_url"] = build_image_url


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initierar DB, startar schemaläggaren, städar vid avslut."""
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    init_db(settings.db_path)
    scheduler = create_scheduler(settings)
    scheduler.start()
    app.state.settings = settings
    app.state.scheduler = scheduler
    logger.info("kt-rss startad (db=%s)", settings.db_path)
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        logger.info("kt-rss stoppad")


app = FastAPI(title="kt-rss", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "static")), name="static")


def get_conn_settings(request: Request):
    """Dependency: öppnar en DB-anslutning + tillgång till settings."""
    settings: Settings = request.app.state.settings
    conn = connect(settings.db_path)
    try:
        yield conn, settings
    finally:
        conn.close()


def _known_sections(conn) -> list[str]:
    return [row["section"] for row in list_sections(conn)]


def _known_tags(conn) -> list[str]:
    return [tag for tag, _ in list_tags(conn)]


def _paginate(base_path: str, page: int, total: int, page_size: int) -> dict:
    """Pagineringsdata för en HTML-lista.

    Klampar `page` till giltigt intervall (out-of-bounds visar sista sidan),
    ger `offset` för DB-frågan och `next_page` (None om sista). `base` används
    av infinite scroll-JS för att hämta nästa batch.
    """
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    return {
        "page": page,
        "total_pages": total_pages,
        "offset": (page - 1) * page_size,
        "base": base_path,
        "next_page": page + 1 if page < total_pages else None,
    }


# --------------------------------------------------------------------------
# Feeds (XML)
# --------------------------------------------------------------------------

def _feed_response(xml: bytes, fmt: str) -> Response:
    media = (
        "application/rss+xml; charset=utf-8"
        if fmt == "rss"
        else "application/atom+xml; charset=utf-8"
    )
    return Response(
        content=xml,
        media_type=media,
        headers={"Cache-Control": "public, max-age=600"},
    )


@app.get("/feed.xml")
def feed_all(
    conn_settings=Depends(get_conn_settings),
    fmt: str = Query("atom", pattern="^(atom|rss)$"),
) -> Response:
    conn, settings = conn_settings
    articles = get_articles(conn, limit=settings.max_items)
    return _feed_response(build_feed(settings, articles, fmt=fmt), fmt)


@app.get("/feed/{section}.xml")
def feed_section(
    section: str,
    conn_settings=Depends(get_conn_settings),
    fmt: str = Query("atom", pattern="^(atom|rss)$"),
) -> Response:
    conn, settings = conn_settings
    known = _known_sections(conn)
    if section not in known:
        return JSONResponse(
            status_code=404,
            content={"error": f"okänd sektion: {section}", "valid_sections": known},
        )
    articles = get_articles(conn, section=section, limit=settings.max_items)
    return _feed_response(build_feed(settings, articles, section=section, fmt=fmt), fmt)


@app.get("/feed/t/{tag}.xml")
def feed_tag(
    tag: str,
    conn_settings=Depends(get_conn_settings),
    fmt: str = Query("atom", pattern="^(atom|rss)$"),
) -> Response:
    conn, settings = conn_settings
    tag_l = tag.strip().lower()
    known = _known_tags(conn)
    if tag_l not in known:
        return JSONResponse(
            status_code=404,
            content={"error": f"okänd tagg: {tag}", "valid_tags": known},
        )
    articles = get_articles(conn, tag=tag_l, limit=settings.max_items)
    return _feed_response(build_feed(settings, articles, tag=tag_l, fmt=fmt), fmt)


# --------------------------------------------------------------------------
# Styleat webbgränssnitt (HTML)
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, conn_settings=Depends(get_conn_settings)):
    conn, settings = conn_settings
    state = get_fetch_state(conn)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "sections": list_sections(conn),
            "total_articles": count_articles(conn),
            "state": state,
        },
    )


@app.get("/articles", response_class=HTMLResponse)
def articles_all(
    request: Request,
    conn_settings=Depends(get_conn_settings),
    page: int = Query(1, ge=1),
    partial: int = Query(0),
):
    conn, settings = conn_settings
    pg = _paginate("/articles", page, count_articles(conn), settings.page_size)
    articles = get_articles(conn, limit=settings.page_size, offset=pg["offset"])
    if partial:
        return templates.TemplateResponse(
            request, "_articles.html", {"articles": articles}
        )
    return templates.TemplateResponse(
        request,
        "list.html",
        {
            "articles": articles,
            "title": "Alla artiklar",
            "section": None,
            "feed_path": "/feed.xml",
            "state": get_fetch_state(conn),
            "pagination": pg,
        },
    )


@app.get("/s/{section}", response_class=HTMLResponse)
def articles_section(
    section: str,
    request: Request,
    conn_settings=Depends(get_conn_settings),
    page: int = Query(1, ge=1),
    partial: int = Query(0),
):
    conn, settings = conn_settings
    counts = {r["section"]: r["count"] for r in list_sections(conn)}
    if section not in counts:
        return templates.TemplateResponse(
            request,
            "notfound.html",
            {"kind": "sektion", "name": section, "sections": list(counts)},
            status_code=404,
        )
    pg = _paginate(
        f"/s/{quote(section)}", page, counts[section], settings.page_size
    )
    articles = get_articles(
        conn, section=section, limit=settings.page_size, offset=pg["offset"]
    )
    if partial:
        return templates.TemplateResponse(
            request, "_articles.html", {"articles": articles}
        )
    return templates.TemplateResponse(
        request,
        "list.html",
        {
            "articles": articles,
            "title": section,
            "section": section,
            "feed_path": f"/feed/{section}.xml",
            "state": get_fetch_state(conn),
            "pagination": pg,
        },
    )


@app.get("/t/{tag}", response_class=HTMLResponse)
def articles_tag(
    tag: str,
    request: Request,
    conn_settings=Depends(get_conn_settings),
    page: int = Query(1, ge=1),
    partial: int = Query(0),
):
    conn, settings = conn_settings
    tag_l = tag.strip().lower()
    counts = dict(list_tags(conn))
    if tag_l not in counts:
        return templates.TemplateResponse(
            request,
            "notfound.html",
            {"kind": "tagg", "name": tag, "sections": _known_sections(conn)},
            status_code=404,
        )
    pg = _paginate(
        f"/t/{quote(tag_l)}", page, counts[tag_l], settings.page_size
    )
    articles = get_articles(
        conn, tag=tag_l, limit=settings.page_size, offset=pg["offset"]
    )
    if partial:
        return templates.TemplateResponse(
            request, "_articles.html", {"articles": articles}
        )
    return templates.TemplateResponse(
        request,
        "list.html",
        {
            "articles": articles,
            "title": f"Tagg: {tag_l}",
            "section": None,
            "tag": tag_l,
            "feed_path": f"/feed/t/{quote(tag_l)}.xml",
            "state": get_fetch_state(conn),
            "pagination": pg,
        },
    )


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------

@app.get("/healthz")
def healthz(conn_settings=Depends(get_conn_settings)) -> JSONResponse:
    conn, settings = conn_settings
    state = get_fetch_state(conn)
    last_status = state["last_status"] if state else None
    last_run = state["last_run_at"] if state else None
    successful = last_run if last_status in (STATUS_OK, STATUS_SKIPPED_304) else None
    return JSONResponse(
        {
            "status": "ok",
            "article_count": count_articles(conn),
            "total_count_remote": state["total_count"] if state else 0,
            "last_status": last_status,
            "last_poll_at": last_run,
            "last_successful_poll": successful,
            "sections": {r["section"]: r["count"] for r in list_sections(conn)},
        }
    )
