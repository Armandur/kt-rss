# CLAUDE.md - kt-rss

Kodbasöversikt för Claude Code. Uppdatera samtidigt som arkitektur- eller
filstrukturändringar.

## Vad projektet är

`kt-rss` pollar Kyrkans Tidnings interna JSON-API
(`https://api.kyrkanstidning.se/article`), lagrar artiklar i SQLite och
blev med tiden två saker i ett:

- En **RSS-feed-tillhandahållare** - genererade Atom/RSS-feeds, totalt och
  per sektion, tagg och skribent.
- En **"läsare som inte läser"** - ett styleat HTML-gränssnitt för att
  bläddra, söka och upptäcka artiklar. Det visar rubrik, ingress och bild
  men aldrig brödtexten; varje artikel länkar vidare till KT
  (innehållspolicy, se nedan).

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
  api_client.py   httpx-GET: query-bygge, cache-buster, headers/UA, retry/backoff
  inspect.py      engångs bootstrap-utredning mot live-API (spec §10)
  db.py           SQLite-schema, mapping (map_article), dedup (upsert_article)
  poller.py       en pollningsrunda: sanity, filtrering, upsert
  notify.py       ntfy-felnotis på tillståndsövergång (ok<->fel)
  scheduler.py    APScheduler - periodisk poll + uppstartspoll
  feed.py         feedgen: Atom/RSS-serialisering
  backfill.py     manuell CLI-backfill + uppstarts-backfill (spec §8.1)
  main.py         FastAPI-app: feeds, HTML-vyer, /healthz, lifespan
  templates/      base.html, index.html, list.html, _articles.html, tags.html,
                  tag_index.html, author_index.html, archive_index.html,
                  status.html, search.html, notfound.html
  static/         style.css, scroll.js, feedbuilder.js, theme.js, tagcloud.js,
                  authors.js, newsince.js, suggest.js, live.js, totop.js,
                  kt-rss.svg + kt-rss-{32,64,128,256,512,1024}.png (logotyp)
tests/            offline mot tests/fixtures/article_response.json
docs/SPEC.md      fullständig projektspecifikation
assets/           designkällor utanför runtime (kt-rss.ai)
```

## Datamodell (SQLite)

`init_db()` i `db.py` skapar schemat. Inga migrationer/Alembic - framtida
kolumnändringar görs med `ALTER TABLE`-guards i `init_db()`. `init_db()`
kör dessutom en idempotent omtvätt av `author`-kolumnen (delar äldre
flerskribent-bylines) - billig att köra varje start, skriver bara rader
som faktiskt ändras.

- `articles` - en rad per artikel. PK `id` (API:ets id som text). `bodytext`
  finns medvetet INTE (innehållspolicy, se nedan).
- `fetch_state` - en rad (`key='default'`): pollningstillstånd, `last_status`,
  `total_count`, `alert_active` (om en felnotis är skickad och inte återställd)
  m.m.
- `poll_log` - en rad per pollrunda (tid, status, antal hämtade/nya/
  uppdaterade). `poll_once` skriver, `/status` visar pollhistoriken.
- `articles_fts` - FTS5-fulltextindex (external content mot `articles`,
  triggersynkat) för `/search`. `init_db()` kör `'rebuild'` vid start.

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

`/` index, `/articles` + `/s/{section}` + `/t/{tag}` + `/a/{author}`
+ `/archive/{år}/{månad}` HTML-listor, `/t` taggöversikt, `/a`
skribentöversikt, `/archive` arkivöversikt, `/tags` bygg-en-feed-vy,
`/search` artikelsök, `/suggest` (JSON-autocomplete för sökrutan),
`/latest` (JSON: antal nyare artiklar, driver liveuppdateringen),
`/status` status-/statistiksida, `/feed.xml` +
`/feed/{section}.xml` + `/feed/t/{tag}.xml` +
`/feed/a/{author}.xml` + `/feed/tags.xml` (flera taggar,
`?t=a,b&mode=or/and`) + `/feed/search.xml` (`?q=` sökterm) Atom
(`?fmt=rss` ger RSS). `/feeds.opml` listar
alla sektionsfeeds. Feed-svaren bär en `ETag` (`_feed_response`) och svarar
`304` på matchande `If-None-Match`. HTML-vyerna har
feed-autodiscovery (`<link rel="alternate">`) styrt av `feed_path`. Sektioner och taggar är datadrivna (`section_tag` respektive den
tvättade `tags`-kolumnen) - hårdkoda aldrig listorna. Taggar tvättas i
`map_article` (`_clean_tags`); skribenter likaså (`_clean_authors` delar
flerskribent-bylines på komma och ` och `). Skribentöversikten `/a` delar
skribenter i redaktion och debatt/insändare utifrån `DEBATE_SECTIONS` i
`config.py`. De statiska `/feed/tags.xml` och `/feed/search.xml` måste
registreras före `/feed/{section}.xml` (annars matchas "tags"/"search"
som section-värden).

## Vanliga ändringar

- Ny feed-egenskap: `feed.py` (`build_feed`).
- Ändrad pollningslogik/sanity: `poller.py`.
- Notiser (ntfy): `notify.py`. Bara tillståndsövergång, aldrig per poll -
  se `~/workspace/infra/docs/ntfy-notifieringspolicy.md`.
- Nya/ändrade fält: `db.py` (`Article`, `map_article`, schema i `init_db()`).
- UI: `templates/` + `static/style.css`.
- Ny env-variabel: `config.py` (`Settings`) + `.env.example` + README-tabell.

## Verifiering

```bash
uv run pytest                                          # offline, mot fixtures
uv run python -c "from kt_rss.main import app; print('OK')"
```

Testerna kräver inget nät. `inspect.py` och `backfill.py` anropar live-API:et.
