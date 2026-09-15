"""Research Theses — live Radar/Daily News evidence section (design/
DECISIONS.md). Pure functions only: filtering and sorting over already-
loaded FilingEvent/NewsStory/EditorialStory objects the caller (themes_
research.py) already read via backend_factory's own existing
repositories — this module never fetches, queries, or matches anything
new itself. Company-name matching is an EXACT match against
ThemeCompanyMapEntry.company_name, the same real TrackedCompany.name
identity convention every other part of this app already uses — never a
fuzzy/alias lookup, never a new inference/scoring system. "Live" means:
read fresh from the same repositories Radar/Daily News's own pages
already read from, at render time — never curated, never cached inside
the Theme record itself, and this module carries no persistence of its
own.

Deliberately minimal output (design/DECISIONS.md, "titles and links
only, no new reasoning layer"): EvidenceLink carries no summary,
materiality tier, admission reason, or confidence — only what's needed
to show and link to the item."""
from __future__ import annotations

from dataclasses import dataclass

from src.config.tracked_companies import TrackedCompany
from src.models.daily_news_models import EditorialStory, NewsStory, NewsStoryStatus
from src.models.models import FilingEvent


@dataclass(frozen=True)
class EvidenceLink:
    title: str
    url: str
    date: str  # source-native date string, already stored — never reformatted here
    company: str
    kind: str  # "Radar filing" | "Daily News"


def theme_tags_for_companies(
    company_names: frozenset[str], tracked_companies: tuple[TrackedCompany, ...],
) -> tuple[str, ...]:
    """Union of TrackedCompany.themes across every tracked company whose
    name exactly matches one of a Thesis's mapped companies. Every theme
    slug each matched company carries is included (not just its primary
    one) — a Thesis spanning several companies with different primary
    themes would otherwise lose real signal. Stable, first-seen order
    (never alphabetically re-sorted) — matches TrackedCompany.themes'
    own "primary first" convention rather than discarding it."""
    tags: list[str] = []
    for company in tracked_companies:
        if company.name not in company_names:
            continue
        for theme in company.themes:
            if theme not in tags:
                tags.append(theme)
    return tuple(tags)


def recent_filing_evidence(
    filings: tuple[FilingEvent, ...], company_names: frozenset[str], limit: int,
) -> tuple[EvidenceLink, ...]:
    """Exact match against FilingEvent.corp_name — the same identity
    convention scan_service.py already establishes for every tracked
    company's own filings, never a new resolution step. Sorted newest
    first by the filing's own stored rcept_dt, bounded to `limit`."""
    matched = [f for f in filings if f.corp_name in company_names]
    matched.sort(key=lambda f: f.rcept_dt, reverse=True)
    return tuple(
        EvidenceLink(title=f.report_nm, url=f.source_url, date=f.rcept_dt, company=f.corp_name, kind="Radar filing")
        for f in matched[:limit]
    )


def recent_daily_news_evidence(
    news_stories: tuple[NewsStory, ...], editorial_stories: tuple[EditorialStory, ...],
    company_names: frozenset[str], limit: int,
) -> tuple[EvidenceLink, ...]:
    """Issuer-matched NewsStory: exact match against company_name, and
    only ever a PUBLISHED story (the same gate daily_news.py's own
    render() already applies before showing any story publicly — never
    a DISCOVERED/SUMMARIZED/SUPPRESSED one). Editorial (issuer-agnostic)
    EditorialStory: a company_names overlap against matched_companies —
    every stored EditorialStory already passed editorial_matching's own
    fail-closed gate before it was ever persisted, so no separate status
    filter applies to it. Sorted newest first by each item's own stored
    published_at, bounded to `limit` across both kinds combined."""
    dated: list[tuple[str, EvidenceLink]] = []
    for story in news_stories:
        if story.status != NewsStoryStatus.PUBLISHED or story.company_name not in company_names:
            continue
        if not story.sources:
            continue
        source = story.sources[0]
        dated.append((source.published_at, EvidenceLink(
            title=story.headline, url=source.url, date=source.published_at,
            company=story.company_name, kind="Daily News",
        )))
    for story in editorial_stories:
        overlap = sorted(set(story.matched_companies) & company_names)
        if not overlap:
            continue
        dated.append((story.published_at, EvidenceLink(
            title=story.headline, url=story.source_url, date=story.published_at,
            company=overlap[0], kind="Daily News",
        )))
    dated.sort(key=lambda pair: pair[0], reverse=True)
    return tuple(link for _, link in dated[:limit])
