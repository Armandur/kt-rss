"""Bild-URL-bygge för enclosure (spec SS2.2)."""

from kt_rss.feed import build_image_url

IMG = "https://image.kyrkanstidning.se"


def test_build_image_url_fran_fixture(raw_articles):
    # Fixturens första artikel har image="434429".
    url = build_image_url(raw_articles[0]["image"])
    assert url.startswith(f"{IMG}/434429.webp?")
    assert "imageId=434429" in url
    assert "format=webp" in url


def test_build_image_url_full_rendition():
    # Crop låses till full bild - cropw/croph är 100, ingen beskärning.
    url = build_image_url("999")
    assert url.startswith(f"{IMG}/999.webp?")
    assert "cropw=100" in url and "croph=100" in url
    assert "heightw=100" in url and "heighth=100" in url


def test_build_image_url_format():
    assert build_image_url("1", fmt="jpeg").startswith(f"{IMG}/1.jpeg")


def test_build_image_url_med_storlek():
    # width skalar proportionerligt; width+height tvingar exakta mått.
    # Utan storleksparameter svarar API:t bara med en 100x56-rendition.
    url = build_image_url("5", width=480, height=312)
    assert "width=480" in url and "height=312" in url
    assert "width=1200" in build_image_url("5", width=1200)
    assert "width=" not in build_image_url("5")
