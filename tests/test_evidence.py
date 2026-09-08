from src.logic.evidence import cite_label, claim_type_badge, freshness_badge, source_label
from src.models.models import ClaimType, EvidenceItem


def _evidence(**overrides) -> EvidenceItem:
    defaults = dict(
        id="1", title="t", source_name="EevaResearch Demo Data", source_type="Demo Dataset",
        published_at="2026-08-15T00:00:00+00:00", retrieved_at="2026-08-15T00:00:00+00:00",
        excerpt="e", claim_type=ClaimType.FACT,
    )
    defaults.update(overrides)
    return EvidenceItem(**defaults)


def test_claim_type_badge_returns_label_and_color_for_all_types():
    for ct in ClaimType:
        label, color = claim_type_badge(ct)
        assert label == ct.value
        assert color != "unknown"


def test_freshness_badge_matches_evidence_freshness_label():
    ev = _evidence(retrieved_at="2026-08-15T00:00:00+00:00")
    label, color = freshness_badge(ev)
    assert label == ev.freshness_label
    assert color != "unknown" or label == "Unknown"


def test_source_label_no_url_states_demo_data_explicitly():
    ev = _evidence(source_url=None)
    label = source_label(ev)
    assert "demo data" in label.lower()
    assert "EevaResearch Demo Data" in label


def test_source_label_with_url_includes_it():
    ev = _evidence(source_url="https://example.com/demo-only")
    label = source_label(ev)
    assert "https://example.com/demo-only" in label


def test_cite_label_joins_issuer_venue_date():
    ev = _evidence(published_at="2026-08-14T09:00:00+00:00")
    label = cite_label(ev)
    assert "EevaResearch Demo Data" in label
    assert "Demo Dataset" in label
    assert "Aug 14, 2026" in label


def test_cite_label_appends_document_location_when_present():
    ev = _evidence(document_location="p.7 (demo)")
    assert cite_label(ev).endswith("p.7 (demo)")


def test_cite_label_omits_document_location_when_absent():
    ev = _evidence(document_location="")
    assert cite_label(ev).count(" · ") == 2
