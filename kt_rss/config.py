"""Konfiguration (pydantic-settings) och statiska API-konstanter.

Bootstrap-fynd från `python -m kt_rss.inspect` (spec SS10) skrivs in i
avsnittet BOOTSTRAP-FYND längst ner i filen - de styr pollerns paginering.
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from kt_rss import __version__

# Minsta tillåtna pollningsintervall (hövlighet mot KT:s API, spec SS3).
MIN_POLL_MINUTES = 15

# KT:s bild-API. Dokumenterat för v2 (bild-enclosure), ej använt i v1-feeden.
IMAGE_API_BASE = "https://image.kyrkanstidning.se"

# section_tag-värden som räknas som debatt/insändare snarare än redaktionellt
# innehåll. Styr uppdelningen i skribentöversikten /a: en skribent som bara
# förekommit i dessa sektioner listas under "Debatt och insändare", övriga
# under "Redaktionen". Matchas skiftlägesokänsligt.
DEBATE_SECTIONS = {"debatt", "insändare", "minnesord"}


class Settings(BaseSettings):
    """Läses från miljön med prefix KT_RSS_ (eller .env-fil)."""

    model_config = SettingsConfigDict(
        env_prefix="KT_RSS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_path: str = "/data/kt.sqlite3"
    poll_minutes: int = 15
    api_url: str = "https://api.kyrkanstidning.se/article"
    base_url: str = "https://www.kyrkanstidning.se"
    public_url: str = "http://localhost:8000"
    max_fetch: int = 50
    max_items: int = 50
    page_size: int = 50
    section_allowlist: str = ""
    include_image_enclosure: bool = True
    backfill_pages: int = 0
    log_level: str = "INFO"

    # Notifiering via privat ntfy (infra-policy, avsnitt 3.2 och 6).
    # Tom topic eller token = notifiering av.
    ntfy_url: str = "https://ntfy.pettersson-vik.se"
    ntfy_topic: str = ""
    ntfy_token: str = ""
    notify_after_failures: int = 3

    @field_validator("poll_minutes")
    @classmethod
    def _enforce_min_poll(cls, v: int) -> int:
        # Klampas i stället för att kasta - en felkonfiguration ska inte
        # hindra start, men intervallet får aldrig understiga minimivärdet.
        return max(MIN_POLL_MINUTES, v)

    @field_validator("notify_after_failures")
    @classmethod
    def _at_least_one_failure(cls, v: int) -> int:
        return max(1, v)

    @field_validator("public_url", "base_url", "api_url", "ntfy_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/") if v != v.rstrip("/") and v.count("/") > 2 else v

    @property
    def user_agent(self) -> str:
        """Ärlig, identifierande User-Agent (spec SS3)."""
        return f"kt-rss-bridge/{__version__} (+self-hosted feed bridge)"

    @property
    def request_headers(self) -> dict[str, str]:
        """Headers som efterliknar webbklienten (spec SS2)."""
        return {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.kyrkanstidning.se/",
            "Origin": "https://www.kyrkanstidning.se",
        }

    @property
    def notify_enabled(self) -> bool:
        """Notifiering kräver både topic och token - annars tyst av."""
        return bool(self.ntfy_topic and self.ntfy_token and self.ntfy_url)

    @property
    def allowed_sections(self) -> set[str]:
        """section_tag-allowlist som set; tom = inga begränsningar."""
        return {
            s.strip().lower()
            for s in self.section_allowlist.split(",")
            if s.strip()
        }


def get_settings() -> Settings:
    """Skapar en Settings-instans. Anropas i lifespan och CLI-verktyg."""
    return Settings()


# ---------------------------------------------------------------------------
# BOOTSTRAP-FYND (spec SS2.3, SS10)
#
# Fastställt av `python -m kt_rss.inspect` mot live-API:et 2026-05-16.
# Råsvar: tests/fixtures/article_response.json, bild-API:
# tests/fixtures/image-api-findings.md.
#
#   start=0           : ger nyaste sidan, identisk första artikel som utan
#                       start-parameter. Specens rapport om "start=0 ger inga
#                       resultat" stämde inte mot live-API:et 2026-05-16.
#   offset-paginering : bekräftad - start=100 ger artiklar 100 steg bakåt.
#                       Backfill loopar start += limit (speglar canPage, SS2.3).
#   orderBy=published : nyaste först (descending) - bekräftat.
#   nextPageToken     : finns i svaret men behövs inte; offset räcker.
#   ETag/Last-Modified: API:et skickar ingendera. Conditional requests ger
#                       därför inget; 304-grenen i pollern behålls defensivt
#                       om API:et börjar skicka validatorer.
#   bild-API          : både x/y/cropw/croph och frontCropUrl-varianten
#                       (pano*/height*) ger 200 + image/webp.
# ---------------------------------------------------------------------------

# start-värde för nyaste sidan. 0 = explicit första sidan (bekräftat).
FIRST_PAGE_START = 0

# API:et skickar inte ETag/Last-Modified - conditional requests ger inget.
CONDITIONAL_REQUEST_SUPPORT = False
