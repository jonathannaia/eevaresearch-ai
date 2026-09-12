"""Editorial Daily News v1 (design/DECISIONS.md) — deterministic,
fail-closed matching for issuer-agnostic editorial feed items (CNBC,
Korea Herald). No LLM, no ranking score, no ticker matching.

An item is eligible only if it has at least one company match (via
company_aliases.py's two-alias-per-company table, matched with regex
word boundaries so an alias never matches inside or across an unrelated
word) or at least one theme match (via the fixed, approved, narrow
compound-phrase table below — never a bare overloaded single word like
"AI"/"space"/"memory"/"robot"). Zero matches means the item is dropped
entirely, never constructed, never partially rendered.

Matching is checked against the item's title AND its raw description
(when present) — both are real, source-provided text, never generated.
"""
from __future__ import annotations

import re
from functools import lru_cache

from src.data_access.daily_news.company_aliases import CompanyAliasEntry, build_company_alias_entries

# Exact, approved compound phrases only — every entry here was
# explicitly approved; do not add, remove, or broaden without a
# separate proposal. Deliberately no bare "AI"/"space"/"memory"/"robot":
# every phrase is either a standalone narrow term (HBM, DRAM, photonics)
# or a required multi-word compound.
THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ai-buildout": (
        "AI data center", "AI infrastructure", "AI chip", "AI accelerator",
        "AI cloud", "GPU cluster", "hyperscaler capex", "data center capex",
    ),
    "memory": (
        "HBM", "DRAM", "NAND flash", "NAND chip", "memory chip",
        "memory market", "DDR5", "high-bandwidth memory",
    ),
    "space": (
        "satellite launch", "rocket launch", "orbital launch",
        "launch vehicle", "spacecraft", "space station",
        "satellite constellation",
    ),
    "photonics": (
        "photonics", "silicon photonics", "optical networking",
        "co-packaged optics", "optical transceiver", "optical interconnect",
    ),
    "humanoids": (
        "humanoid robot", "humanoid robotics", "industrial robot",
        "warehouse robotics", "robotics automation",
    ),
}


@lru_cache(maxsize=1)
def _company_alias_entries() -> tuple[CompanyAliasEntry, ...]:
    return build_company_alias_entries()


@lru_cache(maxsize=None)
def _boundary_pattern(phrase: str) -> re.Pattern[str]:
    """Word boundaries on both ends of the full (possibly multi-word)
    phrase — an alias/keyword can never match inside or across an
    unrelated word. Case-insensitive: company names and theme phrases
    both appear with natural, varying real-world capitalization in
    editorial headlines."""
    return re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)


def _contains_phrase(text: str, phrase: str) -> bool:
    return bool(_boundary_pattern(phrase).search(text))


def match_companies(text: str) -> tuple[str, ...]:
    """Every currently active tracked company whose exact name or
    single-suffix-stripped alias appears in `text` with a real word
    boundary. Order matches the live tracked-company roster; never
    duplicated."""
    matched: list[str] = []
    for entry in _company_alias_entries():
        if any(_contains_phrase(text, alias) for alias in entry.aliases):
            matched.append(entry.company_name)
    return tuple(matched)


def match_themes(text: str) -> tuple[str, ...]:
    """Every theme with at least one approved compound phrase present
    in `text` with a real word boundary. Order matches THEME_KEYWORDS'
    own declaration order; never duplicated."""
    matched: list[str] = []
    for theme_slug, phrases in THEME_KEYWORDS.items():
        if any(_contains_phrase(text, phrase) for phrase in phrases):
            matched.append(theme_slug)
    return tuple(matched)


def matched_companies_and_themes(
    title: str, description: str | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The one public entry point: checks both the item's real title and
    its real description (when present) — never anything fetched from a
    linked page, never anything generated. Returns
    (matched_companies, matched_themes), each possibly empty. Fail-closed
    eligibility (at least one non-empty) is the caller's own decision —
    this function only ever reports facts, never decides eligibility."""
    combined = title if not description else f"{title}\n{description}"
    return match_companies(combined), match_themes(combined)
