from __future__ import annotations

from datetime import datetime, timezone

from src.config.settings import Settings
from src.data_access import loaders
from src.data_access.interfaces import ResearchAnswerProvider
from src.models.models import ChatAnswer, ClaimType, EvidenceItem, ResearchClaim, Strength

SEED_FILE = "chat_demo_answers.json"

FALLBACK_ANSWER_TEMPLATE = (
    "Demo mode: Research Chat only has canned answers for the suggested "
    "questions in this foundation phase — there's no live model behind it "
    "yet. This card shows the intended answer format for your question, "
    "not a real analysis of it."
)


def _parse_source(raw: dict) -> EvidenceItem:
    return EvidenceItem(
        id=raw["id"], title=raw["title"], source_name=raw["source_name"], source_type=raw["source_type"],
        published_at=raw["published_at"], retrieved_at=raw["retrieved_at"], excerpt=raw["excerpt"],
        claim_type=ClaimType(raw["claim_type"]), source_url=raw.get("source_url"),
        is_demo=raw.get("is_demo", True), ticker_symbol=raw.get("ticker_symbol"), theme_slug=raw.get("theme_slug"),
        excerpt_original=raw.get("excerpt_original"), document_location=raw.get("document_location", ""),
    )


def _parse_claim(raw: dict) -> ResearchClaim:
    return ResearchClaim(
        text=raw["text"], claim_type=ClaimType(raw["claim_type"]),
        evidence=[_parse_source(e) for e in raw.get("evidence", [])],
    )


def _parse_answer(raw: dict) -> ChatAnswer:
    return ChatAnswer(
        question=raw["question"],
        what_happened=raw["what_happened"],
        why_it_matters=raw["why_it_matters"],
        underappreciated=raw["underappreciated"],
        risks=raw["risks"],
        what_to_watch=raw["what_to_watch"],
        sources=[_parse_source(s) for s in raw.get("sources", [])],
        confidence=Strength(raw["confidence"]),
        freshness=raw["freshness"],
        claim_type=ClaimType(raw.get("claim_type", "Interpretation")),
        is_demo=raw.get("is_demo", True),
        claims=[_parse_claim(c) for c in raw.get("claims", [])],
    )


def _fallback_answer(question: str) -> ChatAnswer:
    now = datetime.now(timezone.utc).isoformat()
    return ChatAnswer(
        question=question,
        what_happened=FALLBACK_ANSWER_TEMPLATE,
        why_it_matters="Not applicable — demo fallback response.",
        underappreciated="Not applicable — demo fallback response.",
        risks="Not applicable — demo fallback response.",
        what_to_watch="Try one of the suggested research questions to see a fully populated demo answer.",
        sources=[],
        confidence=Strength.WEAK,
        freshness="Fresh",
        claim_type=ClaimType.UNCERTAINTY,
        is_demo=True,
    )


class DemoResearchAnswerProvider(ResearchAnswerProvider):
    def __init__(self, settings: Settings):
        self._settings = settings

    def _all(self) -> list[ChatAnswer]:
        raw = loaders.load(self._settings, SEED_FILE, default=[])
        return [_parse_answer(r) for r in raw]

    def get_answer(self, question: str) -> ChatAnswer:
        normalized = question.strip().lower()
        for answer in self._all():
            if answer.question.strip().lower() == normalized:
                return answer
        return _fallback_answer(question)

    def get_suggested_questions(self) -> list[str]:
        return [a.question for a in self._all()]
