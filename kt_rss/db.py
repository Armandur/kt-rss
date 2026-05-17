"""SQLite-lager: schema, mapping från API-objekt, dedup och feed-querys.

Ingen ORM, ingen Alembic - schemat skapas i init_db(). Framtida
kolumnändringar görs med ALTER TABLE-guards här inne.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

# Status som lagras i fetch_state.last_status (spec SS6).
STATUS_OK = "ok"
STATUS_SKIPPED_304 = "skipped_304"
STATUS_ERROR = "error"
STATUS_SANITY_FAILED = "sanity_failed"


def now_utc() -> str:
    """Aktuell tid som ISO 8601 i UTC, sekundprecision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _modified_to_iso(modified: object) -> str | None:
    """`modified` är en Unix-timestamp (sek) som sträng/heltal, eller saknas."""
    if modified in (None, "", 0, "0"):
        return None
    try:
        ts = int(str(modified).strip())
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat()


def _clean_url(base_url: str, published_url: str) -> str:
    """Bygger absolut artikel-URL och strippar query/fragment defensivt.

    `published_url` är en relativ path; ev. tracking-parametrar har inget
    i en stabil artikel-URL att göra och tas bort vid insert (spec SS6).
    """
    absolute = urljoin(base_url + "/", published_url.lstrip("/"))
    parts = urlparse(absolute)
    return urlunparse((parts.scheme, parts.netloc, parts.path, "", "", ""))


def _clean_tags(raw_tags: object, section: str) -> str:
    """Tvättar API:ets kommaseparerade `tags`-sträng till en visningsklar lista.

    Tar bort den interna markeringen `out`, taggar identiska med artikelns
    `section_tag` och dubbletter. Inre whitespace kollapsas, allt blir
    gemener. Returnerar en ', '-joinad sträng, tom om inget blir kvar.
    """
    section_l = section.strip().lower()
    seen: list[str] = []
    for part in str(raw_tags or "").split(","):
        tag = " ".join(part.lower().split())
        if not tag or tag == "out" or tag == section_l or tag in seen:
            continue
        seen.append(tag)
    return ", ".join(seen)


@dataclass
class Article:
    """En artikel mappad från API-svaret - de fält v1 faktiskt lagrar (SS6)."""

    id: str
    url: str
    title: str
    subtitle: str
    section: str
    kicker: str
    author: str
    published_at: str
    modified_at: str | None
    is_paywalled: int
    tags: str
    image_id: str


def map_article(raw: dict, base_url: str) -> Article:
    """Mappar ett rått API-artikelobjekt till en Article.

    Defensivt: fält kan saknas eller ha fel typ. `bodytext` läses aldrig
    in - den återanvänds aldrig i v1 (spec SS7) och får inte "optimeras in".
    """
    paywall = str(raw.get("paywall") or "") == "1"
    internal = str(raw.get("isInternalPaywall") or "") == "1"
    section = str(raw.get("section_tag") or "").strip()
    return Article(
        id=str(raw.get("id") or "").strip(),
        url=_clean_url(base_url, str(raw.get("published_url") or "")),
        title=str(raw.get("title") or "").strip(),
        subtitle=str(raw.get("subtitle") or "").strip(),
        section=section,
        kicker=str(raw.get("kicker") or "").strip(),
        author=str(raw.get("byline_names") or "").strip(),
        published_at=str(raw.get("published") or "").strip(),
        modified_at=_modified_to_iso(raw.get("modified")),
        is_paywalled=1 if (paywall or internal) else 0,
        tags=_clean_tags(raw.get("tags"), section),
        image_id=str(raw.get("image") or "").strip(),
    )


def connect(db_path: str) -> sqlite3.Connection:
    """Öppnar en anslutning med Row-factory och WAL (poller skriver, webben läser)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(db_path: str) -> None:
    """Skapar databasen och schemat om de saknas."""
    parent = Path(db_path).expanduser().parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id            TEXT PRIMARY KEY,
                url           TEXT NOT NULL,
                title         TEXT NOT NULL,
                subtitle      TEXT NOT NULL DEFAULT '',
                section       TEXT NOT NULL DEFAULT '',
                kicker        TEXT NOT NULL DEFAULT '',
                author        TEXT NOT NULL DEFAULT '',
                published_at  TEXT NOT NULL,
                modified_at   TEXT,
                is_paywalled  INTEGER NOT NULL DEFAULT 0,
                tags          TEXT NOT NULL DEFAULT '',
                image_id      TEXT NOT NULL DEFAULT '',
                first_seen    TEXT NOT NULL,
                last_seen     TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_articles_published
                ON articles (published_at DESC);
            CREATE INDEX IF NOT EXISTS idx_articles_section
                ON articles (section, published_at DESC);

            CREATE TABLE IF NOT EXISTS fetch_state (
                key           TEXT PRIMARY KEY,
                etag          TEXT,
                last_modified TEXT,
                last_run_at   TEXT,
                last_count    INTEGER NOT NULL DEFAULT 0,
                last_status   TEXT,
                total_count   INTEGER NOT NULL DEFAULT 0
            );

            INSERT OR IGNORE INTO fetch_state (key) VALUES ('default');
            """
        )
        # ALTER TABLE-guards: kolumner som tillkom efter v1 (ROADMAP v2).
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(articles)")}
        if "tags" not in cols:
            conn.execute(
                "ALTER TABLE articles ADD COLUMN tags TEXT NOT NULL DEFAULT ''"
            )
        if "image_id" not in cols:
            conn.execute(
                "ALTER TABLE articles ADD COLUMN image_id TEXT NOT NULL DEFAULT ''"
            )

        # Fulltextindex (FTS5) för artikelsök. External content mot articles;
        # triggrarna håller indexet synkat. Skapas efter ALTER-guardarna så
        # att tags/author garanterat finns när triggrarna refererar dem.
        conn.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
                title, subtitle, tags, author,
                content='articles', content_rowid='rowid'
            );
            CREATE TRIGGER IF NOT EXISTS articles_fts_ai
              AFTER INSERT ON articles BEGIN
                INSERT INTO articles_fts(rowid, title, subtitle, tags, author)
                VALUES (new.rowid, new.title, new.subtitle, new.tags, new.author);
            END;
            CREATE TRIGGER IF NOT EXISTS articles_fts_ad
              AFTER DELETE ON articles BEGIN
                INSERT INTO articles_fts(articles_fts, rowid, title, subtitle,
                                         tags, author)
                VALUES ('delete', old.rowid, old.title, old.subtitle,
                        old.tags, old.author);
            END;
            CREATE TRIGGER IF NOT EXISTS articles_fts_au
              AFTER UPDATE ON articles BEGIN
                INSERT INTO articles_fts(articles_fts, rowid, title, subtitle,
                                         tags, author)
                VALUES ('delete', old.rowid, old.title, old.subtitle,
                        old.tags, old.author);
                INSERT INTO articles_fts(rowid, title, subtitle, tags, author)
                VALUES (new.rowid, new.title, new.subtitle, new.tags, new.author);
            END;
            """
        )
        # Bygg FTS-indexet från articles. 'rebuild' är det kanoniska sättet
        # för en external-content-tabell - idempotent och billigt, så det
        # körs vid varje init: fyller indexet när en databas uppgraderas
        # från pre-FTS och rättar ev. drift. Triggrarna håller det sedan
        # synkat löpande.
        conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
        conn.commit()
    finally:
        conn.close()


def upsert_article(conn: sqlite3.Connection, a: Article) -> str:
    """INSERT vid okänt id, annars uppdatera last_seen (+ ändrade fält).

    Returnerar 'inserted', 'updated' eller 'unchanged'. Commit görs av
    anroparen (pollern committar en gång per runda).
    """
    now = now_utc()
    row = conn.execute(
        "SELECT title, subtitle, modified_at, tags, image_id "
        "FROM articles WHERE id = ?",
        (a.id,),
    ).fetchone()

    if row is None:
        conn.execute(
            """
            INSERT INTO articles (
                id, url, title, subtitle, section, kicker, author,
                published_at, modified_at, is_paywalled, tags, image_id,
                first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (a.id, a.url, a.title, a.subtitle, a.section, a.kicker, a.author,
             a.published_at, a.modified_at, a.is_paywalled, a.tags, a.image_id,
             now, now),
        )
        return "inserted"

    # Känd artikel: first_seen rör vi aldrig. title/subtitle/modified_at/tags/
    # image_id uppdateras vid ändring (spec SS8:7); last_seen uppdateras alltid.
    changed = (
        row["title"] != a.title
        or row["subtitle"] != a.subtitle
        or row["modified_at"] != a.modified_at
        or row["tags"] != a.tags
        or row["image_id"] != a.image_id
    )
    if changed:
        conn.execute(
            "UPDATE articles SET title = ?, subtitle = ?, modified_at = ?, "
            "tags = ?, image_id = ?, last_seen = ? WHERE id = ?",
            (a.title, a.subtitle, a.modified_at, a.tags, a.image_id, now, a.id),
        )
        return "updated"

    conn.execute("UPDATE articles SET last_seen = ? WHERE id = ?", (now, a.id))
    return "unchanged"


def get_fetch_state(conn: sqlite3.Connection) -> sqlite3.Row:
    """Hämtar den enda fetch_state-raden (key='default')."""
    return conn.execute(
        "SELECT * FROM fetch_state WHERE key = 'default'"
    ).fetchone()


def save_fetch_state(
    conn: sqlite3.Connection,
    *,
    etag: str | None,
    last_modified: str | None,
    last_count: int,
    last_status: str,
    total_count: int,
) -> None:
    """Skriver fullständig fetch_state efter en lyckad runda."""
    conn.execute(
        "UPDATE fetch_state SET etag = ?, last_modified = ?, last_run_at = ?, "
        "last_count = ?, last_status = ?, total_count = ? WHERE key = 'default'",
        (etag, last_modified, now_utc(), last_count, last_status, total_count),
    )
    conn.commit()


def touch_run(conn: sqlite3.Connection, last_status: str) -> None:
    """Uppdaterar bara last_run_at + last_status (304, fel, sanity-fail).

    Rör inte last_count/total_count - urvalet ska inte påverkas av en
    runda som inte skrev några artiklar.
    """
    conn.execute(
        "UPDATE fetch_state SET last_run_at = ?, last_status = ? "
        "WHERE key = 'default'",
        (now_utc(), last_status),
    )
    conn.commit()


def get_articles(
    conn: sqlite3.Connection,
    *,
    section: str | None = None,
    tag: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[sqlite3.Row]:
    """Artiklar sorterade på published_at fallande.

    Filtrerar på `section` eller `tag` om angivet. `tag` matchas mot en hel
    token i den ', '-joinade tags-kolumnen - inte som delsträng, så 'kyrka'
    träffar inte 'svenska kyrkan'. `offset` hoppar förbi rader (paginering).
    """
    if tag is not None:
        return conn.execute(
            "SELECT * FROM articles "
            "WHERE instr(', ' || tags || ', ', ', ' || ? || ', ') > 0 "
            "ORDER BY published_at DESC LIMIT ? OFFSET ?",
            (tag, limit, offset),
        ).fetchall()
    if section is None:
        return conn.execute(
            "SELECT * FROM articles ORDER BY published_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM articles WHERE section = ? "
        "ORDER BY published_at DESC LIMIT ? OFFSET ?",
        (section, limit, offset),
    ).fetchall()


def get_articles_for_tags(
    conn: sqlite3.Connection,
    tags: list[str],
    *,
    mode: str = "or",
    limit: int = 50,
) -> list[sqlite3.Row]:
    """Artiklar som matchar flera taggar - OR (någon tagg) eller AND (alla).

    Varje tagg går in som en ?-parameter; bara joinern (AND/OR) sätts av den
    validerade `mode`-strängen, aldrig tagginnehållet. Tom lista in ger tom
    lista ut.
    """
    if not tags:
        return []
    cond = "instr(', ' || tags || ', ', ', ' || ? || ', ') > 0"
    joiner = " AND " if mode == "and" else " OR "
    where = joiner.join([cond] * len(tags))
    return conn.execute(
        f"SELECT * FROM articles WHERE ({where}) "
        "ORDER BY published_at DESC LIMIT ?",
        (*tags, limit),
    ).fetchall()


def _fts_query(raw: str) -> str | None:
    """Bygger en FTS5 MATCH-fras av användarinput.

    Varje ord citeras så det tolkas som en literal term - inga FTS5-
    operatorer (`OR`, `*`, `NEAR` ...) läcker in från användaren. Tokens som
    själva innehåller citattecken släpps. Returnerar None om inget blir kvar.
    """
    parts = ['"' + t + '"' for t in raw.split() if t and '"' not in t]
    return " ".join(parts) if parts else None


def search_articles(
    conn: sqlite3.Connection, query: str, *, limit: int = 50
) -> list[sqlite3.Row]:
    """Fulltextsök på titel, ingress, taggar och författare (FTS5).

    Orden bildar en AND-sökning av literala termer; FTS5:s unicode61-
    tokenizer ger korrekt versal- och åäö-hantering. Resultaten rankas på
    relevans (bm25, lägre = mer relevant). Tom/ogiltig sökterm ger tom lista.
    """
    match = _fts_query(query)
    if match is None:
        return []
    return conn.execute(
        "SELECT a.* FROM articles a "
        "JOIN articles_fts ON articles_fts.rowid = a.rowid "
        "WHERE articles_fts MATCH ? "
        "ORDER BY bm25(articles_fts), a.published_at DESC LIMIT ?",
        (match, limit),
    ).fetchall()


def list_sections(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Alla sektioner med antal - datadrivet, inget hårdkodas (spec SS4)."""
    return conn.execute(
        "SELECT section, COUNT(*) AS count FROM articles "
        "WHERE section <> '' GROUP BY section ORDER BY section"
    ).fetchall()


def list_tags(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Alla taggar med artikelantal, härlett ur den tvättade tags-kolumnen."""
    counts: dict[str, int] = {}
    for row in conn.execute("SELECT tags FROM articles WHERE tags <> ''"):
        for tag in row["tags"].split(", "):
            if tag:
                counts[tag] = counts.get(tag, 0) + 1
    return sorted(counts.items())


def count_articles(conn: sqlite3.Connection) -> int:
    """Totalt antal lagrade artiklar."""
    return conn.execute("SELECT COUNT(*) AS c FROM articles").fetchone()["c"]
