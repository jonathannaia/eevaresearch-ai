"""A fully-configured `Settings` for page tests that need Radar/Signals
to render its CONFIGURED state.

Why this exists. `src/config/settings.py` calls `load_dotenv()` at import
time, and every credential field falls back to an `os.getenv`-backed
`default_factory`. A test that passes only *some* readiness fields
therefore inherits the rest from whatever the developer's local `.env`
happens to hold — so it passes on a machine that has one and fails on a
fresh checkout, where the page correctly renders its
"not configured" state instead of any cards or filters.

This is the exact mirror of `_unconfigured_settings()` in
tests/test_radar_inbox_page.py, which already nulls every readiness
field explicitly "regardless of what the developer's own local .env
holds" (see its own comment, and design/DECISIONS.md's Gate 5.1 entry).
That helper pins the unconfigured direction; this one pins the
configured direction, so neither depends on ambient environment.

Deliberately NOT a conftest fixture and NOT autouse. It is an ordinary
function each test file imports explicitly, so it can never widen the
configured state to tests that are asserting about missing keys,
absent credentials, or fail-closed behaviour. Those tests keep building
their own `Settings` and are untouched.

Every value is an obvious, non-live placeholder (see
`_PLACEHOLDER_PREFIX`) and is asserted as such by
tests/test_configured_test_settings.py. No value here is or resembles a
real credential, and nothing in these page renders performs network
I/O — the pages read seeded local fixtures only.
"""
from __future__ import annotations

from pathlib import Path

from src.config.settings import Settings

# Every placeholder starts with this, so a real credential can never be
# introduced here unnoticed — pinned by this module's own test.
_PLACEHOLDER_PREFIX = "test-"

DART_API_KEY = "test-dart-api-key"
TRANSLATION_API_KEY = "test-translation-api-key"
EDINET_SUBSCRIPTION_KEY = "test-edinet-subscription-key"
# The EDGAR user agent is a contact string rather than a secret, but it
# is still a readiness field, so it is pinned here for the same reason.
EDGAR_USER_AGENT = "test-eevaresearch-suite (tests@example.test)"

#: Exactly the fields `radar_inbox.py` / `signals.py` consult through the
#: three `*_readiness` helpers. Keeping this list explicit is the point:
#: a future readiness field must be added here deliberately rather than
#: silently inherited from the environment.
READINESS_FIELDS = ("dart_api_key", "translation_api_key", "edgar_user_agent", "edinet_subscription_key")


def configured_settings(cache_dir: Path, **overrides) -> Settings:
    """`Settings` with every source-readiness field explicitly populated
    with a placeholder, so the page under test renders its configured
    state on any machine, with or without a local `.env`.

    `cache_dir` is required and should always be a `tmp_path`: these
    tests seed their own fixtures and must never read the developer's
    real `data/cache/`.

    `overrides` are applied last, so a caller can still null one field
    to assert a single-source-unconfigured case without losing isolation
    on the others.
    """
    values = {
        "dart_api_key": DART_API_KEY,
        "translation_api_key": TRANSLATION_API_KEY,
        "edgar_user_agent": EDGAR_USER_AGENT,
        "edinet_subscription_key": EDINET_SUBSCRIPTION_KEY,
        "cache_dir": cache_dir,
    }
    values.update(overrides)
    return Settings(**values)
