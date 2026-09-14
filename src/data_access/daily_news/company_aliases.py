"""Editorial Daily News v1 (design/DECISIONS.md) — deterministic company
alias generation for editorial-feed matching. Two independent alias
sources per company, kept in separate fields so each stays independently
auditable:

1. `aliases` — exactly one or two MECHANICAL, auto-derived aliases for
   every company in the Daily News universe (see `daily_news_company_names()`
   below): the exact company name, plus that same name with the single
   longest matching legal-suffix pattern stripped from its end (one pass
   only — never recursive, never a shorter "brand name" heuristic beyond
   this one mechanical rule). Unchanged behavior/shape from before the
   company-matched-news system-wide fix.
2. `brand_aliases` — a small, explicitly CURATED overlay (see
   `_BRAND_ALIAS_OVERLAY` below) of documented brand/common-name aliases
   for a company whose real-world editorial coverage commonly uses a name
   that a mechanical legal-suffix strip alone can't reach. Every entry
   requires an explicit human review and a real-world justification —
   never auto-derived, never guessed. Deliberately excludes any bare,
   ambiguous single-word brand name (e.g. bare "Amazon", "Meta",
   "Facebook") — those carry real false-positive risk (e.g. "Amazon"
   also names a river/rainforest) that capitalization alone does not
   remove; only low-ambiguity, multi-word or well-established-acronym
   aliases are added here, each with its own false-positive test in
   tests/test_company_aliases.py and tests/test_editorial_matching.py.
   A future broader brand-alias expansion is a separate, later reviewed
   decision, not part of this pass.

Every alias (mechanical or curated) is matched by editorial_matching.py
with regex word boundaries on both ends of the full (possibly
multi-word) phrase, so an alias never matches inside an unrelated word
or as a fragment of a longer word.

Deliberately excludes src.config.tracked_companies.TrackedCompany.
krx_code entirely — that field is a ticker/exchange code, not a company
name, and Editorial Daily News v1's own approved scope removes ticker
matching altogether.

Company universe (system-wide company-matched-news fix, design/
DECISIONS.md): `daily_news_company_names()` is the one shared,
data-driven source of truth for "every company Daily News should be
able to match editorial coverage for" — every active tracked company
(Radar's own scan universe) plus every `issuer_registry.DISCOVERY_STUBS`
entry added via the established Daily News source-admission process,
identified by that entry's own already-real `discovered_via` provenance
string (never a newly-invented marker), that is not already present in
tracked_companies.py. This is also what `src.ui.pages.daily_news`'s
company selector reads, so any company addable via either supported
mechanism — a real tracked-company addition, or a Daily-News-only
`DISCOVERY_STUBS` addition following the same established pattern
already used for Hewlett Packard Enterprise Company — is automatically
selectable and alias-matched with zero further code change. The ~21
unrelated 2026-08-20 portfolio-map seed-list `DISCOVERY_STUBS`
candidates (unverified, never part of Daily News, several explicitly
flagged ambiguous, no working feed) are deliberately excluded — only the
`discovered_via` marker already used for Quanta Services/nVent Electric/
Arista Networks/Cisco Systems/Hewlett Packard Enterprise is read."""
from __future__ import annotations

from dataclasses import dataclass

from src.config.issuer_registry import DISCOVERY_STUBS
from src.config.tracked_companies import TrackedCompany, get_tracked_companies

# The exact, already-real provenance string every Daily-News-only
# DISCOVERY_STUBS entry carries (Quanta Services, nVent Electric, Arista
# Networks, Cisco Systems, Hewlett Packard Enterprise Company) — never a
# newly-invented marker. A future Daily-News-only stub addition that
# follows this same established convention is picked up automatically.
_DAILY_NEWS_STUB_PROVENANCE = "Daily News official-feed verification (design/DECISIONS.md)"

# Curated brand-alias overlay (system-wide company-matched-news fix,
# design/DECISIONS.md) — every entry explicitly reviewed and approved;
# never auto-derived. Deliberately excludes bare "Amazon", "Meta",
# "Facebook" (real ambiguity risk a capitalization-only mitigation does
# not remove) — only low-ambiguity, multi-word or well-established-
# acronym aliases are included this pass.
_BRAND_ALIAS_OVERLAY: dict[str, tuple[str, ...]] = {
    # Multi-word, essentially zero real-world ambiguity.
    "Amazon.com, Inc.": ("Amazon Web Services", "AWS"),
    # Well-established, low-ambiguity in business/tech editorial
    # coverage; still matched case-sensitively (see editorial_matching.py)
    # as an additional deterministic mitigation.
    "Alphabet Inc.": ("Google",),
    "International Business Machines Corporation": ("IBM",),
}

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
    company_name: str  # the real company/issuer name — never invented, never re-derived
    aliases: tuple[str, ...]  # 1 or 2 mechanical entries: exact name, plus the suffix-stripped form if one exists
    # Curated brand-alias overlay (see _BRAND_ALIAS_OVERLAY) — empty for
    # every company not explicitly reviewed and added. Kept as its own
    # field, never merged into `aliases`, so the two sources stay
    # independently auditable and every existing `aliases`-only
    # assertion (1-2 entries, first is the exact name) stays true
    # unchanged for every company.
    brand_aliases: tuple[str, ...] = ()


def daily_news_company_names() -> tuple[str, ...]:
    """The full, data-driven Daily News company universe — see this
    module's own docstring for the exact rule. Sorted for a stable,
    deterministic iteration order; never a hand-maintained list."""
    tracked_names = {c.name for c in get_tracked_companies()}  # already active_only=True by default
    daily_news_stub_names = {
        issuer.legal_name for issuer in DISCOVERY_STUBS
        if issuer.discovered_via == _DAILY_NEWS_STUB_PROVENANCE and issuer.legal_name not in tracked_names
    }
    return tuple(sorted(tracked_names | daily_news_stub_names))


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
    """Built fresh from daily_news_company_names() every call — never a
    hand-maintained, separately-drifting copy of the company list.
    Deliberately no caching: this is cheap, pure, and called at most
    once per editorial-matching pass."""
    return tuple(
        CompanyAliasEntry(
            company_name=name, aliases=_aliases_for(name), brand_aliases=_BRAND_ALIAS_OVERLAY.get(name, ()),
        )
        for name in daily_news_company_names()
    )
