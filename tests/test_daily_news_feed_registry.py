"""feed_registry.PILOT_FEEDS — minimal focused coverage for the Bloom
Energy, Marvell, and MaxLinear registry additions: each entry is
recognized with the exact approved fields, resolves to the real tracked
company, and its item links validate only against its own approved
canonical-domain allowlist (never another company's domain)."""
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


def _marvell_source():
    matches = [s for s in PILOT_FEEDS if s.company_name == "Marvell Technology, Inc."]
    assert len(matches) == 1
    return matches[0]


def _maxlinear_source():
    matches = [s for s in PILOT_FEEDS if s.company_name == "MaxLinear, Inc."]
    assert len(matches) == 1
    return matches[0]


def test_marvell_is_registered_with_the_exact_approved_fields():
    source = _marvell_source()
    assert source.feed_url == "https://investor.marvell.com/rss-news-feed"
    assert source.feed_format == "rss"
    assert source.canonical_domains == ("investor.marvell.com",)


def test_marvell_resolves_to_the_real_tracked_company():
    company = tracked_company_for("Marvell Technology, Inc.")
    assert company is not None
    assert company.krx_code == "MRVL"
    assert "photonics" in company.themes


def test_marvell_item_url_validates_against_its_canonical_domain():
    source = _marvell_source()
    url = "https://investor.marvell.com/news-events/press-releases/detail/1031/marvell-technology-inc-reports-second-quarter-of-fiscal-year-2027-financial-results"
    assert validate_canonical_url(url, source.canonical_domains, source.feed_url)


def test_marvell_off_domain_url_is_rejected():
    source = _marvell_source()
    assert not validate_canonical_url("https://investors.maxlinear.com/news/detail/624/some-release", source.canonical_domains, source.feed_url)


def test_maxlinear_is_registered_with_the_exact_approved_fields():
    source = _maxlinear_source()
    assert source.feed_url == "https://investors.maxlinear.com/news/rss"
    assert source.feed_format == "rss"
    assert source.canonical_domains == ("investors.maxlinear.com",)


def test_maxlinear_resolves_to_the_real_tracked_company():
    company = tracked_company_for("MaxLinear, Inc.")
    assert company is not None
    assert company.krx_code == "MXL"
    assert "photonics" in company.themes


def test_maxlinear_item_url_validates_against_its_canonical_domain():
    source = _maxlinear_source()
    url = "https://investors.maxlinear.com/news/detail/623/maxlinear-introduces-rackcommander-control-plane-portfolio-for-next-generation-ai-infrastructure"
    assert validate_canonical_url(url, source.canonical_domains, source.feed_url)


def test_maxlinear_off_domain_url_is_rejected():
    source = _maxlinear_source()
    assert not validate_canonical_url("https://investor.marvell.com/news-events/press-releases/detail/1031/some-release", source.canonical_domains, source.feed_url)


def test_pilot_feeds_now_has_exactly_six_sources():
    assert len(PILOT_FEEDS) == 6
    assert {s.company_name for s in PILOT_FEEDS} == {
        "NVIDIA", "Intel Corp.", "Advanced Micro Devices", "Bloom Energy Corp",
        "Marvell Technology, Inc.", "MaxLinear, Inc.",
    }
