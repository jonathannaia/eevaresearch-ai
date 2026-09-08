from __future__ import annotations

from src.config.settings import Settings
from src.data_access import loaders
from src.data_access.interfaces import ThemeRepository
from src.models.models import Subtheme, Theme

SEED_FILE = "themes.json"


def _parse_theme(raw: dict) -> Theme:
    subthemes = [
        Subtheme(slug=s["slug"], name=s["name"], description=s["description"], theme_slug=raw["slug"])
        for s in raw.get("subthemes", [])
    ]
    return Theme(slug=raw["slug"], name=raw["name"], description=raw["description"], subthemes=subthemes)


class DemoThemeRepository(ThemeRepository):
    def __init__(self, settings: Settings):
        self._settings = settings

    def _all(self) -> list[Theme]:
        raw = loaders.load(self._settings, SEED_FILE, default=[])
        return [_parse_theme(r) for r in raw]

    def get_all_themes(self) -> list[Theme]:
        return self._all()

    def get_theme(self, slug: str) -> Theme | None:
        return next((t for t in self._all() if t.slug == slug), None)
