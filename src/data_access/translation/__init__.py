"""Translation provider abstraction for the Korea DART radar pilot.
DeepL is the first implementation (see deepl_provider.py) — swappable
for Google/Azure/human review later without touching callers. Every
translation is explicitly non-authoritative: the Korean original stays
the source of truth (see src.models.models.Translation)."""
