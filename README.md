# kt-rss

RSS-feeds för Kyrkans Tidning. Tidningen saknar publik RSS-feed, men
webbplatsens "hämta fler"-knapp anropar ett internt JSON-API. `kt-rss`
pollar det API:et periodiskt, lagrar artiklarna i SQLite och exponerar
genererade **Atom/RSS-feeds** - totalt och per sektion - samt ett enkelt
styleat webbgränssnitt för att bläddra.

Feeden innehåller bara rubrik, ingress och länk. Brödtext återpubliceras
aldrig (se [Innehållspolicy](#innehållspolicy)).

## Vad du får

| URL | Beskrivning |
|-----|-------------|
| `/` | Startsida: sektionskort + feed-länkar |
| `/articles` | Stylead HTML-lista, alla artiklar |
| `/s/{section}` | Stylead HTML-lista per sektion |
| `/t` | Taggöversikt - taggmoln att bläddra från |
| `/t/{tag}` | Stylead HTML-lista per tagg |
| `/tags` | Bygg en feed på flera taggar (sök + kryssrutor) |
| `/search` | Sök artiklar på rubrik och ingress (`?q=`) |
| `/feed.xml` | Atom, alla sektioner (`?fmt=rss` ger RSS 2.0) |
| `/feed/{section}.xml` | Atom per sektion (`?fmt=rss` ger RSS 2.0) |
| `/feed/t/{tag}.xml` | Atom per tagg (`?fmt=rss` ger RSS 2.0) |
| `/feed/tags.xml` | Atom på flera taggar (`?t=a,b&mode=or/and`) |
| `/feeds.opml` | OPML med alla sektionsfeeds (importeras i en RSS-läsare) |
| `/healthz` | JSON-status: antal artiklar, senaste poll m.m. |

Sektioner härleds från datan (`section_tag`) - inget hårdkodas.

## Köra lokalt (dev-VM)

Kräver Python 3.12+ och [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env          # lokalt: KT_RSS_DB_PATH=data/kt.sqlite3 (inte /data/...)
uv sync --extra dev
uv run uvicorn kt_rss.main:app --reload
```

Appen pollar API:et kort efter start och sedan var `KT_RSS_POLL_MINUTES`:e
minut. Öppna http://localhost:8000.

Tester (offline, mot sparade fixtures - inget nät krävs):

```bash
uv run pytest
```

### Bootstrap-utredning

`kt_rss/inspect.py` gjorde de hövliga live-anrop som låste API-kontraktet
(paginering, sorteringsordning, bild-API). Fynden står som konstanter i
`kt_rss/config.py`. Behöver normalt inte köras om:

```bash
uv run python -m kt_rss.inspect
```

## Köra med Docker (hemma-deploy)

```bash
cp .env.example .env          # sätt KT_RSS_PUBLIC_URL till din riktiga URL
docker compose up -d --build
```

SQLite ligger på den namngivna volymen `kt-rss-data` och överlever
omstart. Appen hanterar inte TLS - kör bakom en reverse proxy (Caddy /
Cloudflare Tunnel) och sätt `KT_RSS_PUBLIC_URL` till den publika adressen.

Samma image kan byggas i CI och dras in i homelab-stacken; `docker compose
up` på dev-VM:en och prod-stacken hemma använder alltså samma image.

## Konfiguration

Alla variabler har prefix `KT_RSS_` och läses från miljön eller `.env`.
Se [`.env.example`](.env.example) för fullständig lista. De viktigaste:

| Variabel | Default | Not |
|----------|---------|-----|
| `KT_RSS_DB_PATH` | `/data/kt.sqlite3` | Lokalt: `data/kt.sqlite3` |
| `KT_RSS_POLL_MINUTES` | `15` | Klamras till minst 15 |
| `KT_RSS_PUBLIC_URL` | `http://localhost:8000` | Länkbas i feeds |
| `KT_RSS_MAX_FETCH` | `50` | Artiklar per poll |
| `KT_RSS_MAX_ITEMS` | `50` | Items per feed |
| `KT_RSS_PAGE_SIZE` | `50` | Artiklar per sida i webui (infinite scroll) |
| `KT_RSS_SECTION_ALLOWLIST` | (tom = alla) | Komma-separerade `section_tag` |
| `KT_RSS_INCLUDE_IMAGE_ENCLOSURE` | `true` | Artikelbild som enclosure i feeds |
| `KT_RSS_BACKFILL_PAGES` | `0` | Backfill vid start: sidor à 100 artiklar (0 = av, -1 = hela arkivet) |

## Hövlighet mot KT:s API

Detta är icke-förhandlingsbart - bygg inte bort det:

- Pollningsintervall minst 15 min (även default).
- Endast nyaste sidan per poll (`KT_RSS_MAX_FETCH` artiklar). Hela arkivet
  hämtas aldrig automatiskt.
- Ärlig, identifierande User-Agent (`kt-rss-bridge/<version>`).
- Kort timeout, få retries med exponentiell backoff, endast `GET`.

## Manuell backfill

Engångsverktyg för att fylla arkivet bakåt. **Av som default**, långsamt,
avbrytbart - inte del av den löpande driften:

```bash
uv run python -m kt_rss.backfill --pages 10 --delay 3
```

Paginerar bakåt via offset tills `--pages` nås eller arkivet är slut.
Senaste klarade offset sparas i en sidecar-fil bredvid databasen, så en
avbruten körning kan återupptas genom att köra kommandot igen.

## Innehållspolicy

API:et returnerar full brödtext även för betalartiklar. Det utnyttjas
**inte**: feed-items och HTML-vyer innehåller bara `title`, `subtitle`
(ingress) och länk till artikeln på kyrkanstidning.se. `bodytext` lagras
aldrig och finns inte ens i datamodellen. Detta är ett medvetet
upphovsrättsbeslut - se `kt_rss/db.py` och spec §7.

`kt-rss` är ett inofficiellt, självhostat verktyg utan koppling till
Kyrkans Tidning. Allt innehåll tillhör tidningen.

## Mer

- `CLAUDE.md` - kodbasöversikt
- `docs/SPEC.md` - fullständig projektspecifikation
- `ROADMAP.md` - planerat (v2)
