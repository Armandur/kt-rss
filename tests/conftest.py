"""Delade pytest-fixtures. Allt körs offline mot tests/fixtures/."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kt_rss.config import Settings
from kt_rss.db import init_db

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_response() -> dict:
    """Rått API-svar sparat av kt_rss.inspect (spec SS10)."""
    return json.loads((FIXTURES / "article_response.json").read_text(encoding="utf-8"))


@pytest.fixture
def raw_articles(fixture_response) -> list[dict]:
    return fixture_response["result"]


@pytest.fixture
def db_path(tmp_path) -> str:
    """En frisk, initierad SQLite-databas per test."""
    path = str(tmp_path / "kt.sqlite3")
    init_db(path)
    return path


@pytest.fixture
def settings(db_path) -> Settings:
    """Settings med explicita värden - oberoende av miljön/.env."""
    return Settings(
        db_path=db_path,
        base_url="https://www.kyrkanstidning.se",
        public_url="http://localhost:8000",
        api_url="https://api.kyrkanstidning.se/article",
        max_fetch=50,
        max_items=50,
        section_allowlist="",
    )
