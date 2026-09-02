"""Loads the curated, human-edited reference facts used to ground blog
generation so the writer model isn't inventing rates/dates/institution names
from nothing. Small, git-tracked JSON — not a DB table, not pgvector (see
AGENTS.md on why pgvector is deliberately unresolved in production)."""

import json
from functools import lru_cache
from pathlib import Path

FACTS_DIR = Path(__file__).resolve().parent / "facts"


@lru_cache
def _all_facts() -> list[dict]:
    facts: list[dict] = []
    for path in sorted(FACTS_DIR.glob("*.json")):
        facts.extend(json.loads(path.read_text()))
    return facts


def relevant_facts(category: str, keywords: str | None, limit: int = 8) -> list[dict]:
    """Ranks curated facts by keyword/category overlap with a topic and
    returns the top matches. Deliberately simple substring scoring — the
    corpus is small enough that this beats the complexity of embeddings."""
    query_terms = {t.strip().lower() for t in (keywords or "").split(",") if t.strip()}
    query_terms.add(category.strip().lower())

    scored: list[tuple[int, dict]] = []
    for fact in _all_facts():
        fact_terms = {k.lower() for k in fact.get("keywords", [])}
        fact_terms.add(fact.get("category", "").lower())
        score = len(query_terms & fact_terms)
        if fact.get("category", "").lower() == category.strip().lower():
            score += 2
        if score > 0:
            scored.append((score, fact))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [fact for _, fact in scored[:limit]]
