import pytest

from src.models.models import ClaimType
from src.ui.components.evidence_chips import UnlinkedFactChipError, evidence_chip_html


def test_fact_chip_with_source_renders():
    html = evidence_chip_html(ClaimType.FACT, has_source=True)
    assert "er-chip-fact" in html
    assert "Fact" in html


def test_fact_chip_without_source_fails_loudly():
    with pytest.raises(UnlinkedFactChipError):
        evidence_chip_html(ClaimType.FACT, has_source=False)


def test_non_fact_chips_never_require_a_source():
    for ct in (ClaimType.INTERPRETATION, ClaimType.INFERENCE, ClaimType.UNCERTAINTY):
        html = evidence_chip_html(ct, has_source=False)
        assert ct.value in html


@pytest.mark.parametrize(
    "claim_type,expected_class",
    [
        (ClaimType.FACT, "er-chip-fact"),
        (ClaimType.INTERPRETATION, "er-chip-interpretation"),
        (ClaimType.INFERENCE, "er-chip-inference"),
        (ClaimType.UNCERTAINTY, "er-chip-uncertainty"),
    ],
)
def test_each_claim_type_gets_its_own_distinct_treatment(claim_type, expected_class):
    html = evidence_chip_html(claim_type, has_source=True)
    assert expected_class in html
