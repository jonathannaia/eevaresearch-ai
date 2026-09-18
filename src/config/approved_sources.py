"""APPROVED_OFFICIAL_SOURCE_ALLOWLIST (design §6 search_approved_official_
sources, §8.1, §8.3) — the only domains that may support an auto-published
fact. Backend config, never agent-editable; extending it is an ordinary,
separately-approved config change (design §15).

Three tiers are recognized:
- filing hosts: the three adapters' own primary sources (SEC EDGAR, DART,
  EDINET) — fixed here, these are the product's foundation;
- issuer IR: an issuer's own investor-relations domain, taken from the
  issuer registry's Issuer.ir_domain — never guessed from a URL;
- government/regulator: explicit domains only. Deliberately EMPTY by
  default — no regulator source is approved until one is added here.

Matching is exact-suffix on a case-normalized hostname (design §6: "any URL
not resolving to an allowlisted domain is dropped before the response is
built, never returned for the model to judge"). No redirect is ever
followed to reach this decision — the URL is classified as given.
"""
from __future__ import annotations

from urllib.parse import urlparse

from src.mcp_agent.contracts import SourceTier

_SEC_EDGAR_HOSTS: frozenset[str] = frozenset({"sec.gov"})
_DART_HOSTS: frozenset[str] = frozenset({"dart.fss.or.kr", "opendart.fss.or.kr"})
_EDINET_HOSTS: frozenset[str] = frozenset({"disclosure.edinet-fsa.go.jp", "edinet-fsa.go.jp"})

FILING_HOST_ALLOWLIST: frozenset[str] = _SEC_EDGAR_HOSTS | _DART_HOSTS | _EDINET_HOSTS

# Explicit, empty by default. Extend only via an approved backend config change.
GOVERNMENT_REGULATOR_DOMAIN_ALLOWLIST: frozenset[str] = frozenset()

_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})


def normalize_domain(url: str) -> str | None:
    """Lower-cased hostname with a leading 'www.' stripped; None for anything
    that is not an absolute http(s) URL with a host."""
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    return host[4:] if host.startswith("www.") else host


def domain_matches(domain: str, allowlisted: str) -> bool:
    allowlisted = allowlisted.lower()
    return domain == allowlisted or domain.endswith("." + allowlisted)


def _matches_any(domain: str, allowlist: frozenset[str]) -> bool:
    return any(domain_matches(domain, entry) for entry in allowlist)


def classify_source_url(url: str, issuer_ir_domain: str | None = None) -> SourceTier:
    domain = normalize_domain(url)
    if domain is None:
        return SourceTier.UNKNOWN
    if _matches_any(domain, _SEC_EDGAR_HOSTS):
        return SourceTier.SEC_EDGAR
    if _matches_any(domain, _DART_HOSTS):
        return SourceTier.DART
    if _matches_any(domain, _EDINET_HOSTS):
        return SourceTier.EDINET
    if _matches_any(domain, GOVERNMENT_REGULATOR_DOMAIN_ALLOWLIST):
        return SourceTier.GOVERNMENT_REGULATOR
    if issuer_ir_domain:
        ir = normalize_domain(issuer_ir_domain if "://" in issuer_ir_domain else f"https://{issuer_ir_domain}")
        if ir and domain_matches(domain, ir):
            return SourceTier.ISSUER_IR
    return SourceTier.UNKNOWN


def is_approved_official_domain(url: str, issuer_ir_domain: str | None = None) -> bool:
    return classify_source_url(url, issuer_ir_domain) is not SourceTier.UNKNOWN
