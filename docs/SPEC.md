# Projektspecifikation v2: `kt-rss` - RSS-feeds från Kyrkans Tidning

> Mata denna fil till Claude Code som projektbeskrivning. Läs hela specen först,
> ställ klargörande frågor om något är tvetydigt, börja sedan med projekt-
> struktur + datamodell innan poller och feed-generator.
>
> **v2-grund:** Projektet baseras på Kyrkans Tidnings interna JSON-API
> (upptäckt via "hämta fler"-knappen), inte HTML-scraping. Detta gör lösningen
> dramatiskt enklare och robustare. HTML-parsning utgår helt.

---

## 1. Syfte och kontext

Kyrkans Tidning (`https://www.kyrkanstidning.se`) saknar publik RSS-feed, men
webbplatsens "hämta fler"-funktion anropar ett internt JSON-API på
`https://api.kyrkanstidning.se/article`. API:et returnerar strukturerad
artikeldata: titel, underrubrik, publiceringsdatum, sektion och URL.

Detta projekt bygger en självhostad **feed-generator**: en liten tjänst som
periodiskt hämtar de senaste artiklarna via JSON-API:et, diffar mot en lokal
databas, och exponerar genererade Atom/RSS-feeds - totalt och per sektion.

**Utvecklingsflöde:**
1. Utveckling i en Linux-dev-VM med Claude Code.
2. Testdeploy på samma VM.
3. Slutlig drift hemma i en Docker-image (Compose-stack).

Tjänsten ska vara liten, läsbar, defensiv och driftsäker — den ska kunna köra
obevakad i månader.

---

## 2. API:et (känd information)

**Endpoint:** `GET https://api.kyrkanstidning.se/article`

**Verifierade query-parametrar:**

| Parameter  | Värde i observerat anrop  | Betydelse                                  |
|------------|---------------------------|--------------------------------------------|
| `limit`    | `100`                     | Antal artiklar per svar                     |
| `start`    | `100`                     | Offset för paginering (se §2.3 — oklarhet)  |
| `query`    | `% AND lab_site_id:(2)`   | `%` = matcha allt; `lab_site_id:(2)` = KT   |
| `altText`  | `1`                       | Inkluderar alt-texter; behåll               |
| `orderBy`  | `published`               | Sortering på publiceringsdatum              |

**Headers som ska skickas** (efterlikna webbklienten, var hövlig):
- `User-Agent`: konfigurerbar, ärlig (se §3)
- `Accept: */*`
- `Accept-Language: sv-SE,sv;q=0.9,en;q=0.8`
- `Accept-Encoding: gzip, deflate, br`
- `Referer: https://www.kyrkanstidning.se/`
- `Origin: https://www.kyrkanstidning.se`

**Svarsstruktur (JSON):**
```
{
  "totalCount": 32735,
  "result": [ { …artikelobjekt… }, … ],
  "nextPageToken": "…"   // kan finnas
}
```

**Relevanta fält per artikelobjekt** (många fält ignoreras):

| Fält                  | Användning                                                  |
|-----------------------|-------------------------------------------------------------|
| `id`                  | Numeriskt artikel-id. Stabil unik nyckel.                   |
| `published`           | ISO 8601 m. tidszon, `2026-04-27T11:00:32+02:00`. Feed-datum.|
| `modified`            | Unix-timestamp (sek) eller saknas. För uppdateringsdetektion.|
| `title`               | Rubrik.                                                     |
| `subtitle`            | Underrubrik/ingress. Feed-summary.                          |
| `published_url`       | Relativ path, `/teologi/...-1/433146`. Bygg absolut URL.    |
| `section_tag`         | Sektion: `nyhet`, `debatt`, `kultur`, `teologi`, `kronika`, `ledare`, `minnesord`, `församlingsliv`. |
| `kicker`              | Etikett: `Nyhet`, `Debatt`, `Krönika`, `Podd`, `Recension`, `Inför söndagen`, … |
| `byline_names`        | Författarnamn (sträng).                                     |
| `paywall` / `isInternalPaywall` | `"1"` = betalinnehåll. Styr bodytext-policy (§7).  |
| `bodytext`            | Brödtext — **innebörden varierar per artikeltyp, se §2.2**. Återpubliceras INTE i v1 (§7). |
| `status`              | `P` = publicerad. Filtrera.                                 |
| `visibility_status`   | `P` = synlig. Filtrera.                                     |
| `image`               | Primärt bild-id (sträng/heltal), t.ex. `"433455"`. För bild-API (§2.3). |
| `crop`                | Objekt med `pano`- och `height`-crop. Bygger bild-URL (§2.3).|
| `frontCropUrl`        | Färdig query-sträng för bild-API, t.ex. `?imageId=...&panox=0&...`. |
| `imageCaption`        | Bildtext.                                                    |
| `altText`             | Alt-text (när `altText=1` skickas i anropet).                |
| `stats_char_count` / `stats_word_count` | Hjälper bedöma om `bodytext` ser komplett ut (§2.2). |

### 2.1 `bodytext` är inte enhetlig — viktig nyans

`bodytext` är **inte** garanterat fullständig artikeltext för alla artikel-
typer. Mönstret i exempeldatan:

- **Text-artiklar** (debatt, krönika, ledare, recension, vanliga nyheter,
  "inför söndagen"/teologi): `bodytext` innehåller hela artikeltexten.
  Längden stämmer mot `stats_char_count`/`stats_word_count`.
  Exempel: debatt `id=433452`, krönika `id=433340`, nyhet `id=433212`.
- **Poddartiklar** (`kicker="Podd"`, `tags` innehåller `podd/podcast`):
  `bodytext` är en **beskrivning av poddavsnittet**, inte en transkription.
  Ibland längre, ibland en kort programtext. Exempel: Torsdagsdeppen
  `id=433121`, `id=432554`; Vox populi `id=433198`.
- **Notiser/korta nyheter** kan ha mycket kort `bodytext` (det är hela
  notisen, inte trunkering).

**Konsekvens för designen:**
- v1 återpublicerar ändå inte `bodytext` (§7), så detta påverkar inte v1
  funktionellt — men det ska dokumenteras så att en framtida v2 inte naivt
  antar "full text = artikeltext".
- Om v2 någon gång inkluderar ett textutdrag: använd `subtitle` som primär
  summary. Om `bodytext` används, behandla det som **osäkert fullständigt**
  och jämför längd mot `stats_char_count` som en grov heuristik; lita aldrig
  blint på att `bodytext` motsvarar hela den publicerade artikeln.
- Klassificera artikeltyp via `kicker` + `tags` (`podd`/`podcast`) snarare än
  att gissa från textlängd.

### 2.2 Bild-API (dokumenterat, ej använt i v1-feed-text men förberett)

Bilder serveras av ett separat bild-API som tar bild-id + crop-parametrar:

```
https://image.kyrkanstidning.se/{imageId}.{format}?imageId={imageId}
  &x={x}&y={y}&cropw={cropw}&croph={croph}
  &heightx={hx}&heighty={hy}&heightw={hw}&heighth={hh}
  &format={format}
```

Exempel (verifierat):
```
https://image.kyrkanstidning.se/433475.webp?imageId=433475
  &x=0.00&y=0.00&cropw=100.00&croph=100.00
  &heightx=0.00&heighty=0.00&heightw=100.00&heighth=100.00&format=webp
```

**Hur parametrarna mappar från artikel-API:et:**

- `imageId` = artikelns `image`-fält (primärt bild-id).
- Crop-värden finns i artikelns `crop`-objekt, som har två varianter:
  - `crop.pano` → används som `x`/`y`/`cropw`/`croph` (panorama-beskärning).
  - `crop.height` → används som `heightx`/`heighty`/`heightw`/`heighth`.
  - Varje delobjekt har nycklar `x`, `y`, `cropw`, `croph` (procentvärden
    som strängar, t.ex. `"0"`, `"100"`, `"71.67"`) samt `metadata_key`
    (`fcp`/`fch` — ignoreras för URL-bygget).
- API:et tillhandahåller dessutom en **färdig query-sträng i
  `frontCropUrl`**, t.ex.
  `?imageId=433455&panox=0&panow=100&panoh=100&panoy=0&heightx=0&heightw=41.43&heighth=100&heighty=0`.
  Notera att `frontCropUrl` använder prefixen `pano*`/`height*` medan det
  verifierade bild-API-exemplet använder `x/y/cropw/croph` + `height*`.
  **Bootstrap (§10) ska verifiera vilken parameteruppsättning bild-API:et
  faktiskt accepterar** innan URL-byggaren skrivs.

**Användning i detta projekt (v2):**
- `feed.py:build_image_url(image_id, *, width, height, fmt)` bygger bild-URL:er.
  Crop låses till full bild (`0/0/100/100`) - feeds och webui vill ha hela
  motivet, inte en panorama-crop. `width`/`height` ger en nedskalad rendition
  (KT:s egen listvy använder 240x156); webui skickar 480x312, feed-enclosure
  utelämnar storlek.
- Feeds: artikelbilden läggs som enclosure (Atom `<link rel="enclosure">` /
  RSS `<enclosure>`). feedgen 1.0.0 tappar attribut på Atom-entry-länkar, så
  `feed.py` lagar dem i efterhand (`_fix_atom_enclosure_links`).
- HTML: artikellistorna visar bilden som `loading="lazy"`-thumbnail.
- Env `KT_RSS_INCLUDE_IMAGE_ENCLOSURE` (default `true`) styr feed-enclosuren.

### 2.3 Känd oklarhet — MÅSTE utredas i bootstrap (§10)
I det observerade anropet fungerade `start=100` men `start=0` rapporterades
**inte** ge resultat. Fastställ innan pollern kodas:

- Fungerar `start=0`? Minsta fungerande `start`? Behövs `start` alls för
  första sidan?
- Är `orderBy=published` garanterat nyaste-först (descending)?
- Vad gör `nextPageToken` — krävs den för paginering, eller räcker
  `start`/`limit`?
- Är `published` alltid satt och i ISO 8601? (I exempeldatan: ja.)

**Ledtråd från webbklientens "hämta fler"-knapp:**
```js
e => { e.preventDefault(), this.searchSettings.canPage && this.nextPage() }
```
Klienten pagineras via en boolean `searchSettings.canPage` + en inkrementell
`nextPage()`, inte genom att konsumera en token i taget. Detta är ett **starkt
indicium** för att:
- Pagineringen är **offset-baserad** (`start += limit`), inte tvingande
  token-baserad. `nextPageToken` i svaret är då sannolikt valfri/optimering.
- Termineringsvillkoret motsvarar troligen `start + limit < totalCount`
  (dvs. det `canPage` representerar).

Bootstrap (§10) ska **bekräfta** detta empiriskt, inte anta det. Om
offset-paginering bekräftas: implementera loopen som `start += limit` tills
`start >= totalCount`, vilket speglar klientens `canPage`-logik. Behåll
`nextPageToken`-hantering som en valfri fallback om `start`-pagineringen visar
sig bete sig oväntat.

Bootstrap-hjälparen (§10) besvarar dessa **mot live-API:et med ett fåtal
hövliga anrop** och sparar råsvar som fixtures innan vidare kodning.

---

## 3. Etik och hövlighet (icke-förhandlingsbart)

- **Pollningsintervall:** default 15 min, konfigurerbart, aldrig under 15 min.
- **Endast nyaste sidan per poll:** hämta `KT_RSS_MAX_FETCH` (default 50) av de
  nyaste artiklarna. Hämta INTE hela arkivet annat än via valfri manuell
  backfill (§8.1).
- **User-Agent:** ärlig och identifierande, default
  `kt-rss-bridge/<version> (+self-hosted feed bridge)`
- **Conditional requests:** spara/skicka `ETag`/`Last-Modified` om API:et
  stödjer det; hantera `304`.
- **Timeout & retries:** 15 s timeout, max 2 retries, exponentiell backoff,
  ge upp tyst (logga) hellre än att hamra.
- **Backoff vid fel:** vid upprepade `429`/`5xx`, pausa en runda.
- **Endast GET.**

---

## 4. Sektioner

Sektioner härleds **från datan** (`section_tag`), inte från olika URL:er.
Allt hämtas via ETT API-anrop; sektionsfiltrering sker lokalt i databasen.

Per-sektion-feeds exponeras per `section_tag`. Lista byggs datadrivet (inte
hårdkodas). Observerade värden: `nyhet`, `debatt`, `kultur`, `teologi`,
`kronika`, `ledare`, `minnesord`, `församlingsliv`.

`podcast` är ingen egen `section_tag` — poddartiklar ligger under `nyhet` med
`kicker: "Podd"`. Ingen särskild podcast-hantering i v1.

`KT_RSS_SECTION_ALLOWLIST` (env, default tom = alla) kan begränsa vilka
sektioner som lagras/exponeras.

---

## 5. Teknisk stack

Python 3.12+, FastAPI + uvicorn, APScheduler, httpx, feedgen, SQLite (stdlib
`sqlite3` eller SQLModel), pydantic-settings, structlog/stdlib-logging,
pytest. **Ingen HTML-parser.** Minimala dependencies.

---

## 6. Datamodell (SQLite)

### Tabell `articles`
| Kolumn         | Typ      | Not                                                      |
|----------------|----------|----------------------------------------------------------|
| `id`           | TEXT PK  | API:ets `id` som text                                    |
| `url`          | TEXT     | `urljoin(KT_RSS_BASE_URL, published_url)`                 |
| `title`        | TEXT     | `title`                                                  |
| `subtitle`     | TEXT     | `subtitle` (kan vara tom)                                |
| `section`      | TEXT     | `section_tag`                                            |
| `kicker`       | TEXT     | `kicker`                                                 |
| `author`       | TEXT     | `byline_names`                                           |
| `published_at` | TEXT     | `published` (ISO 8601 oförändrad)                        |
| `modified_at`  | TEXT     | ISO 8601 UTC härledd från `modified` om satt             |
| `is_paywalled` | INTEGER  | 1 om `paywall=="1"` eller `isInternalPaywall=="1"`       |
| `tags`         | TEXT     | Tvättade ämnestaggar ur `tags`, `', '`-joinade (se nedan)|
| `image_id`     | TEXT     | Primärt bild-id (`image`), tomt om artikeln saknar bild  |
| `first_seen`   | TEXT     | ISO 8601 UTC, första gången sedd                         |
| `last_seen`    | TEXT     | ISO 8601 UTC, senaste runda sedd                         |

### Tabell `fetch_state` (en rad, `key='default'`)
`key` PK, `etag`, `last_modified`, `last_run_at`, `last_count`,
`last_status` (`ok`/`skipped_304`/`error`/`sanity_failed`), `total_count`.

### FTS5-index `articles_fts`
Virtuell FTS5-tabell (external content mot `articles`) som indexerar
`title`, `subtitle`, `tags`, `author` för `/search`. Triggrar på `articles`
håller indexet synkat; `init_db` kör `'rebuild'` vid start.

**Dedup:** `id` är PK. Återkommande artikel uppdaterar `last_seen` (och
ändrade fält som `modified_at`/`title`/`subtitle`/`tags`/`image_id`);
`first_seen` rörs aldrig. URL byggs en gång vid insert; strippa ev.
tracking-parametrar defensivt.

**Taggar:** `tags` lagras tvättat - API:ets kommaseparerade sträng med
markeringen `out` och taggar identiska med `section_tag` borttagna,
dubbletter strukna, gemener, `', '`-joinad. Tom om inget blir kvar. Det
styleade gränssnittet visar dem som klickbara pills (`/t/{tag}`).

---

## 7. Upphovsrätt / innehållspolicy (medvetet designbeslut)

API:et returnerar full `bodytext` även för betalartiklar
(`isInternalPaywall: "1"`). Den utnyttjas ALDRIG:

- Feeds och HTML visar `title`, `subtitle`, länk och artikelbild - aldrig
  `bodytext`. Bilden ligger som enclosure i feeds (v2, §2.2) och som
  `loading="lazy"`-thumbnail i webui.
- `bodytext` lagras INTE och återpubliceras aldrig.
- Kommentera detta tydligt i koden så det inte "optimeras bort" senare.

---

## 8. Poller-logik (per pollningsrunda)

1. Bygg anrop: `GET {API}/article?limit={MAX_FETCH}&start={start}&query=%25%20AND%20lab_site_id:(2)&altText=1&orderBy=published`
   (`start` enligt §10-fynd; URL-enkoda query korrekt: `%`→`%25`, mellanslag,
   `:` etc.).
2. Skicka conditional headers från `fetch_state` om de finns.
3. `304` → uppdatera `last_run_at`, `last_status=skipped_304`, klart.
4. Parsa JSON; läs `totalCount` + `result`.
5. **Sanity-check:** `result` tom/saknas, eller `< last_count*0.4`, eller
   oparsbar JSON → WARNING, `last_status=sanity_failed`, **skriv inget**, rör
   inte befintliga artiklar, avsluta rundan.
6. Filtrera: behåll `status=="P"` och `visibility_status=="P"`; om allowlist
   satt, behåll bara de `section_tag`.
7. Per artikel (nyckel `str(id)`):
   - Okänd → INSERT, `first_seen=last_seen=now()`.
   - Känd → uppdatera `last_seen`; om `modified`/`title`/`subtitle`/`tags`/
     `image_id` ändrats, uppdatera dessa. `first_seen` orörd.
8. Uppdatera `fetch_state` (etag, last_modified, last_run_at, last_count,
   total_count, `last_status=ok`).

Pollern får **aldrig** krascha schemaläggaren (try/except runt hela rundan,
logga stacktrace, fortsätt nästa körning). Kör en poll vid uppstart (kort
delay) så feeden inte är tom, sedan enligt schema.

### 8.1 Valfri manuell engångs-backfill (ej default)
CLI `python -m kt_rss.backfill --pages N --delay S` som paginerar bakåt via
offset (`start += limit`) tills `start >= totalCount` eller `--pages`-gränsen
nås — samma terminering som webbklientens `canPage` (§2.3). `nextPageToken`
används endast som fallback om offset-pagineringen visar sig bete sig oväntat.
Default AV, långsam (`delay` ≥ 3 s mellan sidor), avbrytbar/återupptagbar
(spara senaste klarade `start` så körningen kan fortsätta). Bekvämlighets-
verktyg, ej del av löpande drift.

**Uppstarts-backfill:** env `KT_RSS_BACKFILL_PAGES` (positivt sidantal, eller
`-1` för hela arkivet) kör samma backfill i en daemon-tråd vid containerstart
- blockerar inte appstart eller `/healthz`.
När hela arkivet är genomgånget skapas markörfilen `{db_path}.backfill-done`
som hindrar omkörning vid varje omstart; ta bort den för att köra om.

---

## 9. HTTP-endpoints (FastAPI)

| Metod | Path                  | Beskrivning                                       |
|-------|-----------------------|---------------------------------------------------|
| GET   | `/feed.xml`           | Atom, alla sektioner, senaste `MAX_ITEMS`         |
| GET   | `/feed/{section}.xml` | Atom filtrerad på `section_tag`                   |
| GET   | `/feed/t/{tag}.xml`   | Atom filtrerad på en tvättad tagg                 |
| GET   | `/feed/a/{author}.xml`| Atom filtrerad på skribent                        |
| GET   | `/feed/tags.xml`      | Atom på flera taggar (`?t=a,b&mode=or/and`)       |
| GET   | `/feeds.opml`         | OPML med alla sektionsfeeds                       |
| GET   | `/healthz`            | JSON: status, antal, senaste lyckad poll          |
| GET   | `/`                   | HTML-startsida: sektionskort + feed-länkar        |
| GET   | `/articles`           | HTML-lista, alla artiklar                         |
| GET   | `/s/{section}`        | HTML-lista filtrerad på `section_tag`             |
| GET   | `/t`                  | HTML: taggöversikt (taggmoln)                     |
| GET   | `/t/{tag}`            | HTML-lista filtrerad på en tvättad tagg           |
| GET   | `/a`                  | HTML: skribentöversikt (sökbar lista)             |
| GET   | `/a/{author}`         | HTML-lista filtrerad på skribent                  |
| GET   | `/tags`               | HTML: bygg en feed på flera taggar                |
| GET   | `/search`             | HTML: FTS5-sök på titel/ingress/taggar (`?q=`)    |

- Atom (RSS 2.0 via `?fmt=rss`). `Content-Type:
  application/atom+xml; charset=utf-8`.
- Item: `title`=`title`; `id`/`guid`/`link`=absolut URL;
  `updated`/`published`=`published_at`; `summary`=`subtitle` (tom → utelämna
  eller kicker); `author`=`author` om satt.
- Enclosure: artikelbilden (§2.2) som `<enclosure>` (RSS) / `<link
  rel="enclosure">` (Atom) när `image_id` finns och
  `KT_RSS_INCLUDE_IMAGE_ENCLOSURE` är på.
- Sortering `published_at` fallande, max `KT_RSS_MAX_ITEMS`.
- Feed-`updated` = max `last_seen`/`published_at` i urvalet.
- HTML-listorna (`/articles`, `/s/`, `/t/`) paginerar `KT_RSS_PAGE_SIZE`
  artiklar per `?page=N`; webui laddar nästa sida via infinite scroll
  (`?partial=1` ger ett HTML-fragment utan sidram).
- Okänd `{section}` → 404 + lista giltiga sektioner.
- `Cache-Control: public, max-age=600` på feeds.
- `/healthz` → 200 om appen lever; `last_successful_poll`, `article_count`,
  `total_count_remote`, `last_status`, `sections` (per-sektion antal).

---

## 10. Bootstrap-steg INNAN pollern kodas (FÖRST)

`python -m kt_rss.inspect` gör ett fåtal hövliga live-anrop (rätt UA/headers,
≥3 s mellan) och fastställer:
- Fungerar `start=0`? Minsta fungerande `start`? Behövs `start` för sida 1?
- Är `orderBy=published` nyaste-först? (jämför `published` först vs sist)
- Krävs `nextPageToken` för paginering, eller räcker `start`/`limit`?
- Skickar API:et `ETag`/`Last-Modified`?
- **Bild-API:** verifiera vilken parameteruppsättning
  `https://image.kyrkanstidning.se/{id}.webp` accepterar — `x/y/cropw/croph`
  (som i det verifierade exemplet) och/eller `pano*/height*` (som i
  `frontCropUrl`). Gör 1–2 HEAD/GET mot ett känt bild-id och notera vilka
  som ger `200` + bildinnehåll. Detta påverkar inte v1-feeden men låser
  bild-URL-kontraktet för v2.
- Spara 1–2 råa JSON-svar (artikel-API) till `tests/fixtures/`. Spara även
  bild-API-fynden som en kort kommentar/markdown i fixtures-mappen.

Skriv fynden som konstanter/kommentarer i `config.py`/`poller.py`. Tester körs
mot fixtures (offline, inget nät i CI).

### 10.1 Eskalering: be användaren observera i webbläsaren

Utvecklingsmiljön kan sakna nätåtkomst till `kyrkanstidning.se` /
`api.kyrkanstidning.se` / `image.kyrkanstidning.se`, eller så kan
`inspect.py` ge tvetydiga svar (t.ex. oklart hur `start`/`canPage`/
`nextPageToken` faktiskt beter sig, eller vilka exakta headers webbklienten
skickar). **Om en bootstrap-fråga inte säkert kan besvaras med ett par
direkta anrop ska Claude Code INTE gissa.** Pausa istället och be användaren
köra **Claude for Chrome** på den riktiga sidan för att observera live.

Formulera då en kort, konkret observationsbegäran till användaren, t.ex.:
- "Öppna `https://www.kyrkanstidning.se`, scrolla till en sektion och klicka
  'hämta fler' några gånger med DevTools → Network öppet. Klistra in:
  (a) den fullständiga request-URL:en för varje `api.kyrkanstidning.se/article`-
  anrop (särskilt hur `start` ökar), (b) request-headers, (c) svarets
  `totalCount` och om `nextPageToken` ändras mellan anropen."
- "Högerklicka på en artikelbild → 'Öppna bild i ny flik' och klistra in den
  faktiska bild-URL:en, så vi ser vilka cropparametrar webbplatsen verkligen
  använder."

Använd svaret för att låsa kontraktet, skriv ned det som
konstanter/kommentarer, och spara ev. inklistrade JSON-svar som fixtures.
Gör detta hellre än att implementera mot en antagen pagineringsmodell.

---

## 11. Konfiguration (env, pydantic-settings)

| Variabel                  | Default                                  | Not                          |
|---------------------------|------------------------------------------|------------------------------|
| `KT_RSS_DB_PATH`          | `/data/kt.sqlite3`                        | Volym i Docker               |
| `KT_RSS_POLL_MINUTES`     | `15`                                      | Min 15                       |
| `KT_RSS_API_URL`          | `https://api.kyrkanstidning.se/article`   |                              |
| `KT_RSS_BASE_URL`         | `https://www.kyrkanstidning.se`           | Absoluta artikel-URL:er      |
| `KT_RSS_PUBLIC_URL`       | `http://localhost:8000`                   | `<link>` i feeds             |
| `KT_RSS_MAX_FETCH`        | `50`                                      | Artiklar per poll            |
| `KT_RSS_MAX_ITEMS`        | `50`                                      | Items per feed               |
| `KT_RSS_PAGE_SIZE`        | `50`                                      | Artiklar per webui-sida      |
| `KT_RSS_SECTION_ALLOWLIST`| `` (tom = alla)                           | Komma-sep `section_tag`      |
| `KT_RSS_INCLUDE_IMAGE_ENCLOSURE` | `true`                             | Artikelbild som feed-enclosure |
| `KT_RSS_BACKFILL_PAGES`   | `0`                                       | Backfill vid start (0 av, -1 allt) |
| `KT_RSS_LOG_LEVEL`        | `INFO`                                    |                              |

---

## 12. Projektstruktur (förslag)

```
kt-rss/
├── pyproject.toml
├── README.md
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── kt_rss/
│   ├── __init__.py
│   ├── config.py          # pydantic-settings
│   ├── db.py              # schema, CRUD, dedup
│   ├── api_client.py      # httpx, query-bygge, conditional requests, UA
│   ├── inspect.py         # bootstrap-utredning (§10)
│   ├── poller.py          # rund-logik, sanity, filtrering
│   ├── backfill.py        # valfri manuell backfill (§8.1)
│   ├── feed.py            # feedgen-serialisering
│   ├── scheduler.py       # APScheduler
│   └── main.py            # FastAPI app, endpoints, lifespan
└── tests/
    ├── conftest.py
    ├── fixtures/          # sparade råa JSON-svar
    ├── test_url_build.py
    ├── test_mapping.py
    ├── test_dedup.py
    ├── test_sanity.py
    └── test_feed.py
```

---

## 13. Docker / drift

**Dockerfile:** slim Python 3.12, icke-root, `EXPOSE 8000`, default
`uvicorn kt_rss.main:app --host 0.0.0.0 --port 8000`.

**docker-compose.yml:** service `kt-rss`, volym `/data` (SQLite persistens),
healthcheck mot `/healthz`, `restart: unless-stopped`, env via `.env`.

**Två lägen, samma image:** testdeploy = `docker compose up` på dev-VM; hemma =
samma image i homelab-stack, reverse-proxas externt (Cloudflare Tunnel). Appen
hanterar inte TLS; bygg publika länkar från `KT_RSS_PUBLIC_URL`.

---

## 14. Acceptanskriterier

- [ ] `inspect` fastställer pagineringssemantiken, sparar fixtures.
- [ ] `pytest` grönt; mapping/dedup/sanity/feed offline mot fixtures.
- [ ] `docker compose up` → `/healthz` 200 med vettig JSON (inkl.
      `total_count_remote`).
- [ ] Efter första poll: `/feed.xml` valida Atom-items.
- [ ] `/feed/debatt.xml` bara `section_tag=debatt`; okänd sektion → 404 + lista.
- [ ] Dedup: samma `id` två pollningar → ett item; `last_seen` uppdat.,
      `first_seen` ej.
- [ ] Ändrad `title`/`subtitle`/`modified` → uppdateras i DB.
- [ ] Feed-items innehåller ALDRIG `bodytext` (test).
- [ ] Endast `status=P` + `visibility_status=P` lagras.
- [ ] Sanity: tomt `result` → `sanity_failed`, inget raderas/blankas.
- [ ] Pollningsfel kan inte döda schemaläggaren (injicerat exception-test).
- [ ] SQLite överlever container-omstart.
- [ ] README: dev-VM-flöde, env, hemma-deploy, backfill + hövlighetskrav.

---

## 15. Utanför scope (v1)

Ingen `bodytext`-återpublicering (§7). Ingen automatisk arkiv-backfill (manuellt
CLI, default av). Ingen auth (bakom egen proxy). Ingen frontend utöver enkel
index. Ingen HTML-scraping. Ingen push/notiser.

---

## 16. Arbetsordning för Claude Code

1. Läs hela specen. Ställ frågor om något är oklart.
2. Projektstruktur, `pyproject.toml`, `.env.example`, `config.py`.
3. `inspect.py` → kör mot live-API, fastställ §2.3, spara fixtures. **Om
   nätet saknas eller svaren är tvetydiga: gissa inte — be användaren köra
   Claude for Chrome på sidan och observera enligt §10.1.**
4. `api_client.py`: query-bygge (korrekt `%`-enkodning), headers, UA,
   conditional requests, timeout/retry/backoff.
5. `db.py`: schema + mapping + dedup + tester mot fixtures.
6. `poller.py`: rund-logik, filtrering, sanity, `modified`-uppdatering + tester.
7. `feed.py` + `main.py` + `scheduler.py`/lifespan.
8. `backfill.py` (valfritt, hövligt).
9. Dockerfile + compose + README.
10. Kör acceptanskriterierna, lämna grönt.

---

# BILAGA A — Fullständig fältreferens (endast dokumentation)

> **Denna bilaga är ren referensdokumentation, inte ett krav.** Den används
> INTE för att styra implementationen — §6 (datamodell) och §2 (relevanta
> fält) är det som gäller för bygget. Bilagan finns för att förstå API:ets
> fullständiga svarsform, underlätta felsökning och möjliggöra framtida v2-
> beslut. Fälten nedan är observerade i ett verkligt svar från
> `GET https://api.kyrkanstidning.se/article?...&query=% AND lab_site_id:(2)`.
> Typer och förekomst är empiriska — fält kan saknas på enskilda artiklar,
> och typer (sträng vs heltal) är inte garanterade. Behandla allt defensivt.

## A.1 Svarets toppnivå

| Fält            | Typ     | Beskrivning                                                   |
|-----------------|---------|---------------------------------------------------------------|
| `totalCount`    | heltal  | Totalt antal träffar för query (t.ex. `32735`). Styr paginering. |
| `result`        | array   | Lista av artikelobjekt (max `limit` st).                      |
| `nextPageToken` | sträng  | Ogenomskinlig pagineringstoken. Sannolikt valfri vid offset-paginering (se §2.3). |

## A.2 Identitet, status och URL

| Fält                  | Typ            | Beskrivning                                                                 |
|-----------------------|----------------|-----------------------------------------------------------------------------|
| `id`                  | sträng/heltal  | Artikelns unika id (t.ex. `"433146"`). **Primärnyckel för dedup.**           |
| `type`                | sträng         | Innehållstyp, observerat alltid `"article"`.                                |
| `status`              | sträng         | Publiceringsstatus. `"P"` = publicerad. **Filtrera på detta.**               |
| `visibility_status`   | sträng         | Synlighet. `"P"` = synlig. **Filtrera på detta.**                            |
| `is_publishedhidden`  | sträng         | `"0"`/`"1"` — dold trots publicerad. `"1"` bör uteslutas.                    |
| `publishhidden`       | sträng         | `"0"`/`"1"` — relaterad dold-flagga.                                         |
| `hidefromapp`         | sträng         | `""`/`"1"` — dölj i app-kontext.                                             |
| `site_id`             | sträng         | Sajt-id, `"2"` = Kyrkans Tidning.                                            |
| `lab_site_id`         | sträng         | Sajt-id i lab-systemet, `"2"`. Används i `query`-filtret.                    |
| `siteDomain`          | sträng         | Domän, t.ex. `https://www.kyrkanstidning.se` (kan innehålla markdown-skräp i svaret — sanera). |
| `published_url`       | sträng         | **Aktuell** relativ artikel-URL, t.ex. `/teologi/.../433146`. Bygg absolut URL från denna. |
| `published_urls_json` | sträng (JSON)  | JSON-array med historiska/alternativa URL:er (ofta med `null` först). Endast för felsökning. |
| `page_template_alias` | sträng         | Sidmall, ofta `"default"`.                                                  |

## A.3 Rubriker och text

| Fält                    | Typ            | Beskrivning                                                              |
|-------------------------|----------------|--------------------------------------------------------------------------|
| `title`                 | sträng         | Rubrik. **Används i feed.**                                              |
| `subtitle`              | sträng         | Underrubrik/ingress. **Används som feed-summary.** Kan vara `""`.        |
| `teaserTitle`           | sträng         | Alternativ rubrik för puffar. Ofta `""`.                                 |
| `teaserSubtitle`        | sträng         | Alternativ underrubrik för puffar. Ofta `""`.                            |
| `summary_short_title`   | sträng         | Kort summeringsrubrik. Ofta `""`.                                        |
| `summary_short_bodytext`| sträng         | Kort summeringstext. Ofta `""`.                                          |
| `bodytext`              | sträng         | Brödtext. **Innebörd varierar per artikeltyp — se §2.1.** Återpubliceras ej i v1 (§7). |
| `kicker`                | sträng         | Etikett ovanför rubrik: `Nyhet`, `Debatt`, `Krönika`, `Podd`, `Recension`, `Inför söndagen`, `Gästledare`, `Minnesord`, `Kyrkfolk`, `Tre frågor till` m.fl. |
| `allowRichTextTeasers`  | sträng         | `"1"` om rich text-puffar tillåts. Sällan satt.                          |

## A.4 Sektion och taggning

| Fält          | Typ           | Beskrivning                                                                      |
|---------------|---------------|----------------------------------------------------------------------------------|
| `section_tag` | sträng        | **Primär sektion.** Observerade: `nyhet`, `debatt`, `kultur`, `teologi`, `kronika`, `ledare`, `församlingsliv`. Notera: `minnesord` förekommer som `kicker` men sektionen är då ofta `församlingsliv`. |
| `tags`        | sträng        | Kommaseparerade ämnestaggar, t.ex. `"israel, nyhet, out, usa"`. Taggen `out` verkar vara intern markering. |
| `term`        | objekt        | Oftast tomt `{}`. Reserverat taxonomifält.                                       |
| `mainterm`    | objekt        | Oftast tomt `{}`. Reserverat huvudterm-fält.                                      |

## A.5 Datum och tid

| Fält                      | Typ           | Beskrivning                                                                |
|---------------------------|---------------|----------------------------------------------------------------------------|
| `published`               | sträng        | **Publiceringstid, ISO 8601 med tidszon** (`2026-04-27T11:00:32+02:00`). Feed-datum. |
| `created`                 | sträng        | Skapad-tid, ISO 8601 med tidszon.                                          |
| `modified`                | sträng        | **Senast ändrad, Unix-timestamp i sekunder** (t.ex. `"1777272193"`). Kan saknas. För uppdateringsdetektion. |
| `print_edition_date`      | sträng        | Datum för papperstidningen, `YYYY-MM-DD`. Kan vara framtida/avvikande.      |
| `print_exported`          | sträng        | Unix-timestamp för print-export.                                           |

## A.6 Författare (byline)

| Fält             | Typ          | Beskrivning                                                                       |
|------------------|--------------|-----------------------------------------------------------------------------------|
| `byline_names`   | sträng       | **Författarnamn som visningssträng**, t.ex. `"Jonatan Sverker"` eller flera kommaseparerade. Används som feed-author. |
| `byline_ids`     | array<int>   | Id:n för byline-poster.                                                            |
| `full_bylines`   | array<obj>   | Detaljerade byline-objekt. Vanliga undernycklar: `firstname`, `lastname`, `description` (roll, t.ex. "biskop växjö stift"), `public_email`, `public_phone`, `public_url`, `id`, `viewports_json`, `created_by`, `modified_by`. Fälten varierar och kan saknas. |
| `created_by`     | sträng       | Internt redaktörsnamn (efternamn, förnamn) — INTE artikelförfattare.               |
| `created_by_name`| sträng       | Internt redaktörsnamn. För felsökning, ej publik.                                  |

## A.7 Bild och beskärning (se §2.2 för bild-API)

| Fält                  | Typ           | Beskrivning                                                                    |
|-----------------------|---------------|--------------------------------------------------------------------------------|
| `image`               | sträng        | **Primärt bild-id** (t.ex. `"433193"`). Indata till bild-API:et.               |
| `crop`                | objekt        | Beskärningsdata. Innehåller delobjekten `pano` och `height`.                   |
| `crop.pano`           | objekt        | Panorama-crop: `x`, `y`, `cropw`, `croph` (procent som strängar) + `metadata_key` (`"fcp"`). |
| `crop.height`         | objekt        | Höjd-crop: `x`, `y`, `cropw`, `croph` + `metadata_key` (`"fch"`).              |
| `frontCropUrl`        | sträng        | **Färdig query-sträng** för bild-API med `pano*`/`height*`-prefix, t.ex. `?imageId=433193&panoh=100&...`. |
| `imageCaption`        | sträng        | Bildtext.                                                                      |
| `altText`             | sträng        | Alt-text (returneras när `altText=1` skickas). Kan saknas.                     |
| `image_count`         | heltal        | Antal bilder kopplade till artikeln.                                           |
| `used_image_ids_json` | sträng (JSON) | JSON-array med använda bild-id:n.                                               |
| `uploaded_images_json`| sträng (JSON) | JSON-array `[{imageId, timestamp}, …]` — uppladdade bilder med tidsstämpel.    |

## A.8 Paywall och prenumeration

| Fält                    | Typ      | Beskrivning                                                                  |
|-------------------------|----------|------------------------------------------------------------------------------|
| `paywall`               | sträng   | `"1"` = bakom betalvägg. Kan vara `""`/saknas för fritt innehåll.            |
| `isInternalPaywall`     | sträng   | `"1"` = intern betalväggsmarkering. **Tillsammans med `paywall` styr §7-policy.** |
| `requiressubscription`  | sträng   | `""`/`"1"` — kräver prenumeration.                                            |
| `isSpesial`             | sträng   | `""`/`"1"` — specialinnehåll (notera stavningen i API:et).                   |
| `isContentMarketing`    | sträng   | `""`/`"1"` — innehållsmarknadsföring/annonsmaterial. Bör ev. exkluderas.     |
| `contentMarketingPublisher` | sträng | Utgivare av content marketing om sådant. Oftast `""`.                       |
| `noneditorial`          | sträng   | `""`/`"1"` — icke-redaktionellt innehåll.                                    |

## A.9 Statistik och läsmetrik

| Fält               | Typ     | Beskrivning                                                                |
|--------------------|---------|----------------------------------------------------------------------------|
| `readTime`         | sträng  | Lästid i minuter (kan vara `"0.5"`, `"1"`, `"2"` …).                        |
| `stats_read_time`  | sträng  | Lästid som text, t.ex. `"2minutes"`.                                        |
| `stats_word_count` | sträng  | Antal ord. Användbar för att bedöma om `bodytext` ser komplett ut (§2.1).   |
| `stats_char_count` | sträng  | Antal tecken. Samma användning.                                            |
| `stats_lix`        | sträng  | LIX-läsbarhetsindex.                                                        |
| `showonfp`         | sträng  | `"1"` = visa på förstasidan.                                                |
| `showcomments`     | sträng  | `"1"` = kommentarer tillåtna.                                              |

## A.10 Redaktionellt arbetsflöde (internt — endast felsökning)

> Dessa fält speglar KT:s interna produktionssystem ("lab"/Roxen). De är INTE
> relevanta för feeden men dokumenteras för fullständighet och felsökning.

| Fält                          | Typ           | Beskrivning                                                       |
|-------------------------------|---------------|-------------------------------------------------------------------|
| `lab_approved`                | sträng        | `"1"` = godkänd i lab-systemet.                                    |
| `lab_approved_json`           | sträng (JSON) | `{user:{id,name}, date:{timestamp}}` — vem/när godkänt.           |
| `lab_sentToDistribution`      | sträng        | `"1"` = skickad till distribution.                                |
| `lab_sentToDistribution_json` | sträng (JSON) | Vem/när skickad till distribution.                                |
| `has_published`               | sträng        | Mellanslagsseparerade redaktörs-id:n som publicerat.              |
| `last_published_by`           | array         | Lista `[[userId, timestamp, kod], …]` — publiceringshistorik.    |
| `lockSessionId`               | sträng        | Redigeringslås-session (UUID).                                    |
| `lock`                        | sträng        | Låsstatus, ofta `""`.                                             |
| `roxen_export_history_json`   | sträng (JSON) | Exporthistorik mot Roxen/print-systemet.                          |
| `print_publication`           | sträng        | Publikationskod, t.ex. `"KT"`.                                    |
| `print_id`                    | sträng        | Print-id, t.ex. `"stories:1489"`.                                 |

## A.11 Presentation och styling (internt — ej för feed)

| Fält                  | Typ           | Beskrivning                                                            |
|-----------------------|---------------|------------------------------------------------------------------------|
| `viewports_json`      | sträng (JSON) | Responsiva stilinställningar per viewport (titelstorlek m.m.).         |
| `title_style_json`    | sträng (JSON) | Stil för rubrik (`fontface`, `font_weight`, `text_size`).             |
| `subtitle_style_json` | sträng (JSON) | Stil för underrubrik.                                                  |
| `kicker_style_json`   | sträng (JSON) | Stil för kicker.                                                       |

## A.12 Fältkategorier — sammanfattning för implementationen

**Använd i v1 (mappas till `articles`, se §6):**
`id`, `published`, `modified`, `title`, `subtitle`, `published_url`,
`section_tag`, `kicker`, `byline_names`, `paywall`, `isInternalPaywall`,
`status`, `visibility_status`.

**Använd för filtrering/sanity:**
`status`, `visibility_status`, `is_publishedhidden`, `isContentMarketing`
(överväg exkludering), `stats_char_count` (heuristik, ej v1).

**Dokumenterat för v2 (bild-enclosure, §2.2):**
`image`, `crop`, `frontCropUrl`, `imageCaption`, `altText`.

**Ignoreras helt (internt arbetsflöde/styling):**
Allt i A.10 och A.11, samt `created_by*`, `lab_*`, `print_*`, `*_style_json`,
`viewports_json`, `lockSessionId`, `term`/`mainterm` (tomma).

> Slut på Bilaga A. Allt ovanför "BILAGA A" är den styrande specifikationen;
> bilagan är endast referens.
