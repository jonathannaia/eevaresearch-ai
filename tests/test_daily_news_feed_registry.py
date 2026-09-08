"""feed_registry.PILOT_FEEDS — minimal focused coverage for the Bloom
Energy, Marvell, MaxLinear, and Rockwell Automation registry additions:
each entry is recognized with the exact approved fields, resolves to the
real tracked company, and its item links validate only against its own
approved canonical-domain allowlist (never another company's domain).

Rockwell Automation is a one-company exception: its allowlist is a
single exact hostname on Q4's shared q4web.com platform
(rockwell2023tf.q4web.com), not Rockwell's own root domain — see
feed_registry.py's own inline comment. test_rockwell_rejects_other_
q4web_subdomains below proves the existing exact-match validation
(never a wildcard/suffix match) correctly rejects a different company's
q4web.com-hosted subdomain."""
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


def _rockwell_source():
    matches = [s for s in PILOT_FEEDS if s.company_name == "Rockwell Automation"]
    assert len(matches) == 1
    return matches[0]


def test_rockwell_is_registered_with_the_exact_approved_fields():
    source = _rockwell_source()
    assert source.feed_url == "https://rockwell2023tf.q4web.com/rss/pressrelease.aspx"
    assert source.feed_format == "rss"
    assert source.canonical_domains == ("rockwell2023tf.q4web.com",)


def test_rockwell_resolves_to_the_real_tracked_company():
    company = tracked_company_for("Rockwell Automation")
    assert company is not None
    assert company.krx_code == "ROK"
    assert "humanoids" in company.themes


def test_rockwell_item_url_validates_against_its_exact_canonical_domain():
    source = _rockwell_source()
    url = "https://rockwell2023tf.q4web.com/news/news-details/2026/Rockwell-Automation-Reports-Third-Quarter-2026-Results/"
    assert validate_canonical_url(url, source.canonical_domains, source.feed_url)


def test_rockwell_rejects_other_q4web_subdomains():
    # The approved exception is the exact hostname only — never a
    # wildcard/suffix match across q4web.com. A different company's own
    # q4web.com-hosted subdomain (e.g. Bloom Energy's, if it were ever
    # hosted there) must still be rejected.
    source = _rockwell_source()
    for other_subdomain_url in (
        "https://otherco2024xyz.q4web.com/news/news-details/2026/Some-Other-Company-Release/",
        "https://q4web.com/news/news-details/2026/Bare-Domain-Release/",
    ):
        assert not validate_canonical_url(other_subdomain_url, source.canonical_domains, source.feed_url)


def test_rockwell_off_domain_url_is_rejected():
    source = _rockwell_source()
    assert not validate_canonical_url("https://investor.marvell.com/news-events/press-releases/detail/1031/some-release", source.canonical_domains, source.feed_url)


def _sk_hynix_source():
    matches = [s for s in PILOT_FEEDS if s.company_name == "SK Hynix"]
    assert len(matches) == 1
    return matches[0]


def test_sk_hynix_is_registered_with_the_exact_approved_fields():
    source = _sk_hynix_source()
    assert source.feed_url == "https://news.skhynix.com/en/feed"
    assert source.feed_format == "rss"
    assert source.canonical_domains == ("news.skhynix.com",)


def test_sk_hynix_resolves_to_the_real_tracked_company():
    company = tracked_company_for("SK Hynix")
    assert company is not None
    assert company.krx_code == "000660"
    assert "memory" in company.themes


def test_sk_hynix_item_url_validates_against_its_exact_canonical_domain():
    source = _sk_hynix_source()
    url = "https://news.skhynix.com/en/indiana-groundbreaking-ceremony-sketch/"
    assert validate_canonical_url(url, source.canonical_domains, source.feed_url)


def test_sk_hynix_rejects_other_hostnames_including_skhynix_like_ones():
    # The approved allowlist is the exact hostname only — never a
    # wildcard/suffix/parent-domain match across skhynix.com. A
    # different real subdomain, the bare parent domain, and a
    # hypothetical unrelated/third-party skhynix-like hostname must all
    # still be rejected.
    source = _sk_hynix_source()
    for other_url in (
        "https://www.skhynix.com/en/some-page/",
        "https://skhynix.com/en/some-page/",
        "https://news.skhynix.com.evil-example.com/en/fake-release/",
    ):
        assert not validate_canonical_url(other_url, source.canonical_domains, source.feed_url)


def test_sk_hynix_off_domain_url_is_rejected():
    source = _sk_hynix_source()
    assert not validate_canonical_url("https://investor.marvell.com/news-events/press-releases/detail/1031/some-release", source.canonical_domains, source.feed_url)


def _quanta_source():
    matches = [s for s in PILOT_FEEDS if s.company_name == "Quanta Services, Inc."]
    assert len(matches) == 1
    return matches[0]


def test_quanta_is_registered_with_the_exact_approved_fields():
    source = _quanta_source()
    assert source.feed_url == "https://investors.quantaservices.com/news-events/press-releases/rss"
    assert source.feed_format == "rss"
    assert source.canonical_domains == ("investors.quantaservices.com",)


def test_quanta_resolves_to_the_discovered_issuer_with_ticker_pwr():
    # Was resolved only via tracked_company_for()'s DISCOVERY_STUBS
    # fallback path (Quanta absent from tracked_companies.py). The Core
    # Issuer Expansion batch (2026-09-04) — a separate, later,
    # explicitly-approved action — added Quanta Services as a real,
    # verified TrackedCompany/SEED_ISSUERS entry too, so
    # tracked_company_for() (unmodified — see feed_registry.py's own
    # "checks two sources in order" docstring) now correctly matches the
    # real tracked-company path first, returning its real Radar source
    # and active status instead of the synthesized stub placeholder.
    company = tracked_company_for("Quanta Services, Inc.")
    assert company is not None
    assert company.krx_code == "PWR"
    assert company.source == "SEC EDGAR"
    assert company.active is True
    assert "ai-buildout" in company.themes


def test_quanta_item_url_validates_against_its_exact_canonical_domain():
    source = _quanta_source()
    url = "https://investors.quantaservices.com/news-events/press-releases/detail/402/quanta-services-reports-second-quarter-2026-results"
    assert validate_canonical_url(url, source.canonical_domains, source.feed_url)


def test_quanta_rejects_bare_domain_www_other_subdomain_and_lookalike_hostnames():
    source = _quanta_source()
    for other_url in (
        "https://quantaservices.com/news-events/press-releases/detail/402/some-release",
        "https://www.quantaservices.com/news-events/press-releases/detail/402/some-release",
        "https://blog.quantaservices.com/news-events/press-releases/detail/402/some-release",
        "https://investors.quantaservices.com.evil-example.com/fake-release/",
    ):
        assert not validate_canonical_url(other_url, source.canonical_domains, source.feed_url)


def test_quanta_off_domain_url_is_rejected():
    source = _quanta_source()
    assert not validate_canonical_url("https://news.skhynix.com/en/some-release/", source.canonical_domains, source.feed_url)


def _nvent_source():
    matches = [s for s in PILOT_FEEDS if s.company_name == "nVent Electric plc"]
    assert len(matches) == 1
    return matches[0]


def test_nvent_is_registered_with_the_exact_approved_fields():
    source = _nvent_source()
    assert source.feed_url == "https://investors.nvent.com/rss/pressrelease.aspx"
    assert source.feed_format == "rss"
    assert source.canonical_domains == ("investors.nvent.com",)


def test_nvent_resolves_to_the_discovered_issuer_with_ticker_nvt():
    # Was resolved only via the DISCOVERY_STUBS fallback path — see the
    # matching Quanta test above for why this now asserts the real
    # tracked-company path instead, after the Core Issuer Expansion
    # batch (2026-09-04).
    company = tracked_company_for("nVent Electric plc")
    assert company is not None
    assert company.krx_code == "NVT"
    assert company.source == "SEC EDGAR"
    assert company.active is True
    assert "ai-buildout" in company.themes
    assert "power-cooling" in company.subthemes


def test_nvent_item_url_validates_against_its_exact_canonical_domain():
    source = _nvent_source()
    url = "https://investors.nvent.com/press-releases/press-release-details/2026/nVent-to-Acquire-Maverick-Power/default.aspx"
    assert validate_canonical_url(url, source.canonical_domains, source.feed_url)


def test_nvent_rejects_bare_domain_www_other_subdomain_and_lookalike_hostnames():
    source = _nvent_source()
    for other_url in (
        "https://nvent.com/press-releases/press-release-details/2026/some-release/",
        "https://www.nvent.com/press-releases/press-release-details/2026/some-release/",
        "https://blog.nvent.com/press-releases/press-release-details/2026/some-release/",
        "https://investors.nvent.com.evil-example.com/fake-release/",
    ):
        assert not validate_canonical_url(other_url, source.canonical_domains, source.feed_url)


def test_nvent_off_domain_url_is_rejected():
    source = _nvent_source()
    assert not validate_canonical_url("https://investors.quantaservices.com/news-events/press-releases/detail/402/some-release", source.canonical_domains, source.feed_url)


def _arista_source():
    matches = [s for s in PILOT_FEEDS if s.company_name == "Arista Networks, Inc."]
    assert len(matches) == 1
    return matches[0]


def _cisco_source():
    matches = [s for s in PILOT_FEEDS if s.company_name == "Cisco Systems, Inc."]
    assert len(matches) == 1
    return matches[0]


def test_arista_is_registered_with_the_exact_approved_fields():
    source = _arista_source()
    assert source.feed_url == "https://investors.arista.com/rss/pressrelease.aspx"
    assert source.feed_format == "rss"
    assert source.canonical_domains == ("investors.arista.com",)


def test_arista_resolves_to_the_discovered_issuer_with_ticker_anet():
    # Was resolved only via the DISCOVERY_STUBS fallback path — see
    # test_quanta_resolves_to_the_discovered_issuer_with_ticker_pwr's own
    # comment for why this now asserts the real tracked-company path
    # instead, after the Core Issuer Expansion batch (2026-09-04).
    company = tracked_company_for("Arista Networks, Inc.")
    assert company is not None
    assert company.krx_code == "ANET"
    assert company.source == "SEC EDGAR"
    assert company.active is True
    assert "ai-buildout" in company.themes


def test_arista_item_url_validates_against_its_exact_canonical_domain():
    source = _arista_source()
    url = "https://investors.arista.com/Communications/Press-Releases-and-Events/Press-Release-Detail/2026/Arista-Networks-Inc--Reports-Second-Quarter-2026-Financial-Results/default.aspx"
    assert validate_canonical_url(url, source.canonical_domains, source.feed_url)


def test_arista_rejects_bare_domain_www_other_subdomain_and_lookalike_hostnames():
    source = _arista_source()
    for other_url in (
        "https://arista.com/company/news/press-release/some-release/",
        "https://www.arista.com/company/news/press-release/some-release/",
        "https://blog.arista.com/company/news/press-release/some-release/",
        "https://investors.arista.com.evil-example.com/fake-release/",
    ):
        assert not validate_canonical_url(other_url, source.canonical_domains, source.feed_url)


def test_arista_off_domain_url_is_rejected():
    source = _arista_source()
    assert not validate_canonical_url("https://newsroom.cisco.com/c/r/newsroom/en/us/a/y2026/m08/some-article.html", source.canonical_domains, source.feed_url)


def test_cisco_is_registered_with_the_exact_approved_fields():
    source = _cisco_source()
    assert source.feed_url == "https://newsroom.cisco.com/c/services/i/servlets/newsroom/rssfeed.json?feed=press-releases"
    assert source.feed_format == "rss"
    assert source.canonical_domains == ("newsroom.cisco.com",)


def test_cisco_resolves_to_the_discovered_issuer_with_ticker_csco():
    # Was resolved only via the DISCOVERY_STUBS fallback path — see
    # test_quanta_resolves_to_the_discovered_issuer_with_ticker_pwr's own
    # comment for why this now asserts the real tracked-company path
    # instead, after the Core Issuer Expansion batch (2026-09-04).
    company = tracked_company_for("Cisco Systems, Inc.")
    assert company is not None
    assert company.krx_code == "CSCO"
    assert company.source == "SEC EDGAR"
    assert company.active is True
    assert "ai-buildout" in company.themes


def test_cisco_item_url_validates_against_its_exact_canonical_domain():
    source = _cisco_source()
    url = "https://newsroom.cisco.com/c/r/newsroom/en/us/a/y2026/m08/cisco-secure-ai-factory-nvidia-rack-scale.html?source=rss"
    assert validate_canonical_url(url, source.canonical_domains, source.feed_url)


def test_cisco_rejects_bare_domain_www_investor_subdomain_and_lookalike_hostnames():
    source = _cisco_source()
    for other_url in (
        "https://cisco.com/c/r/newsroom/en/us/a/y2026/m08/some-article.html",
        "https://www.cisco.com/c/r/newsroom/en/us/a/y2026/m08/some-article.html",
        "https://investor.cisco.com/resources-and-faqs/rss-feeds/default.aspx",
        "https://newsroom.cisco.com.evil-example.com/fake-article.html",
    ):
        assert not validate_canonical_url(other_url, source.canonical_domains, source.feed_url)


def test_cisco_off_domain_url_is_rejected():
    source = _cisco_source()
    assert not validate_canonical_url("https://investors.arista.com/Communications/Press-Releases-and-Events/some-release/", source.canonical_domains, source.feed_url)


# --- Intel Corp. — feed-URL repair (Daily News feed audit, design/DECISIONS.md):
# newsroom.intel.com/feed no longer serves RSS (confirmed live, 404s into an
# Access-Denied redirector); replaced with Intel's own official investor-
# relations RSS feed at www.intc.com, confirmed live. ---


def _intel_source():
    matches = [s for s in PILOT_FEEDS if s.company_name == "Intel Corp."]
    assert len(matches) == 1
    return matches[0]


def test_intel_is_registered_with_the_repaired_investor_relations_feed():
    source = _intel_source()
    assert source.feed_url == "https://www.intc.com/news-events/press-releases/rss"
    assert source.feed_format == "rss"
    assert source.canonical_domains == ("www.intc.com",)


def test_intel_resolves_to_the_real_tracked_company():
    company = tracked_company_for("Intel Corp.")
    assert company is not None
    assert company.krx_code == "INTC"
    assert "ai-buildout" in company.themes


def test_intel_item_url_validates_against_its_repaired_canonical_domain():
    source = _intel_source()
    url = "https://www.intc.com/news-events/press-releases/detail/1776/intel-reports-second-quarter-2026-financial-results"
    assert validate_canonical_url(url, source.canonical_domains, source.feed_url)


def test_intel_rejects_the_old_retired_newsroom_domain_and_lookalike_hostnames():
    source = _intel_source()
    for other_url in (
        "https://newsroom.intel.com/some-news-article",
        "https://intc.com/news-events/press-releases/detail/1776/some-release",
        "https://www.intc.com.evil-example.com/fake-release/",
    ):
        assert not validate_canonical_url(other_url, source.canonical_domains, source.feed_url)


def test_intel_off_domain_url_is_rejected():
    source = _intel_source()
    assert not validate_canonical_url("https://investors.arista.com/Communications/Press-Releases-and-Events/some-release/", source.canonical_domains, source.feed_url)


def test_pilot_feeds_now_has_exactly_twenty_sources():
    # Was exactly 12 through Daily News source-expansion batch 1
    # (2026-09-04), which appended 7 more official IR/newsroom RSS
    # sources after the original 12 (12 + 7 = 19) — PILOT_FEEDS is now
    # derived from source_registry.RUNTIME_SOURCE_REGISTRY; see that
    # module's own docstring. Expansion batch 2 (2026-09-04) then
    # appended one more entry — a second, distinct source for the
    # already-present Meta Platforms, Inc. (19 + 1 = 20; the set of
    # distinct company names below is unaffected, since both Meta
    # entries share the same company_name).
    assert len(PILOT_FEEDS) == 20
    assert {s.company_name for s in PILOT_FEEDS} == {
        "NVIDIA", "Intel Corp.", "Advanced Micro Devices", "Bloom Energy Corp",
        "Marvell Technology, Inc.", "MaxLinear, Inc.", "Rockwell Automation", "SK Hynix",
        "Quanta Services, Inc.", "nVent Electric plc", "Arista Networks, Inc.", "Cisco Systems, Inc.",
        "Amazon.com, Inc.", "Meta Platforms, Inc.", "Oracle Corporation", "Applied Materials, Inc.",
        "Lam Research Corp", "KLA Corp", "Arm Holdings plc",
    }


# --- image_host: exactly three approved mappings, all others None --------


def test_exactly_three_sources_have_an_approved_image_host():
    with_image_host = {s.company_name: s.image_host for s in PILOT_FEEDS if s.image_host is not None}
    assert with_image_host == {
        "NVIDIA": "iprsoftwaremedia.com",
        "SK Hynix": "d18r0a86za96sg.cloudfront.net",
        "Cisco Systems, Inc.": "newsroom.cisco.com",
    }


def test_all_other_sources_have_no_approved_image_host():
    other_companies = {
        "Intel Corp.", "Advanced Micro Devices", "Bloom Energy Corp", "Marvell Technology, Inc.",
        "MaxLinear, Inc.", "Rockwell Automation", "Quanta Services, Inc.", "nVent Electric plc",
        "Arista Networks, Inc.",
    }
    for source in PILOT_FEEDS:
        if source.company_name in other_companies:
            assert source.image_host is None
