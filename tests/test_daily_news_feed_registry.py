"""feed_registry.PILOT_FEEDS — minimal focused coverage for the Bloom
Energy registry addition: the entry is recognized with the exact
approved fields, resolves to the real tracked company, and its item
links validate against the approved canonical-domain allowlist."""
from __future__ import annotations

from src.data_access.daily_news.canonical_url import validate_canonical_url
from src.data_access.daily_news.feed_registry import PILOT_FEEDS, tracked_company_for


def _bloom_energy_source():
    matches = [s for s in PILOT_FEEDS if s.company_name == "Bloom Energy Corp"]
    assert len(matches) == 1
    return matches[0]


def test_bloom_energy_is_registered_with_the_exact_approved_fields():
    source = _bloom_energy_source()
    assert source.feed_url == "https://investor.bloomenergy.com/rss/pressrelease.aspx"
    assert source.feed_format == "rss"
    assert source.canonical_domains == ("investor.bloomenergy.com",)


def test_bloom_energy_resolves_to_the_real_tracked_company():
    company = tracked_company_for("Bloom Energy Corp")
    assert company is not None
    assert company.krx_code == "BE"
    assert "ai-buildout" in company.themes


def test_bloom_energy_item_url_validates_against_its_canonical_domain():
    source = _bloom_energy_source()
    url = "https://investor.bloomenergy.com/press-releases/press-release-details/2026/Bloom-Energy-Reports-Record-Second-Quarter-2026-Financial-Results-and-Raises-Full-Year-2026-Guidance/default.aspx"
    assert validate_canonical_url(url, source.canonical_domains, source.feed_url)


def test_bloom_energy_off_domain_url_is_rejected():
    source = _bloom_energy_source()
    url = "https://www.businesswire.com/news/home/20260706169350/en/Bloom-Energy-to-Announce"
    assert not validate_canonical_url(url, source.canonical_domains, source.feed_url)


def test_pilot_feeds_now_has_exactly_four_sources():
    assert len(PILOT_FEEDS) == 4
    assert {s.company_name for s in PILOT_FEEDS} == {
        "NVIDIA", "Intel Corp.", "Advanced Micro Devices", "Bloom Energy Corp",
    }
