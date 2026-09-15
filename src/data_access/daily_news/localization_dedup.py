"""Cross-language localized-duplicate detection for Daily News's issuer
lane (Dashboard/Signals quality fix, design/
DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md). Pure, deterministic, no I/O —
the same discipline dedup.py itself already follows. The one input this
module needs that dedup.py's own callers don't have (a machine-
translated title) is always computed by the CALLER
(daily_news_pipeline.py, which already has cache_dir/translation_provider
in scope) before this module is ever invoked; this module never calls a
translation provider itself.

Narrow by construction: every one of the structural gates in
is_localized_duplicate() below must independently hold before the one
similarity check even runs — this is never a broad fuzzy-matching
system, and it can only ever fire for two first-party
(SourceClass.OFFICIAL_COMPANY) items belonging to the SAME already-
resolved tracked company, published close together in time, declared in
two different languages. It can never reach editorial (third-party/
independent-journalism) content at all, since that lane never
constructs a SourceClass.OFFICIAL_COMPANY item.

If confidence is insufficient at any gate, the two items are always
kept as two separate, fully visible stories — this module never
suppresses on a partial match.
"""
from __future__ import annotations

from src.data_access.daily_news.dedup import normalize_title_unicode
from src.models.daily_news_models import SourceClass

# Publication-time proximity window — a conservative, explicit, tunable
# constant. Localized versions of one real corporate announcement are
# ordinarily published within hours of each other, not days; a wide
# window would risk pairing two genuinely unrelated releases that only
# coincidentally share vocabulary.
MAX_PUBLISH_GAP_SECONDS = 48 * 60 * 60

# Conservative token-containment threshold + minimum absolute shared-
# token count — BOTH required, so a short or generic title can never
# match purely by chance (e.g. a bare one-word product name shared
# between two otherwise-unrelated releases). Deliberately high: this
# must never collapse two genuinely distinct releases, even ones that
# share a product name.
MIN_TOKEN_CONTAINMENT_RATIO = 0.6
MIN_SHARED_TOKEN_COUNT = 2


def _structural_gates_pass(
    *,
    candidate_company: str, candidate_source_class: SourceClass, candidate_language: str,
    candidate_published_at_epoch: float,
    existing_company: str, existing_source_class: SourceClass, existing_language: str,
    existing_published_at_epoch: float,
) -> bool:
    """Same resolved tracked company, both first-party OFFICIAL_COMPANY
    sources, a distinct declared language, and publication within
    MAX_PUBLISH_GAP_SECONDS of each other — every one of these is a hard
    requirement, never a scored/weighted signal."""
    if candidate_company != existing_company:
        return False
    if candidate_source_class != SourceClass.OFFICIAL_COMPANY or existing_source_class != SourceClass.OFFICIAL_COMPANY:
        return False
    if candidate_language == existing_language:
        return False
    if abs(candidate_published_at_epoch - existing_published_at_epoch) > MAX_PUBLISH_GAP_SECONDS:
        return False
    return True


def _token_containment(translated_title: str, existing_title: str) -> tuple[float, int]:
    """Normalized-token containment ratio (relative to whichever title
    has FEWER tokens) plus the raw shared-token count — both returned so
    the caller can require both a ratio AND an absolute floor. Uses the
    same normalize_title_unicode() this whole module's Daily News
    duplicate-title discipline already depends on, so a title differing
    only in punctuation/casing behaves identically to every other
    dedup check in this package."""
    translated_tokens = set(normalize_title_unicode(translated_title).split())
    existing_tokens = set(normalize_title_unicode(existing_title).split())
    if not translated_tokens or not existing_tokens:
        return 0.0, 0
    shared = translated_tokens & existing_tokens
    smaller = min(len(translated_tokens), len(existing_tokens))
    ratio = len(shared) / smaller if smaller else 0.0
    return ratio, len(shared)


def is_localized_duplicate(
    *,
    candidate_company: str, candidate_source_class: SourceClass, candidate_language: str,
    candidate_published_at_epoch: float, candidate_translated_title: str,
    existing_company: str, existing_source_class: SourceClass, existing_language: str,
    existing_published_at_epoch: float, existing_title: str,
) -> bool:
    """True only when every structural gate in _structural_gates_pass
    holds AND candidate_translated_title (the candidate's own title,
    ALREADY machine-translated to the same language as existing_title by
    the caller) shares at least MIN_SHARED_TOKEN_COUNT normalized tokens
    with existing_title, covering at least MIN_TOKEN_CONTAINMENT_RATIO
    of whichever title has fewer tokens.

    `candidate_translated_title` must already be a genuine translation.
    When no translation succeeded (e.g. the provider is unavailable or
    the call failed), the caller must not invoke this function with the
    raw, untranslated text as a substitute — "insufficient confidence"
    means keeping both items, never falling back to comparing text in
    two different languages directly."""
    if not _structural_gates_pass(
        candidate_company=candidate_company, candidate_source_class=candidate_source_class,
        candidate_language=candidate_language, candidate_published_at_epoch=candidate_published_at_epoch,
        existing_company=existing_company, existing_source_class=existing_source_class,
        existing_language=existing_language, existing_published_at_epoch=existing_published_at_epoch,
    ):
        return False
    ratio, shared_count = _token_containment(candidate_translated_title, existing_title)
    return ratio >= MIN_TOKEN_CONTAINMENT_RATIO and shared_count >= MIN_SHARED_TOKEN_COUNT
