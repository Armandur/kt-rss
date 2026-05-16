# ROADMAP

## v1 (klar)

API-till-RSS-bro: poller, dedup, sanity, Atom/RSS-feeds totalt och per
sektion, styleat HTML-gränssnitt, `/healthz`, Docker-deploy, manuell
backfill. Se `docs/SPEC.md` för fullständig spec.

## v2 - idéer (ej påbörjat)

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

## Utanför scope

Ingen auth (körs bakom egen proxy), ingen automatisk arkiv-backfill, ingen
HTML-scraping, inga push-notiser.
