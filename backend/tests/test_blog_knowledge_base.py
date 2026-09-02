from app.blog import knowledge_base


def test_relevant_facts_matches_by_keyword():
    facts = knowledge_base.relevant_facts("credit-score", "crc, credit bureau")
    assert any(f["id"] == "credit-bureaus-nigeria" for f in facts)


def test_relevant_facts_matches_by_category_even_without_keyword_hit():
    facts = knowledge_base.relevant_facts("pan-africa", "some unrelated term")
    assert any(f["category"] == "pan-africa" for f in facts)


def test_relevant_facts_respects_limit():
    facts = knowledge_base.relevant_facts("regulation", "cbn, fccpc, ndpa, digital lending", limit=2)
    assert len(facts) <= 2


def test_relevant_facts_returns_empty_for_nonsense_query():
    facts = knowledge_base.relevant_facts("nonexistent-category", "zzz-no-match-zzz")
    assert facts == []
