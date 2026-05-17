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

import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from itertools import groupby
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from kt_rss.backfill import maybe_start_backfill
from kt_rss.config import DEBATE_SECTIONS, Settings, get_settings
from kt_rss.db import (
    connect,
    count_articles,
    get_articles,
    get_articles_for_tags,
    get_fetch_state,
    init_db,
    list_archive_months,
    list_authors,
    list_authors_split,
    list_sections,
    list_tags,
    recent_polls,
    search_articles,
)
from kt_rss.db import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_SANITY_FAILED,
    STATUS_SKIPPED_304,
)
from kt_rss.feed import build_feed, build_image_url, build_opml
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


def _human_size(num: int) -> str:
    """Antal bytes -> kort läsbar storlek (B/KB/MB)."""
    size = float(num)
    for unit in ("B", "KB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} MB"


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
    maybe_start_backfill(settings)
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


def _known_authors(conn) -> list[str]:
    return [name for name, _ in list_authors(conn)]


def _tag_cloud(tags: list[tuple[str, int]]) -> list[tuple[str, int, int]]:
    """Lägger en storleksnivå 1-4 på varje tagg, kvantil-baserad.

    Taggarna delas i fyra lika stora grupper efter artikelantal, så
    fördelningen blir jämn oavsett hur skev count-fördelningen är. Storleken
    speglar alltså rang ("bland de 25 % mest använda"), inte absolut antal -
    en log- eller linjär skala klumpar nästan allt på minsta nivån.
    Ordningen i utdata följer inordningen (alfabetisk från list_tags).
    """
    if not tags:
        return []
    n = len(tags)
    rank = {
        tag: i
        for i, (tag, _) in enumerate(sorted(tags, key=lambda tc: tc[1]))
    }
    return [
        (tag, count, 1 + min(3, rank[tag] * 4 // n))
        for tag, count in tags
    ]


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

def _feed_response(request: Request, xml: bytes, fmt: str) -> Response:
    """Feed-svar med ETag - svarar 304 om klienten redan har samma innehåll.

    ETag är en hash av den serialiserade feeden; build_feed är deterministisk,
    så den ändras precis när feedinnehållet gör det. En RSS-läsare som ekar
    tillbaka ETag:en i If-None-Match får 304 utan kropp och sparar bandbredd.
    """
    media = (
        "application/rss+xml; charset=utf-8"
        if fmt == "rss"
        else "application/atom+xml; charset=utf-8"
    )
    etag = '"' + hashlib.md5(xml, usedforsecurity=False).hexdigest() + '"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=600"}
    inm = request.headers.get("if-none-match", "")
    if etag in {t.strip() for t in inm.split(",")}:
        return Response(status_code=304, headers=headers)
    return Response(content=xml, media_type=media, headers=headers)


@app.get("/feed.xml")
def feed_all(
    request: Request,
    conn_settings=Depends(get_conn_settings),
    fmt: str = Query("atom", pattern="^(atom|rss)$"),
) -> Response:
    conn, settings = conn_settings
    articles = get_articles(conn, limit=settings.max_items)
    return _feed_response(request, build_feed(settings, articles, fmt=fmt), fmt)


# Statisk route - måste stå före /feed/{section}.xml (annars matchar
# "tags" som ett section-värde).
@app.get("/feed/tags.xml")
def feed_tags(
    request: Request,
    conn_settings=Depends(get_conn_settings),
    t: str = Query(""),
    mode: str = Query("or", pattern="^(or|and)$"),
    fmt: str = Query("atom", pattern="^(atom|rss)$"),
) -> Response:
    conn, settings = conn_settings
    known = set(_known_tags(conn))
    tags = [
        x for x in (s.strip().lower() for s in t.split(",")) if x and x in known
    ]
    if not tags:
        return JSONResponse(
            status_code=404,
            content={"error": "inga giltiga taggar i parametern t",
                     "valid_tags": sorted(known)},
        )
    articles = get_articles_for_tags(
        conn, tags, mode=mode, limit=settings.max_items
    )
    return _feed_response(
        request, build_feed(settings, articles, tags=tags, mode=mode, fmt=fmt), fmt
    )


@app.get("/feed/search.xml")
def feed_search(
    request: Request,
    conn_settings=Depends(get_conn_settings),
    q: str = Query(""),
    fmt: str = Query("atom", pattern="^(atom|rss)$"),
) -> Response:
    conn, settings = conn_settings
    query = q.strip()
    if not query:
        return JSONResponse(
            status_code=400, content={"error": "saknar sökterm q"}
        )
    articles = search_articles(conn, query, limit=settings.max_items)
    return _feed_response(
        request, build_feed(settings, articles, query=query, fmt=fmt), fmt
    )


@app.get("/feed/{section}.xml")
def feed_section(
    section: str,
    request: Request,
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
    return _feed_response(
        request, build_feed(settings, articles, section=section, fmt=fmt), fmt
    )


@app.get("/feed/t/{tag}.xml")
def feed_tag(
    tag: str,
    request: Request,
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
    return _feed_response(
        request, build_feed(settings, articles, tag=tag_l, fmt=fmt), fmt
    )


@app.get("/feed/a/{author}.xml")
def feed_author(
    author: str,
    request: Request,
    conn_settings=Depends(get_conn_settings),
    fmt: str = Query("atom", pattern="^(atom|rss)$"),
) -> Response:
    conn, settings = conn_settings
    if author not in _known_authors(conn):
        return JSONResponse(
            status_code=404, content={"error": f"okänd skribent: {author}"}
        )
    articles = get_articles(conn, author=author, limit=settings.max_items)
    return _feed_response(
        request, build_feed(settings, articles, author=author, fmt=fmt), fmt
    )


@app.get("/feeds.opml")
def feeds_opml(conn_settings=Depends(get_conn_settings)) -> Response:
    conn, settings = conn_settings
    return Response(
        content=build_opml(settings, _known_sections(conn)),
        media_type="text/x-opml; charset=utf-8",
    )


# --------------------------------------------------------------------------
# Styleat webbgränssnitt (HTML)
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, conn_settings=Depends(get_conn_settings)):
    conn, settings = conn_settings
    total = count_articles(conn)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "sections": list_sections(conn),
            "total_articles": total,
            "state": get_fetch_state(conn),
            "articles": get_articles(conn, limit=settings.page_size),
            # Sentineln pekar på /articles - startsidans lista är dess sida 1.
            "pagination": _paginate("/articles", 1, total, settings.page_size),
            "feed_path": "/feed.xml",
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


@app.get("/a", response_class=HTMLResponse)
def author_index(request: Request, conn_settings=Depends(get_conn_settings)):
    conn, settings = conn_settings
    editorial, debate = list_authors_split(conn, DEBATE_SECTIONS)
    return templates.TemplateResponse(
        request,
        "author_index.html",
        {
            "editorial": editorial,
            "debate": debate,
            "state": get_fetch_state(conn),
        },
    )


@app.get("/a/{author}", response_class=HTMLResponse)
def articles_author(
    author: str,
    request: Request,
    conn_settings=Depends(get_conn_settings),
    page: int = Query(1, ge=1),
    partial: int = Query(0),
):
    conn, settings = conn_settings
    counts = dict(list_authors(conn))
    if author not in counts:
        return templates.TemplateResponse(
            request,
            "notfound.html",
            {"kind": "skribent", "name": author,
             "sections": _known_sections(conn)},
            status_code=404,
        )
    pg = _paginate(
        f"/a/{quote(author)}", page, counts[author], settings.page_size
    )
    articles = get_articles(
        conn, author=author, limit=settings.page_size, offset=pg["offset"]
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
            "title": author,
            "section": None,
            "feed_path": f"/feed/a/{quote(author)}.xml",
            "state": get_fetch_state(conn),
            "pagination": pg,
        },
    )


@app.get("/archive", response_class=HTMLResponse)
def archive_index(request: Request, conn_settings=Depends(get_conn_settings)):
    conn, settings = conn_settings
    years = [
        (
            year,
            [
                {
                    "month": int(r["month"]),
                    "name": SV_MONTHS[int(r["month"])],
                    "count": r["count"],
                }
                for r in items
            ],
        )
        for year, items in groupby(
            list_archive_months(conn), key=lambda r: r["year"]
        )
    ]
    return templates.TemplateResponse(
        request,
        "archive_index.html",
        {"years": years, "state": get_fetch_state(conn)},
    )


@app.get("/archive/{year}/{month}", response_class=HTMLResponse)
def archive_month(
    year: int,
    month: int,
    request: Request,
    conn_settings=Depends(get_conn_settings),
    page: int = Query(1, ge=1),
    partial: int = Query(0),
):
    conn, settings = conn_settings
    period = f"{year:04d}-{month:02d}"
    counts = {
        f"{r['year']}-{r['month']}": r["count"]
        for r in list_archive_months(conn)
    }
    if period not in counts:
        return templates.TemplateResponse(
            request,
            "notfound.html",
            {"kind": "period", "name": period,
             "sections": _known_sections(conn)},
            status_code=404,
        )
    pg = _paginate(
        f"/archive/{year}/{month}", page, counts[period], settings.page_size
    )
    articles = get_articles(
        conn, period=period, limit=settings.page_size, offset=pg["offset"]
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
            "title": f"{SV_MONTHS[month].capitalize()} {year}",
            "section": None,
            "feed_path": None,
            "state": get_fetch_state(conn),
            "pagination": pg,
        },
    )


@app.get("/tags", response_class=HTMLResponse)
def tag_feed_builder(request: Request, conn_settings=Depends(get_conn_settings)):
    conn, settings = conn_settings
    return templates.TemplateResponse(
        request,
        "tags.html",
        {"tags": list_tags(conn), "state": get_fetch_state(conn)},
    )


@app.get("/t", response_class=HTMLResponse)
def tag_index(request: Request, conn_settings=Depends(get_conn_settings)):
    conn, settings = conn_settings
    return templates.TemplateResponse(
        request,
        "tag_index.html",
        {"cloud": _tag_cloud(list_tags(conn)), "state": get_fetch_state(conn)},
    )


@app.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    conn_settings=Depends(get_conn_settings),
    q: str = Query(""),
):
    conn, settings = conn_settings
    results = search_articles(conn, q, limit=settings.page_size)
    q_clean = q.strip()
    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "q": q,
            "articles": results,
            "state": get_fetch_state(conn),
            "feed_path": (
                f"/feed/search.xml?q={quote(q_clean)}" if q_clean else None
            ),
        },
    )


@app.get("/suggest")
def suggest(
    conn_settings=Depends(get_conn_settings),
    q: str = Query(""),
) -> JSONResponse:
    """Förslag på taggar och skribenter för sökrutans autocomplete."""
    conn, _ = conn_settings
    term = q.strip().lower()
    if len(term) < 2:
        return JSONResponse({"tags": [], "authors": []})
    tags = [t for t, _ in list_tags(conn) if term in t][:6]
    authors = [a for a, _ in list_authors(conn) if term in a.lower()][:6]
    return JSONResponse({"tags": tags, "authors": authors})


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------

STATUS_LABELS = {
    STATUS_OK: "OK",
    STATUS_SKIPPED_304: "Inget nytt (304)",
    STATUS_ERROR: "Fel vid hämtning",
    STATUS_SANITY_FAILED: "Sanity-koll slog till",
}


@app.get("/status", response_class=HTMLResponse)
def status_page(request: Request, conn_settings=Depends(get_conn_settings)):
    conn, settings = conn_settings
    state = get_fetch_state(conn)
    tags = list_tags(conn)
    editorial, debate = list_authors_split(conn, DEBATE_SECTIONS)
    months = list_archive_months(conn)
    db_file = Path(settings.db_path)
    db_bytes = sum(
        p.stat().st_size
        for p in (db_file, db_file.with_name(db_file.name + "-wal"))
        if p.exists()
    )
    last_run = state["last_run_at"] if state else None
    last_status = state["last_status"] if state else None

    def _period(row) -> str:
        return f"{SV_MONTHS[int(row['month'])]} {row['year']}"

    polls = [
        {
            "run_at": _sv_datetime(r["run_at"]),
            "status": STATUS_LABELS.get(r["status"], r["status"]),
            "fetched": r["fetched"],
            "inserted": r["inserted"],
            "updated": r["updated"],
        }
        for r in recent_polls(conn)
    ]
    # Stapelgraf: senaste tolv månaderna i kronologisk ordning, höjd i
    # procent av den mest artikelrika månaden.
    chart_months = list(months[:12])[::-1]
    max_count = max((r["count"] for r in chart_months), default=1)
    chart = [
        {
            "label": f"{SV_MONTHS[int(r['month'])][:3]} {r['year'][2:]}",
            "count": r["count"],
            "pct": round(r["count"] / max_count * 100),
        }
        for r in chart_months
    ]

    return templates.TemplateResponse(
        request,
        "status.html",
        {
            "state": state,
            "article_count": count_articles(conn),
            "remote_count": state["total_count"] if state else 0,
            "last_poll": _sv_datetime(last_run) if last_run else None,
            "last_status": STATUS_LABELS.get(last_status, last_status or "-"),
            "last_count": state["last_count"] if state else 0,
            "poll_minutes": settings.poll_minutes,
            "sections": list_sections(conn),
            "tag_count": len(tags),
            "top_tags": sorted(tags, key=lambda t: (-t[1], t[0]))[:15],
            "editorial_count": len(editorial),
            "debate_count": len(debate),
            "month_count": len(months),
            "newest_period": _period(months[0]) if months else None,
            "oldest_period": _period(months[-1]) if months else None,
            "db_size": _human_size(db_bytes),
            "polls": polls,
            "chart": chart,
        },
    )


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
