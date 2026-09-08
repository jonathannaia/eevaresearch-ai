# Theme Registry Foundation

## Purpose

`config/eevaresearch_theme_registry.yaml` is EevaResearch's canonical, versioned, machine-readable thematic taxonomy — a granular, multilingual classification vocabulary for later use in autonomous Radar filing/IR/news classification, discovery-query generation, and research-brief context. This document records the foundation phase that made the registry loadable and safely usable: typed models, fail-closed validation, and pure normalization/matching — nothing else.

## Relationship to the existing five-theme dashboard taxonomy

`src/config/ontology.py`'s `PRIMARY_THEMES` (`ai-buildout`, `humanoids`, `space`, `memory`, `photonics`) is the existing, coarse, dashboard-facing grouping — it drives `data/seed/themes.json`, `Issuer.themes`, and `TrackedCompany.themes` today. The new registry's 13 theme IDs (e.g. `ai_infrastructure_and_semiconductors`, `optical_networking_and_data_center_connectivity`) are a materially different, more granular vocabulary, built for a different purpose (autonomous classification/retrieval, not dashboard grouping).

**This foundation phase keeps the two namespaces completely separate.** No file, field, or slug in the existing ontology/dashboard/issuer-registry system is renamed, mapped, or reinterpreted. Nothing in this phase writes to `Issuer.themes`, `TrackedCompany.themes`, or `data/seed/themes.json`. A future, separately-approved phase may define a formal crosswalk between the two — this phase deliberately does not attempt one, following the same discipline `ontology.py`'s own `KNOWN_CATEGORY_CONFLICTS` list already models (record a real disagreement/gap explicitly, never silently resolve it).

## What this phase built

- `config/eevaresearch_theme_registry.yaml` — the registry file itself (author-supplied, tracked, versioned via its own `registry_version` field).
- `src/models/theme_registry.py` — pure, frozen-dataclass types (`Theme`, `ThemeRegistry`, `Geography`, `CrossThemeRelationship`, `MatchingPolicy`, `ClassificationOutputContract`). No parsing or I/O.
- `src/config/theme_registry_loader.py` — the only file-I/O boundary. `load_theme_registry(path)` (raises `ThemeRegistryError` on any problem) and `load_theme_registry_or_none(path)` (never raises — the contract every real future caller should use). Uses `yaml.safe_load` exclusively.
- `src/logic/theme_matching.py` — pure, read-only classification matching. `normalize_text()` (Unicode NFKC + `casefold()`), `match_themes(registry, text) -> tuple[ThemeMatch, ...]`.
- `tests/test_theme_registry_loader.py`, `tests/test_theme_matching.py` — see test plan below.

## Validation contract (fail-closed, never breaks a caller)

`load_theme_registry_or_none()` returns `(None, <sanitized reason code>)` for every failure mode — a missing file, malformed YAML, a non-mapping root, an unsupported `registry_version`, a duplicate theme ID, an unknown `entity_roles` value, an invalid `priority`, more than one `applies_to_all_themes: true` theme, or **an unknown `from`/`to` theme ID in `shared_cross_theme_relationships` (a hard failure, by explicit decision — an authoring error in the registry fails the whole registry closed for classification, rather than silently dropping one relationship)**. The reason code is a short internal identifier — safe to log, never a raw YAML-parser exception, a file path, or anything else unsafe to surface. Any caller that ever wires this registry into something real must treat `None` as "no theme classification available" and continue operating exactly as it does today — this phase adds no such caller.

## Normalization and matching

Unicode NFKC normalization + `casefold()` (not a bare `.lower()`) applied identically to registry aliases (cached once per theme at load time, as `Theme.normalized_aliases`) and to incoming text at match time. Matching is deliberately simple and deterministic — a substring check against normalized text, never fuzzy matching or an embedding/ML classifier. `match_themes()` returns every matching theme, never a single "best" one, matching the registry's own `matching_policy.allow_multi_theme_assignment: true`.

## The strict relevance-vs-materiality boundary

`match_themes()` returns `ThemeMatch` records — theme ID plus which aliases/event-patterns matched. This is classification/retrieval metadata only. Neither `theme_registry_loader.py` nor `theme_matching.py` nor `theme_registry.py` imports `review_actions`, `signal_promotion`, `backend_factory`, `container`, or any `SignalRepository` — proven by an AST-based structural test, not just by inspection. No code added in this phase can construct a `CandidateSignal`, calculate a materiality score, or set `CandidateStatus` to `PUBLISHED`, `MONITORING`, or `DISMISSED`. The existing `record_review_decision()` human-review lifecycle remains the sole route to any of those three statuses, completely unchanged by this phase.

## Non-goals (explicitly out of scope this phase)

Live scanning, IR/RSS ingestion, generic news ingestion, AI-generated research, execution of `discovery_queries`, database migrations, dashboard taxonomy changes, changes to `CandidateSignal`/`FilingEvent` persistence, changes to `signal_promotion.py`, changes to Signals policy, changes to authentication. Every one of these is real, named future work — none of it is built, wired, or assumed by this phase.

## Future plug-in points (named, not built)

1. Tracked issuer/theme assignment — a possible future additive field (e.g. `Issuer.theme_registry_ids`), not a repurposing of `Issuer.themes`.
2. Filings — `edgar_rules.py`/`dart_rules.py` calling `match_themes()` to attach optional, additive classification metadata to a `CandidateSignal`, never touching `.status`.
3. Official IR/RSS collection (a future phase) — `discovery_queries`/`event_patterns` as a relevance filter.
4. Approved news providers (a future phase) — same relationship as #3.
5. Discovered/untracked issuer review — a suggested theme surfaced in a human review queue, mirroring `DISCOVERY_STUBS`' own "surfaced, never auto-promoted" pattern.
6. Multilingual evidence/research output — citing which alias/language triggered a match, never inventing a translation.
