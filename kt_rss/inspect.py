"""Bootstrap-utredning (spec SS10).

Kör: `python -m kt_rss.inspect`

Gör ett fåtal hövliga live-anrop (>=3 s mellan, rätt UA/headers) för att
fastställa det som specen SS2.3 lämnar öppet INNAN pollern kodas:

  - Fungerar start=0? Behövs start för första (nyaste) sidan?
  - Är orderBy=published nyaste-först?
  - Skickar nextPageToken med - behövs den?
  - Skickar API:et ETag/Last-Modified?
  - Bild-API: vilken parameteruppsättning ger 200 + bild?

Sparar råsvar i tests/fixtures/ och skriver fynden till stdout. Konstanterna
i config.py (FIRST_PAGE_START, CONDITIONAL_REQUEST_SUPPORT) fylls i för hand
utifrån utskriften - inspect.py är avsiktligt en engångs-utredare, inte en
del av driften.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from kt_rss.config import IMAGE_API_BASE, get_settings

POLITE_DELAY = 3.0  # sekunder mellan live-anrop
QUERY = "% AND lab_site_id:(2)"
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def _params(limit: int, start: int | None) -> dict[str, str]:
    p: dict[str, str] = {
        "limit": str(limit),
        "query": QUERY,
        "altText": "1",
        "orderBy": "published",
    }
    if start is not None:
        p["start"] = str(start)
    return p


def _fetch(client: httpx.Client, url: str, label: str, **kw) -> httpx.Response | None:
    print(f"\n-> {label}")
    try:
        r = client.get(url, **kw)
    except httpx.HTTPError as exc:
        print(f"   FEL: {exc!r}")
        return None
    print(f"   HTTP {r.status_code}  ({r.elapsed.total_seconds():.2f}s)  {r.url}")
    return r


def _summarise(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        print("   svaret är inte JSON")
        return {}
    result = data.get("result") or []
    info = {
        "totalCount": data.get("totalCount"),
        "nextPageToken": data.get("nextPageToken"),
        "result_len": len(result),
        "etag": r.headers.get("ETag"),
        "last_modified": r.headers.get("Last-Modified"),
    }
    if result:
        info["first_published"] = result[0].get("published")
        info["last_published"] = result[-1].get("published")
        info["first_id"] = result[0].get("id")
        info["last_id"] = result[-1].get("id")
    print(
        f"   totalCount={info['totalCount']}  result={info['result_len']}  "
        f"nextPageToken={'ja' if info['nextPageToken'] else 'nej'}"
    )
    print(f"   ETag={info['etag']}  Last-Modified={info['last_modified']}")
    if result:
        print(
            f"   nyaste id={info['first_id']} ({info['first_published']})  "
            f"äldsta id={info['last_id']} ({info['last_published']})"
        )
    return info


def _probe_image_api(client: httpx.Client, article: dict) -> None:
    """Verifierar vilken bild-API-parameteruppsättning som accepteras (SS2.2)."""
    image_id = article.get("image")
    crop = article.get("crop") or {}
    front_crop_url = article.get("frontCropUrl") or ""
    if not image_id:
        print("\n(hoppar över bild-API: artikeln saknar 'image')")
        return

    print(f"\n=== BILD-API (imageId={image_id}) ===")
    findings: list[str] = []

    # Variant 1: x/y/cropw/croph + height* (det verifierade exemplet i specen).
    pano = crop.get("pano") or {}
    height = crop.get("height") or {}
    xyc_params = {
        "imageId": str(image_id),
        "x": pano.get("x", "0"),
        "y": pano.get("y", "0"),
        "cropw": pano.get("cropw", "100"),
        "croph": pano.get("croph", "100"),
        "heightx": height.get("x", "0"),
        "heighty": height.get("y", "0"),
        "heightw": height.get("cropw", "100"),
        "heighth": height.get("croph", "100"),
        "format": "webp",
    }
    url1 = f"{IMAGE_API_BASE}/{image_id}.webp"
    r1 = _fetch(client, url1, "bild-API variant x/y/cropw/croph", params=xyc_params)
    if r1 is not None:
        ok1 = r1.status_code == 200 and r1.headers.get("Content-Type", "").startswith("image")
        findings.append(f"- variant `x/y/cropw/croph`: HTTP {r1.status_code}, "
                         f"Content-Type `{r1.headers.get('Content-Type')}` -> "
                         f"{'OK' if ok1 else 'EJ OK'}")
    time.sleep(POLITE_DELAY)

    # Variant 2: frontCropUrl ordagrant (pano*/height*-prefix).
    if front_crop_url:
        url2 = f"{IMAGE_API_BASE}/{image_id}.webp{front_crop_url}"
        r2 = _fetch(client, url2, "bild-API variant frontCropUrl (pano*/height*)")
        if r2 is not None:
            ok2 = r2.status_code == 200 and r2.headers.get("Content-Type", "").startswith("image")
            findings.append(f"- variant `frontCropUrl` (pano*/height*): HTTP {r2.status_code}, "
                             f"Content-Type `{r2.headers.get('Content-Type')}` -> "
                             f"{'OK' if ok2 else 'EJ OK'}")
    else:
        findings.append("- variant `frontCropUrl`: artikeln saknade frontCropUrl, ej testad")

    doc = FIXTURES_DIR / "image-api-findings.md"
    doc.write_text(
        "# Bild-API-fynd (kt_rss.inspect, SS2.2/SS10)\n\n"
        f"Testat mot imageId `{image_id}` ({IMAGE_API_BASE}/{image_id}.webp).\n\n"
        + "\n".join(findings)
        + "\n\nv1-feeden inkluderar inte bilder; detta låser kontraktet för v2.\n",
        encoding="utf-8",
    )
    print(f"\nBild-API-fynd sparade: {doc}")


def main() -> None:
    settings = get_settings()
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    print(f"API: {settings.api_url}")
    print(f"User-Agent: {settings.user_agent}")

    with httpx.Client(
        headers=settings.request_headers, timeout=15.0, follow_redirects=True
    ) as client:
        # 1. Nyaste sidan utan start - blir även huvudfixturen.
        r_nostart = _fetch(
            client, settings.api_url, "nyaste sidan (utan start)",
            params=_params(limit=25, start=None),
        )
        nostart = _summarise(r_nostart) if r_nostart is not None else {}
        if r_nostart is not None and r_nostart.status_code == 200:
            fx = FIXTURES_DIR / "article_response.json"
            fx.write_text(
                json.dumps(r_nostart.json(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"   fixture sparad: {fx}")
        time.sleep(POLITE_DELAY)

        # 2. start=0.
        r_start0 = _fetch(
            client, settings.api_url, "start=0",
            params=_params(limit=5, start=0),
        )
        start0 = _summarise(r_start0) if r_start0 is not None else {}
        time.sleep(POLITE_DELAY)

        # 3. start=100.
        r_start100 = _fetch(
            client, settings.api_url, "start=100",
            params=_params(limit=5, start=100),
        )
        start100 = _summarise(r_start100) if r_start100 is not None else {}
        time.sleep(POLITE_DELAY)

        # 4. Conditional request mot nyaste sidan.
        cond_supported = False
        etag = nostart.get("etag")
        last_mod = nostart.get("last_modified")
        if etag or last_mod:
            cond_headers = {}
            if etag:
                cond_headers["If-None-Match"] = etag
            if last_mod:
                cond_headers["If-Modified-Since"] = last_mod
            r_cond = _fetch(
                client, settings.api_url, "conditional request (If-None-Match)",
                params=_params(limit=25, start=None), headers=cond_headers,
            )
            cond_supported = r_cond is not None and r_cond.status_code == 304
            time.sleep(POLITE_DELAY)
        else:
            print("\n-> conditional request: hoppas över (inget ETag/Last-Modified)")

        # 5. Bild-API.
        articles = (r_nostart.json().get("result") if r_nostart is not None else None) or []
        if articles:
            img_article = next(
                (a for a in articles if a.get("image") and a.get("frontCropUrl")),
                articles[0],
            )
            _probe_image_api(client, img_article)

    # --- Sammanfattning ---
    print("\n" + "=" * 64)
    print("BOOTSTRAP-FYND - skriv in i config.py")
    print("=" * 64)

    def _desc(info: dict) -> str:
        n = info.get("result_len", 0)
        return f"{n} artiklar" if n else "TOMT/inget resultat"

    print(f"utan start  : {_desc(nostart)}")
    print(f"start=0     : {_desc(start0)}")
    print(f"start=100   : {_desc(start100)}")

    first_page_start: int | None = None
    if nostart.get("result_len"):
        first_page_start = None if not start0.get("result_len") else 0
        if start0.get("result_len") and start0.get("first_id") == nostart.get("first_id"):
            first_page_start = 0
        elif not start0.get("result_len"):
            first_page_start = None
    print(f"\n=> FIRST_PAGE_START = {first_page_start!r}"
          "  (None = utelämna start-parametern för nyaste sidan)")

    # orderBy-riktning.
    fp, lp = nostart.get("first_published"), nostart.get("last_published")
    if fp and lp:
        direction = "nyaste-först (descending)" if fp >= lp else "äldsta-först (ascending)"
        print(f"=> orderBy=published: {direction}  ({fp} ... {lp})")

    print(f"=> nextPageToken: {'finns i svaret' if nostart.get('nextPageToken') else 'saknas'}"
          "  (offset-paginering räcker, spec SS2.3)")
    print(f"=> CONDITIONAL_REQUEST_SUPPORT = {cond_supported!r}"
          f"  (ETag={nostart.get('etag')}, Last-Modified={nostart.get('last_modified')})")


if __name__ == "__main__":
    main()
