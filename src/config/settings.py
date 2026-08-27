"""App-wide configuration for the foundation build.

Phase 1 needs no environment variables to run — everything is demo data
read from data/seed/. The settings that exist are here so Phase 2 has a
single, already-wired place to add real configuration (data mode, provider
keys) without touching UI code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

APP_VERSION = "0.1.0-foundation"
APP_NAME = "EevaResearch AI"

# Private-beta access foundation, Phase 1 (configuration + gating only — see
# design/DECISIONS.md; no Google/OIDC login is implemented yet). Defaults
# preserve today's fully-open local-dev behavior: absent, blank, or an
# unrecognized value always resolves to disabled/empty, never enabled.
_PRIVATE_BETA_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _parse_beta_auth_enabled(var_name: str) -> bool:
    return (os.getenv(var_name) or "").strip().lower() in _PRIVATE_BETA_TRUE_VALUES


def _parse_beta_allowed_emails(var_name: str) -> frozenset[str]:
    raw = os.getenv(var_name) or ""
    return frozenset(email for email in (part.strip().lower() for part in raw.split(",")) if email)


@dataclass(frozen=True)
class Settings:
    app_version: str = APP_VERSION
    app_name: str = APP_NAME
    # Always "demo" in this phase — no live data mode exists yet. Kept as a
    # field (not a hardcoded string in the UI) so Phase 2 can introduce a
    # real "live" mode without a UI rewrite.
    data_mode: str = field(default_factory=lambda: os.getenv("EDGE_DATA_MODE", "demo"))
    seed_data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "seed")
    # Korea DART radar pilot — narrowly scoped, not a general "live mode"
    # switch (see design/DECISIONS.md). None means unconfigured; callers
    # must show a clear missing-key state rather than guessing or crashing.
    dart_api_key: str | None = field(default_factory=lambda: os.getenv("EDGE_DART_API_KEY") or None)
    translation_api_key: str | None = field(default_factory=lambda: os.getenv("EDGE_TRANSLATION_API_KEY") or None)
    # SEC EDGAR radar pilot — not a secret (EDGAR needs no API key at all),
    # but still a required, non-empty identifying contact string per SEC's
    # own access policy. None means unconfigured; callers must fail closed
    # with a typed config error rather than sending an unidentified request
    # EDGAR would reject anyway. Never logged/printed/exposed — only ever
    # checked for presence (see edgar_service.edgar_readiness).
    edgar_user_agent: str | None = field(default_factory=lambda: os.getenv("EDGE_EDGAR_USER_AGENT") or None)
    # EDINET (Japan) radar pilot — planning Gate 1, fixture-only. This
    # gate never reads/validates the real value beyond presence-checking
    # (see edinet_service.edinet_readiness); no live request uses it yet.
    # Exactly one env var, no competing aliases, per the Gate 1 brief.
    edinet_subscription_key: str | None = field(default_factory=lambda: os.getenv("EDGE_EDINET_SUBSCRIPTION_KEY") or None)
    # EDGAR issuer-discovery preview harness (Phase B — dormant, design/
    # DECISIONS.md). Disabled by default, same "unset/blank/unrecognized ->
    # disabled" parsing as private_beta_auth_enabled/remote_cache_enabled
    # below. Nothing in the app reads this yet — src/data_access/edgar/
    # discovery_service.py takes `discovery_enabled` as a plain bool
    # argument rather than importing Settings itself (keeping that module
    # dependency-free), so this flag has no call site in this phase; it
    # exists so a future, separately-approved wiring step has a ready,
    # already-fail-closed-by-default gate to read.
    edgar_discovery_enabled: bool = field(default_factory=lambda: _parse_beta_auth_enabled("EDGE_EDGAR_DISCOVERY_ENABLED"))
    # Durable-State Phase 4M-0 (design/DECISIONS.md) — the master switch
    # for the standalone continuous worker (scripts/radar_worker.py)
    # only. Disabled by default, same "unset/blank/unrecognized ->
    # disabled" parsing as every other flag on this class. The Streamlit
    # dashboard never reads this to decide whether to scan — it never
    # scans on page render regardless of this value; only the worker
    # process's own startup checks it.
    radar_live_scan_enabled: bool = field(default_factory=lambda: _parse_beta_auth_enabled("EDGE_RADAR_LIVE_SCAN_ENABLED"))
    # Conservative default (hourly) — this pilot's tracked-issuer universe
    # (25 EDGAR + 2 DART + 5 EDINET) does not need sub-hourly polling, and
    # EDGAR's own existing client-side throttle (0.5s/request minimum
    # interval, src/data_access/edgar/client.py) already bounds per-scan
    # request volume independently of this setting.
    radar_scan_interval_minutes: int = field(
        default_factory=lambda: int(os.getenv("EDGE_RADAR_SCAN_INTERVAL_MINUTES") or "60")
    )
    # Conservative autonomous-publication gate for the narrow, evidence-complete
    # EDGAR policy path. Disabled unless explicitly enabled in the deployment
    # environment; this field only parses the flag and never performs I/O.
    edgar_auto_publish_enabled: bool = field(
        default_factory=lambda: _parse_beta_auth_enabled("EDGE_EDGAR_AUTO_PUBLISH_ENABLED")
    )
    cache_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "cache")
    # Durable-State Phase 1 (dormant — see src/data_access/state_db/).
    # "json" (the default, used whenever this var is unset/blank/
    # unrecognized) keeps every existing JSON-cache-backed code path
    # exactly as-is; "sqlite" is a local-development/test-only opt-in —
    # nothing in the app actually branches on this value yet, since no
    # pipeline/container wiring is part of this phase. A SQLite file is
    # NOT a hosted-durable-storage answer on its own — treat it as
    # ephemeral on Streamlit Community Cloud unless a persistent volume
    # is separately verified and explicitly approved (see
    # design/DECISIONS.md).
    db_backend: str = field(default_factory=lambda: (os.getenv("EDGE_DB_BACKEND") or "json").strip().lower())
    # Local filesystem path to the SQLite database file. Deliberately a
    # dedicated EDGE_STATE_DB_* name, not EDGE_DB_PATH — this repo's real
    # local .env already defines an unrelated, pre-"foundation rebuild"
    # EDGE_DB_PATH (see design/DECISIONS.md); this field never reads that
    # name and never will (no backward-compatible alias). None (the
    # default) means unconfigured; a caller choosing db_backend="sqlite"
    # is responsible for providing one — this field never invents a
    # default path itself, the same "None means not configured, never
    # guessed" convention every other optional Settings field follows.
    state_db_path: Path | None = field(
        default_factory=lambda: Path(os.getenv("EDGE_STATE_DB_PATH")) if os.getenv("EDGE_STATE_DB_PATH") else None
    )
    # Durable-State Phase 4B (dormant in production — see
    # src/data_access/postgres_state_db/). Same dedicated EDGE_STATE_DB_*
    # naming as state_db_path above, for the same reason. This field
    # itself still only reads the env var for presence — no parsing or
    # validation happens here, exactly like state_db_path. Real use is
    # confined to backend_factory.py's db_backend="postgres" branch,
    # which requires this to be an explicit, non-empty DSN before
    # connecting (see that module's own docstring) — and, this phase,
    # that branch is reachable only via direct backend_factory calls in
    # local, synthetic tests against a disposable local Postgres target,
    # never from get_settings()/any real service entry point, never a
    # hosted database, never a secret.
    state_db_url: str | None = field(default_factory=lambda: os.getenv("EDGE_STATE_DB_URL") or None)
    # Durable-State Phase 4M-0 — deliberately separate from db_backend/
    # state_db_url above, and read only by scripts/radar_worker.py, never
    # by get_settings()'s own ambient use in app.py/container.py. This
    # keeps a Streamlit Secrets misconfiguration from ever making the
    # *dashboard* silently start writing to a worker's database — the
    # worker's own backend/DSN pair lives in its own, separate deployment
    # environment only (see design/DECISIONS.md and
    # design/RADAR_WORKER_DEPLOYMENT.md). None means unconfigured; the
    # worker fails closed rather than guessing json/sqlite/postgres.
    radar_worker_db_backend: str | None = field(
        default_factory=lambda: (os.getenv("EDGE_RADAR_WORKER_DB_BACKEND") or "").strip().lower() or None
    )
    radar_worker_state_db_url: str | None = field(
        default_factory=lambda: os.getenv("EDGE_RADAR_WORKER_STATE_DB_URL") or None
    )
    # SQLite counterpart to radar_worker_state_db_url above — local/test
    # only (design/RADAR_WORKER_DEPLOYMENT.md): a single-host, single-
    # process worker may use this to validate the continuous-loop
    # behavior without Postgres, but it is never the recommended
    # deployed configuration for a separate dashboard+worker pair.
    radar_worker_state_db_path: Path | None = field(
        default_factory=lambda: (
            Path(os.getenv("EDGE_RADAR_WORKER_STATE_DB_PATH")) if os.getenv("EDGE_RADAR_WORKER_STATE_DB_PATH") else None
        )
    )
    # Private-beta access foundation, Phase 1 — disabled by default so every
    # existing page keeps working with no configuration at all. The
    # allowlist alone is authorization, not authentication (see
    # src/ui/beta_gate.py); no identity/sign-in exists yet this phase.
    private_beta_auth_enabled: bool = field(default_factory=lambda: _parse_beta_auth_enabled("EDGE_PRIVATE_BETA_AUTH_ENABLED"))
    private_beta_allowed_emails: frozenset[str] = field(default_factory=lambda: _parse_beta_allowed_emails("EDGE_PRIVATE_BETA_ALLOWED_EMAILS"))
    # R2 remote-cache sync (dormant infrastructure — see
    # src/data_access/remote_cache/ — nothing in the app reads/writes
    # through these yet). Disabled by default so every existing page and
    # pipeline keeps working with no configuration at all. None means
    # unconfigured for each individual field; remote_cache_available()
    # requires r2_endpoint/r2_access_key_id/r2_secret_access_key/r2_bucket
    # to all be present (r2_account_id is optional metadata only — see
    # r2_client.py's r2_settings_complete()), never a partial config of
    # the four required fields. Never logged/printed/exposed — only ever
    # checked for presence, same discipline as dart_api_key/edgar_user_agent above.
    remote_cache_enabled: bool = field(default_factory=lambda: _parse_beta_auth_enabled("EDGE_REMOTE_CACHE_ENABLED"))
    r2_account_id: str | None = field(default_factory=lambda: os.getenv("EDGE_R2_ACCOUNT_ID") or None)
    r2_access_key_id: str | None = field(default_factory=lambda: os.getenv("EDGE_R2_ACCESS_KEY_ID") or None)
    r2_secret_access_key: str | None = field(default_factory=lambda: os.getenv("EDGE_R2_SECRET_ACCESS_KEY") or None)
    r2_bucket: str | None = field(default_factory=lambda: os.getenv("EDGE_R2_BUCKET") or None)
    r2_endpoint: str | None = field(default_factory=lambda: os.getenv("EDGE_R2_ENDPOINT") or None)


def get_settings() -> Settings:
    return Settings()


def demo_last_updated_label() -> str:
    """A clearly-labeled mock 'last updated' value for the global status
    banner — never a real data-refresh timestamp in this phase."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d (demo session)")
