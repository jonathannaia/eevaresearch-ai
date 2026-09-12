"""Editorial Daily News v1 (design/DECISIONS.md) — deterministic company
alias generation for editorial-feed matching. No ticker matching: exactly
two auditable aliases per active tracked company —

1. the exact TrackedCompany.name string;
2. that same name with the single longest matching legal-suffix pattern
   stripped from its end (one pass only — never recursive, never a
   shorter "brand name" heuristic beyond this one mechanical rule).

Every alias is matched by editorial_matching.py with regex word
boundaries on both ends of the full (possibly multi-word) phrase, so an
alias never matches inside an unrelated word or as a fragment of a
longer word.

Deliberately excludes src.config.tracked_companies.TrackedCompany.
krx_code entirely — that field is a ticker/exchange code, not a company
name, and Editorial Daily News v1's own approved scope removes ticker
matching altogether."""
from __future__ import annotations

from dataclasses import dataclass

from src.config.tracked_companies import TrackedCompany, get_tracked_companies

# Ordered here for human readability only — _strip_legal_suffix() below
# always picks the LONGEST matching suffix (by trying every entry and
# keeping the longest match), so accidental short-before-long ordering
# in this tuple can never cause an incorrect, too-short strip.
_LEGAL_SUFFIXES: tuple[str, ...] = (
    " Co., Ltd.", " Co., Ltd", " Co Ltd.", " Co Ltd",
    ", Inc.", " Inc.", ", Inc", " Inc",
    ", Corp.", " Corp.", ", Corp", " Corp",
    " Corporation", " Incorporated",
    ", Ltd.", " Ltd.", ", Ltd", " Ltd",
    " Limited",
    " plc", " PLC",
    " N.V.", " NV",
    " Company",
    " Holdings",
    " Group",
    " PBC",
    " Co.", " Co",
)


@dataclass(frozen=True)
class CompanyAliasEntry:
    company_name: str  # the real TrackedCompany.name — never invented, never re-derived
    aliases: tuple[str, ...]  # 1 or 2 entries: exact name, plus the suffix-stripped form if one exists


def _strip_legal_suffix(name: str) -> str | None:
    """Returns the name with its single longest matching legal suffix
    removed, or None if no suffix matches at all — never returns an
    empty or whitespace-only string, and never strips more than once."""
    lowered = name.lower()
    matches = [suffix for suffix in _LEGAL_SUFFIXES if lowered.endswith(suffix.lower())]
    if not matches:
        return None
    longest = max(matches, key=len)
    stripped = name[: len(name) - len(longest)].rstrip()
    return stripped if stripped else None


def _aliases_for(name: str) -> tuple[str, ...]:
    stripped = _strip_legal_suffix(name)
    if stripped is None or stripped == name:
        return (name,)
    return (name, stripped)


def build_company_alias_entries() -> tuple[CompanyAliasEntry, ...]:
    """Built fresh from the live, active tracked-company roster every
    call — never a hand-maintained, separately-drifting copy of the
    company list. Deliberately no caching: this is cheap, pure, and
    called at most once per editorial-matching pass."""
    return tuple(
        CompanyAliasEntry(company_name=company.name, aliases=_aliases_for(company.name))
        for company in get_tracked_companies()
        if company.active
    )
