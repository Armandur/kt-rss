# Bild-API-fynd (kt_rss.inspect, SS2.2/SS10)

Testat mot imageId `434429` (https://image.kyrkanstidning.se/434429.webp).

- variant `x/y/cropw/croph`: HTTP 200, Content-Type `image/webp` -> OK
- variant `frontCropUrl` (pano*/height*): HTTP 200, Content-Type `image/webp` -> OK

v1-feeden inkluderar inte bilder; detta låser kontraktet för v2.

## Storleksparametrar

Verifierat 2026-05-17 mot samma imageId:

- utan `width`/`height`: API:t svarar med en pytteliten rendition, **100x56 px**
  (~1 KB). Oanvändbart för en feed-enclosure.
- `&width=1200` ensam: **1200x674 px** - skalas proportionerligt, bildens
  höjd-bredd-förhållande behålls.
- `&width=1200&height=800`: **1200x800 px** - exakta mått tvingas fram,
  vilket kan beskära eller förvränga bilden.

Slutsats: ange alltid minst `width`. `build_image_url` ger feed-enclosure
`width=1200` (proportionerligt) och webui-thumbnails `width`+`height`.
