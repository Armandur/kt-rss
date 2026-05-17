# ROADMAP

## v1 (klar)

Poller, dedup, sanity, Atom/RSS-feeds totalt och per sektion, styleat
HTML-gränssnitt, `/healthz`, Docker-deploy, manuell backfill. Se
`docs/SPEC.md` för fullständig spec.

## v2 (klar)

- **Tagg-pills på artiklar.** Artiklarnas `tags` lagras tvättat (kolumn i
  `articles`) och visas som klickbara pills i HTML-listan, länkade till
  `/t/{tag}`. Varje tagg har även en egen feed (`/feed/t/{tag}.xml`).
  Se `docs/SPEC.md` §6 och §9.
- **Artikelbilder.** Bild-id lagras i `articles`. Bilden läggs som enclosure
  i feeds (`<enclosure>` / `<link rel="enclosure">`) och som
  `loading="lazy"`-thumbnail i webui-listorna. `build_image_url` i `feed.py`,
  env `KT_RSS_INCLUDE_IMAGE_ENCLOSURE`. Se `docs/SPEC.md` §2.2.

Dessutom: poll-status flyttad till headern, klockslag och dag-separatorer
i artikellistan.

## v3 (klar)

- **Mobilanpassning av webui.** På smal skärm (<560px) floatar en liten
  thumbnail uppe till vänster och texten flödar runt den; `.wrap`-padding
  och 404-sidans sektionslista justerade.
- **Paginering / infinite scroll.** Artikellistorna laddar fler artiklar
  automatiskt vid scroll - `scroll.js` hämtar nästa batch som HTML-fragment
  (`?partial=1`). `KT_RSS_PAGE_SIZE` styr batchstorleken.
- **Startsidan som snabb bläddringsvy.** Sektionskorten är hopfällbara
  (ihop på mobil) och "Senaste artiklarna" visas direkt på `/`, med samma
  infinite scroll som listvyerna.
- **Full backfill vid containerstart.** `KT_RSS_BACKFILL_PAGES` (sidantal
  eller -1 för hela arkivet) startar en daemon-tråd vid uppstart som
  backfillar utan att blockera appstart. Markörfilen `{db}.backfill-done`
  hindrar omkörning. Se `docs/SPEC.md` §8.1.
- **Egenbyggda feeds på flera taggar.** `/feed/tags.xml?t=a,b&mode=or/and`
  kombinerar taggar (OR/AND). Bygg-vyn `/tags` har en sökbar tagglista och
  genererar feed-URL:en (`feedbuilder.js`).
- **Artikelsök.** `/search?q=` söker artiklar på titel och ingress
  (`instr`-substräng, versal-okänsligt). Sökruta i startsidans hero.

## v4

### Klart

- **Mörkt/ljust tema.** Mörk palett som följer OS via `prefers-color-scheme`,
  med en manuell växlingsknapp i headern. Valet sparas i localStorage och
  appliceras FOUC-fritt av ett inline-script i `<head>`; `theme.js` sköter
  knappen, `[data-theme]` på `<html>` styr override.
- **Bläddra via taggar.** `/t` visar ett sökbart taggmoln (storlek efter
  artikelantal) att klicka sig in i `/t/{tag}` från. `tagcloud.js` sköter
  sökfiltret.
- **Bättre sök.** `/search` använder SQLite FTS5 - ordmatchning,
  relevansranking (bm25) och korrekt åäö-hantering. Indexerar titel,
  ingress, taggar och författare. (Infinite scroll på resultaten kvarstår
  som möjlig påbyggnad.)

### Idéer (ej påbörjat)

- **Conditional requests.** API:et skickar i dag inga `ETag`/`Last-Modified`.
  304-grenen i pollern finns kvar defensivt om det ändras.
- **Webbläsarnotiser.** Notis när nya artiklar dykt upp (eller annat
  relevant). Kräver Web Push: en service worker, VAPID-nycklar,
  push-prenumerationer lagrade per klient och utskick från servern.
  Förutsätter HTTPS.

## Utanför scope

Ingen auth (körs bakom egen proxy), ingen HTML-scraping.
