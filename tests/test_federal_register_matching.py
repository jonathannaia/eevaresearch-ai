"""federal_register_matching.qualifying_policy_developments — pure
function tests, no HTTP, no fixtures beyond in-memory
FederalRegisterDocument construction."""
from __future__ import annotations

from src.data_access.daily_news.policy_disclosure_models import DisclosureItemType
from src.data_access.policy_monitor.federal_register_client import FederalRegisterDocument
from src.logic.policy_monitor import federal_register_matching as matching

_ALLOWED_AGENCY = "Bureau of Industry and Security"
_DISALLOWED_AGENCY = "Department of Agriculture"


def _doc(**overrides) -> FederalRegisterDocument:
    defaults = dict(
        title="Export Controls on Semiconductor Manufacturing Equipment",
        type="Rule",
        document_number="2026-18194",
        html_url="https://www.federalregister.gov/documents/2026/09/04/2026-18194/example",
        publication_date="2026-09-04",
        agency_names=(_ALLOWED_AGENCY,),
    )
    defaults.update(overrides)
    return FederalRegisterDocument(**defaults)


# --- Type mapping ----------------------------------------------------


def test_rule_maps_to_enacted_rule():
    items = matching.qualifying_policy_developments((_doc(type="Rule"),))
    assert items[0].item_type == DisclosureItemType.ENACTED_RULE
    assert items[0].type_label == "Rule"


def test_proposed_rule_maps_to_proposed_rule():
    items = matching.qualifying_policy_developments((_doc(type="Proposed Rule"),))
    assert items[0].item_type == DisclosureItemType.PROPOSED_RULE
    assert items[0].type_label == "Proposed Rule"


def test_notice_maps_to_agency_action():
    items = matching.qualifying_policy_developments((_doc(type="Notice"),))
    assert items[0].item_type == DisclosureItemType.AGENCY_ACTION
    assert items[0].type_label == "Notice"


def test_disallowed_type_is_suppressed():
    for disallowed_type in ("Presidential Document", "Correction", "Unknown", None, ""):
        items = matching.qualifying_policy_developments((_doc(type=disallowed_type),))
        assert items == (), f"type={disallowed_type!r} must not qualify"


# --- Agency / keyword combinator (AND, not OR) ------------------------


def test_agency_only_match_is_suppressed():
    doc = _doc(title="A completely unrelated announcement about farming subsidies", agency_names=(_ALLOWED_AGENCY,))
    assert matching.qualifying_policy_developments((doc,)) == ()


def test_keyword_only_match_is_suppressed():
    doc = _doc(title="Export Controls on Semiconductor Manufacturing Equipment", agency_names=(_DISALLOWED_AGENCY,))
    assert matching.qualifying_policy_developments((doc,)) == ()


def test_agency_and_keyword_match_qualifies():
    doc = _doc(title="Export Controls on Semiconductor Manufacturing Equipment", agency_names=(_ALLOWED_AGENCY,))
    items = matching.qualifying_policy_developments((doc,))
    assert len(items) == 1
    assert items[0].agency_name == _ALLOWED_AGENCY
    assert items[0].matched_topic in ("Semiconductor", "Export control")


def test_url_keyword_alone_satisfies_the_keyword_requirement():
    doc = _doc(
        title="A generic title with no listed keyword",
        html_url="https://www.federalregister.gov/documents/2026/09/04/2026-1/entity-list-additions",
        agency_names=(_ALLOWED_AGENCY,),
    )
    items = matching.qualifying_policy_developments((doc,))
    assert len(items) == 1
    assert items[0].matched_topic == "Entity list"


def test_neither_agency_nor_keyword_matches_is_suppressed():
    doc = _doc(title="Routine grazing permit renewals", agency_names=(_DISALLOWED_AGENCY,))
    assert matching.qualifying_policy_developments((doc,)) == ()


# --- Required-field gates ----------------------------------------------


def test_missing_document_number_is_suppressed():
    assert matching.qualifying_policy_developments((_doc(document_number=None),)) == ()
    assert matching.qualifying_policy_developments((_doc(document_number=""),)) == ()


def test_missing_html_url_is_suppressed():
    assert matching.qualifying_policy_developments((_doc(html_url=None),)) == ()


def test_missing_publication_date_is_suppressed():
    assert matching.qualifying_policy_developments((_doc(publication_date=None),)) == ()


def test_invalid_publication_date_is_suppressed():
    for bad_date in ("not-a-date", "2026/09/04", "09-04-2026", "2026-13-40"):
        items = matching.qualifying_policy_developments((_doc(publication_date=bad_date),))
        assert items == (), f"publication_date={bad_date!r} must not qualify"


def test_missing_title_is_suppressed():
    assert matching.qualifying_policy_developments((_doc(title=None),)) == ()
    assert matching.qualifying_policy_developments((_doc(title="   "),)) == ()


# --- Theme resolution ----------------------------------------------------


def test_every_currently_qualifying_item_resolves_to_ai_buildout():
    """This pilot's approved scope is AI Buildout only (Federal Register
    Policy Monitor Pilot, scope correction) — see module docstring. No
    Federal Register item can resolve to a "memory" theme tag: none of
    the approved title/url keywords is memory-specific, and
    _THEME_DISPLAY_NAMES has no "memory" entry at all. Memory policy
    coverage is a separate, future, not-yet-approved task."""
    doc = _doc(title="Export Controls on Semiconductor Manufacturing Equipment")
    items = matching.qualifying_policy_developments((doc,))
    assert items[0].theme_slug == "ai-buildout"
    assert items[0].theme_display_name == "AI Buildout"


def test_theme_display_names_has_no_memory_entry():
    """Structural proof this pilot is AI Buildout only, not merely a
    behavioral coincidence of the current keyword set: "memory" is not
    even a resolvable display name, so no code path — current or future
    keyword addition to _KEYWORD_RESOLUTION — could render a "memory"
    theme without also, separately, adding this entry."""
    assert "memory" not in matching._THEME_DISPLAY_NAMES
    assert tuple(matching._THEME_DISPLAY_NAMES) == ("ai-buildout",)


# --- Reason string -------------------------------------------------------


def test_reason_is_exact_and_truthful():
    doc = _doc(title="Additions to the Entity List for diversion risk")
    items = matching.qualifying_policy_developments((doc,))
    assert items[0].reason == "Why shown: AI Buildout · Entity list"


# --- Per-fetch deduplication ----------------------------------------------


def test_duplicate_document_number_in_one_fetch_is_deduplicated():
    doc = _doc()
    items = matching.qualifying_policy_developments((doc, doc, doc))
    assert len(items) == 1


# --- Ordering --------------------------------------------------------


def test_ordering_is_publication_date_descending():
    older = _doc(document_number="2026-00001", publication_date="2026-09-01")
    newer = _doc(document_number="2026-00002", publication_date="2026-09-04")
    items = matching.qualifying_policy_developments((older, newer))
    assert [item.document_number for item in items] == ["2026-00002", "2026-00001"]


def test_document_number_ascending_is_the_tie_breaker_for_equal_dates():
    doc_b = _doc(document_number="2026-00002", publication_date="2026-09-04")
    doc_a = _doc(document_number="2026-00001", publication_date="2026-09-04")
    items = matching.qualifying_policy_developments((doc_b, doc_a))
    assert [item.document_number for item in items] == ["2026-00001", "2026-00002"]


# --- Bounded top-three -----------------------------------------------


def test_more_than_three_qualifying_items_returns_exactly_three_newest():
    docs = tuple(
        _doc(document_number=f"2026-{i:05d}", publication_date=f"2026-09-{i:02d}")
        for i in range(1, 6)
    )
    items = matching.qualifying_policy_developments(docs)
    assert len(items) == 3
    assert [item.document_number for item in items] == ["2026-00005", "2026-00004", "2026-00003"]


def test_zero_qualifying_items_returns_empty_tuple():
    assert matching.qualifying_policy_developments(()) == ()
    assert matching.qualifying_policy_developments((_doc(agency_names=(_DISALLOWED_AGENCY,)),)) == ()
