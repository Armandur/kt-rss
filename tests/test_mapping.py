"""Mapping från API-objekt till Article (spec SS6, SS7)."""

from dataclasses import fields

from kt_rss.db import Article, _clean_tags, _clean_url, _modified_to_iso, map_article

BASE = "https://www.kyrkanstidning.se"


def test_map_grundfall(raw_articles):
    a = map_article(raw_articles[0], BASE)
    assert isinstance(a.id, str) and a.id
    assert a.url.startswith("https://www.kyrkanstidning.se/")
    assert a.published_at
    assert a.is_paywalled in (0, 1)
    assert isinstance(a.title, str)


def test_article_har_aldrig_bodytext():
    # Upphovsrättspolicy SS7: bodytext får inte ens finnas i datamodellen.
    names = {f.name for f in fields(Article)}
    assert "bodytext" not in names


def test_is_paywalled_fran_bada_falten():
    assert map_article({"id": "1", "published": "p", "paywall": "1"}, BASE).is_paywalled == 1
    assert map_article({"id": "1", "published": "p", "isInternalPaywall": "1"}, BASE).is_paywalled == 1
    assert map_article({"id": "1", "published": "p"}, BASE).is_paywalled == 0


def test_modified_to_iso():
    assert _modified_to_iso(None) is None
    assert _modified_to_iso("") is None
    assert _modified_to_iso("0") is None
    assert _modified_to_iso("inte-ett-tal") is None
    iso = _modified_to_iso("1777272193")
    assert iso is not None and iso.endswith("+00:00")


def test_clean_url_strippar_query():
    u = _clean_url(BASE, "/teologi/artikel/433146?utm_source=spam&ref=x")
    assert u == "https://www.kyrkanstidning.se/teologi/artikel/433146"


def test_map_defensiv_vid_saknade_falt():
    a = map_article({}, BASE)
    assert a.id == ""
    assert a.subtitle == ""
    assert a.is_paywalled == 0
    assert a.tags == ""


def test_clean_tags_tvattar():
    # 'out' är intern API-markering; section_tag dubbleras ofta i tags.
    assert _clean_tags("nattvard, debatt, out", "debatt") == "nattvard"
    # dubbletter bort, ordning bevarad, inre whitespace kollapsad
    assert _clean_tags("a,  b  c , a", "") == "a, b c"
    # tomt/None in -> tomt ut
    assert _clean_tags("", "nyhet") == ""
    assert _clean_tags(None, "nyhet") == ""


def test_map_article_satter_tvattade_tags(raw_articles):
    # Fixturens första artikel: section_tag=debatt, tags="nattvard, debatt".
    a = map_article(raw_articles[0], BASE)
    assert a.tags == "nattvard"
