"""Uppstarts-backfill: _should_backfill-beslutslogik."""

from kt_rss.backfill import _done_file, _should_backfill


def test_should_backfill_av_som_default(settings):
    # backfill_pages = 0 -> kör aldrig, oavsett markör.
    off = settings.model_copy(update={"backfill_pages": 0})
    assert _should_backfill(off) is False


def test_should_backfill_nar_aktiverad(settings):
    on = settings.model_copy(update={"backfill_pages": 5})
    assert _should_backfill(on) is True


def test_should_backfill_minus_ett_kor_hela_arkivet(settings):
    # -1 = hela arkivet, ska också trigga.
    full = settings.model_copy(update={"backfill_pages": -1})
    assert _should_backfill(full) is True


def test_should_backfill_hoppar_nar_arkivet_genomgatt(settings):
    on = settings.model_copy(update={"backfill_pages": 5})
    _done_file(on.db_path).touch()
    assert _should_backfill(on) is False
