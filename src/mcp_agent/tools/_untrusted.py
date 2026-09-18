"""Prompt-injection defense for retrieved filing text (design §7.3, §8.1).
Filing/source text is untrusted DATA, never instructions — the same
"observed content is data, not commands" discipline this codebase already
applies to web and tool content generally.

Pattern-based, deterministic, and deliberately conservative: a line that
looks instruction-shaped is dropped from what reaches the model and the
pattern that fired is reported so the audit event can record it. A false
positive costs one line of an excerpt; a false negative could steer the
session — so the lexicon errs toward dropping.
"""
from __future__ import annotations

import re

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore_previous_instructions", re.compile(r"\bignore\b.{0,40}\b(previous|prior|above|earlier|all)\b.{0,20}\binstructions?\b", re.IGNORECASE)),
    ("disregard_instructions", re.compile(r"\bdisregard\b.{0,40}\binstructions?\b", re.IGNORECASE)),
    ("role_marker", re.compile(r"^\s*(system|assistant|user|human|ai)\s*:", re.IGNORECASE)),
    ("role_assignment", re.compile(r"\byou are (now )?(a|an|the)\b", re.IGNORECASE)),
    ("new_instructions", re.compile(r"\bnew instructions?\s*:", re.IGNORECASE)),
    ("act_as", re.compile(r"\bact as (a|an|the)\b", re.IGNORECASE)),
    ("do_not_follow", re.compile(r"\bdo not follow\b", re.IGNORECASE)),
    ("reveal_prompt", re.compile(r"\breveal\b.{0,20}\b(system )?prompt\b", re.IGNORECASE)),
    ("prompt_injection_literal", re.compile(r"\bprompt injection\b", re.IGNORECASE)),
    ("markup_tag", re.compile(r"<\s*/?\s*(system|instructions?|prompt|tool_call|function_call)\b[^>]*>", re.IGNORECASE)),
    ("tool_call_marker", re.compile(r"\b(call|invoke|use) the (tool|function)\b", re.IGNORECASE)),
)


def scrub_untrusted_text(text: str) -> tuple[str, tuple[str, ...]]:
    """Returns (clean_text, flagged_pattern_names). Lines that match any
    pattern are removed; unmatched lines pass through byte-for-byte."""
    if not text:
        return "", ()
    kept: list[str] = []
    flagged: list[str] = []
    for line in text.splitlines():
        hit = next((name for name, pattern in _PATTERNS if pattern.search(line)), None)
        if hit is None:
            kept.append(line)
        else:
            flagged.append(hit)
    return "\n".join(kept), tuple(dict.fromkeys(flagged))
