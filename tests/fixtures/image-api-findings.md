# Bild-API-fynd (kt_rss.inspect, SS2.2/SS10)

Testat mot imageId `434429` (https://image.kyrkanstidning.se/434429.webp).

- variant `x/y/cropw/croph`: HTTP 200, Content-Type `image/webp` -> OK
- variant `frontCropUrl` (pano*/height*): HTTP 200, Content-Type `image/webp` -> OK

v1-feeden inkluderar inte bilder; detta låser kontraktet för v2.
