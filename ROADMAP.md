# ROADMAP

## v1 (klar)

Poller, dedup, sanity, Atom/RSS-feeds totalt och per sektion, styleat
HTML-gränssnitt, `/healthz`, Docker-deploy, manuell backfill. Se
`docs/SPEC.md` för fullständig spec.

## v2

### Klart

- **Tagg-pills på artiklar.** Artiklarnas `tags` lagras tvättat (kolumn i
  `articles`) och visas som klickbara pills i HTML-listan, länkade till
  `/t/{tag}`. Varje tagg har även en egen feed (`/feed/t/{tag}.xml`).
  Se `docs/SPEC.md` §6 och §9.

### Idéer (ej påbörjat)

- **Egenbyggda feeds på flera taggar.** Låt användaren kombinera flera
  taggar till en feed (query-baserad URL, t.ex. `/feed/tags.xml?t=a,b`).
  Designval att ta: OR- kontra AND-logik mellan taggarna. "Bygg själv"-delen
  bör vara ett UI med sökruta och lazyloading av tagglistan - taggarna är
  väldigt många (hundratals) så hela listan kan inte renderas rakt av.

- **Bild-enclosure i feeds.** Bild-API:et är utrett och dokumenterat
  (`tests/fixtures/image-api-findings.md`, spec §2.2). Båda parametersetten
  (`x/y/cropw/croph` och `frontCropUrl`) ger giltig `image/webp`. En env
  `KT_RSS_INCLUDE_IMAGE_ENCLOSURE` är reserverad. Kräver en testbar
  `build_image_url(image_id, crop, fmt)`.
- **Textutdrag i feed-summary.** Om det någonsin görs: använd `subtitle`
  som primär summary. `bodytext` är osäkert fullständig och varierar per
  artikeltyp (poddar har programtext, inte transkription - spec §2.1) -
  lita aldrig på den. Brödtext återpubliceras inte (innehållspolicy §7).
- **Conditional requests.** API:et skickar i dag inga `ETag`/`Last-Modified`.
  304-grenen i pollern finns kvar defensivt om det ändras.
- **Full backfill vid containerstart.** En env-variabel (t.ex.
  `KT_RSS_BACKFILL_PAGES`) som vid uppstart triggar en backfill av arkivet.
  Måste köras som bakgrundsuppgift, inte i `lifespan` - en full backfill tar
  ~28 min och får inte blockera appstart eller `/healthz`. Behöver en "redan
  klar"-markör så den inte kör om ~328 API-anrop vid varje containeromstart;
  `backfill.py`:s resume-sidecar täcker delar av det.
- **Paginering i HTML-vyerna.** Efter en full backfill rymmer `articles`
  tiotusentals rader, men `/articles` och `/s/{section}` visar bara
  `max_items`. Kräver `offset` i `get_articles` samt sid- eller
  lazyload-navigering i `list.html`. Feeds berörs inte - de ska förbli
  senaste N (RSS-konvention).

## Utanför scope

Ingen auth (körs bakom egen proxy), ingen HTML-scraping, inga push-notiser.
