"""Query-bygge och URL-enkodning (spec SS2, SS8:1)."""

from kt_rss.api_client import build_params, build_query_string


def test_params_har_ratt_falt():
    p = build_params(50, 0)
    assert p["limit"] == "50"
    assert p["start"] == "0"
    assert p["orderBy"] == "published"
    assert p["altText"] == "1"
    assert p["query"] == "% AND lab_site_id:(2)"


def test_start_none_utelamnas():
    assert "start" not in build_params(50, None)
    # start=0 är ett giltigt värde och ska tas med
    assert build_params(50, 0)["start"] == "0"


def test_query_procent_enkodas():
    # % måste bli %25 - annars tolkar API:et query-strängen fel.
    qs = build_query_string(50, 0)
    assert "query=%25" in qs
    assert "%25" in qs
    assert "limit=50" in qs
    assert "orderBy=published" in qs
    assert "start=0" in qs
