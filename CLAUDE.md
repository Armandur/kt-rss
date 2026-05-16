# CLAUDE.md - kt-rss

Kodbasöversikt för Claude Code. Uppdatera samtidigt som arkitektur- eller
filstrukturändringar.

## Vad projektet är

En tjänst som genererar RSS-feeds för Kyrkans Tidning. Pollar tidningens
interna JSON-API (`https://api.kyrkanstidning.se/article`), lagrar artiklar
i SQLite, exponerar Atom/RSS-feeds och ett styleat HTML-gränssnitt.

Fullständig styrande spec: `docs/SPEC.md`. Den är källan vid tvistefrågor.

## Stack

Python 3.12, FastAPI + uvicorn, APScheduler, httpx, feedgen, Jinja2,
pydantic-settings, SQLite (stdlib `sqlite3`, ingen ORM). Beroenden via
`uv` (`pyproject.toml` + `uv.lock`, `package = false`). Tester med pytest.
Ingen HTML-parser, ingen bundler, inga webbteckensnitt.

## Filstruktur

```
kt_rss/
  config.py       pydantic-settings + statiska API-konstanter + BOOTSTRAP-FYND
  api_client.py   httpx-GET: query-bygge, headers/UA, retry/backoff
  inspect.py      engångs bootstrap-utredning mot live-API (spec §10)
  db.py           SQLite-schema, mapping (map_article), dedup (upsert_article)
  poller.py       en pollningsrunda: sanity, filtrering, upsert
  scheduler.py    APScheduler - periodisk poll + uppstartspoll
  feed.py         feedgen: Atom/RSS-serialisering
  backfill.py     manuell CLI-backfill + uppstarts-backfill (spec §8.1)
  main.py         FastAPI-app: feeds, HTML-vyer, /healthz, lifespan
  templates/      base.html, index.html, list.html, _articles.html, tags.html, notfound.html
  static/         style.css, scroll.js (infinite scroll), feedbuilder.js (/tags)
tests/            offline mot tests/fixtures/article_response.json
docs/SPEC.md      fullständig projektspecifikation
```

## Datamodell (SQLite)

`init_db()` i `db.py` skapar schemat. Inga migrationer/Alembic - framtida
kolumnändringar görs med `ALTER TABLE`-guards i `init_db()`.

- `articles` - en rad per artikel. PK `id` (API:ets id som text). `bodytext`
  finns medvetet INTE (innehållspolicy, se nedan).
- `fetch_state` - en rad (`key='default'`): pollningstillstånd, `last_status`,
  `total_count` m.m.

Dedup: `upsert_article()` rör aldrig `first_seen`; uppdaterar `last_seen`
varje runda och `title`/`subtitle`/`modified_at` vid ändring.

## Bootstrap-fynd

`config.py`-avsnittet BOOTSTRAP-FYND innehåller det `inspect.py` fastställde
mot live-API:et: `FIRST_PAGE_START = 0`, offset-paginering, `orderBy=published`
är nyaste-först, inga `ETag`/`Last-Modified`. Ändra inte utan att köra om
`inspect.py`. Råsvar: `tests/fixtures/article_response.json`.

## Innehållspolicy (rör inte)

API:et returnerar full `bodytext` även för betalartiklar. Den återpubliceras
aldrig: `map_article()` läser den inte, `Article` saknar fältet, och feeds/
HTML visar rubrik, ingress, länk och artikelbild - aldrig brödtext.
Kommentarer i `db.py`, `feed.py` och `list.html` markerar detta - "optimera"
inte bort dem.

## Hövlighet (rör inte)

Pollintervall minst 15 min, bara nyaste sidan per poll, ärlig User-Agent,
timeout + få retries, endast GET. Se `api_client.py` och spec §3.

## URL-schema

`/` index, `/articles` + `/s/{section}` + `/t/{tag}` HTML-listor, `/tags`
bygg-en-feed-vy, `/feed.xml` + `/feed/{section}.xml` + `/feed/t/{tag}.xml` +
`/feed/tags.xml` (flera taggar, `?t=a,b&mode=or/and`) Atom (`?fmt=rss` ger
RSS). Sektioner och taggar är datadrivna (`section_tag` respektive den
tvättade `tags`-kolumnen) - hårdkoda aldrig listorna. Taggar tvättas i
`map_article` (`_clean_tags`). `/feed/tags.xml` måste registreras före
`/feed/{section}.xml` (annars matchas "tags" som ett section-värde).

## Vanliga ändringar

- Ny feed-egenskap: `feed.py` (`build_feed`).
- Ändrad pollningslogik/sanity: `poller.py`.
- Nya/ändrade fält: `db.py` (`Article`, `map_article`, schema i `init_db()`).
- UI: `templates/` + `static/style.css`.
- Ny env-variabel: `config.py` (`Settings`) + `.env.example` + README-tabell.

## Verifiering

```bash
uv run pytest                                          # offline, mot fixtures
uv run python -c "from kt_rss.main import app; print('OK')"
```

Testerna kräver inget nät. `inspect.py` och `backfill.py` anropar live-API:et.
