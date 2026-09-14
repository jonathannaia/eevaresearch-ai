"""Editorial Daily News v1 (design/DECISIONS.md) — deterministic,
fail-closed matching for issuer-agnostic editorial feed items (CNBC,
Korea Herald, and — system-wide company-matched-news fix, design/
DECISIONS.md — every other configured editorial source). No LLM, no
ranking score, no ticker matching.

An item is eligible only if it has at least one company match or at
least one theme match (via the fixed, approved, narrow compound-phrase
table below — never a bare overloaded single word like "AI"/"space"/
"memory"/"robot"). Zero matches means the item is dropped entirely,
never constructed, never partially rendered.

Company matching checks two independent sources per company, both from
company_aliases.build_company_alias_entries():
1. `aliases` — the mechanical legal-name/suffix-stripped forms, matched
   case-insensitively (as before), with regex word boundaries so an
   alias never matches inside or across an unrelated word.
2. `brand_aliases` — the small, explicitly curated overlay of
   documented brand/common-name aliases, matched with the SAME word
   boundaries but CASE-SENSITIVELY (the alias's own real-world
   capitalization, e.g. "Google", "AWS", "IBM") — a deterministic,
   rule-based mitigation for common-word/acronym ambiguity, applied
   uniformly to every curated alias, never a per-company special case.
   This reduces, but does not eliminate, false-positive risk (e.g. a
   capitalized "Amazon River" reference would still not be affected by
   this field at all, since bare "Amazon" is deliberately never added
   to the overlay — see company_aliases.py's own docstring).

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
def _boundary_pattern(phrase: str, case_sensitive: bool = False) -> re.Pattern[str]:
    """Word boundaries on both ends of the full (possibly multi-word)
    phrase — an alias/keyword can never match inside or across an
    unrelated word. Case-insensitive by default (mechanical company
    aliases and theme phrases both appear with natural, varying
    real-world capitalization in editorial headlines); case-sensitive
    only for a curated brand_aliases entry — see this module's own
    docstring for why."""
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(r"\b" + re.escape(phrase) + r"\b", flags)


def _contains_phrase(text: str, phrase: str, case_sensitive: bool = False) -> bool:
    return bool(_boundary_pattern(phrase, case_sensitive).search(text))


def match_companies(text: str) -> tuple[str, ...]:
    """Every company in the Daily News company universe (see
    company_aliases.daily_news_company_names()) whose mechanical alias
    (case-insensitive) or curated brand alias (case-sensitive) appears
    in `text` with a real word boundary. Order matches
    daily_news_company_names()'s own sorted order; never duplicated."""
    matched: list[str] = []
    for entry in _company_alias_entries():
        mechanical_hit = any(_contains_phrase(text, alias) for alias in entry.aliases)
        brand_hit = any(_contains_phrase(text, alias, case_sensitive=True) for alias in entry.brand_aliases)
        if mechanical_hit or brand_hit:
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
