"""Prompt construction for the writer and QA passes. Both models are
instructed to return strict JSON so `generator.py` can parse deterministically
rather than scraping free-form prose."""

import json

WRITER_OUTPUT_SCHEMA = (
    '{"title": str, "meta_description": str (<=155 chars), "excerpt": str (1-2 sentences), '
    '"tags": [str, ...] (3-6 tags), "faq": [{"question": str, "answer": str}, ...] (3-5 entries), '
    '"news_section_included": bool, "body_markdown": str}'
)

QA_OUTPUT_SCHEMA = '{"verdict": "pass" | "fail", "issues": [str, ...]}'

WRITER_SYSTEM_PROMPT = """You are the editorial writer for the Waterline finance blog, which publishes
factual, plain-English articles about credit, loans and personal/SME finance
in Nigeria and across Africa, for readers who are not finance professionals.

Hard rules, no exceptions:
1. You may state a specific interest rate, date, statistic, or name a specific
   institution/regulation ONLY if it appears in the "Reference facts" or
   "Recent news" sections below. Otherwise, write in general/structural terms
   (e.g. "rates vary by lender and loan type" rather than inventing a number).
2. This is general education, not personalized financial or legal advice.
   Never tell the reader what they personally should do; explain how things
   work and what factors matter.
3. Structure for both search engines and AI answer engines:
   - Open with a direct, 2-4 sentence answer to the article's core question
     (a reader or an AI summarizer should get the key point from paragraph one).
   - Use H2/H3 markdown headings to break up the body.
   - If, and only if, the "Recent news" section below contains relevant items,
     include an H2 section titled "Recent developments" that references those
     specific items by title, source and date with a markdown link. If no
     relevant news was supplied, omit this section entirely — never invent
     news.
   - End with a short FAQ-style wrap-up matching the "faq" field you return.
4. Aim for 700-1100 words in body_markdown.
5. Output ONLY a single JSON object matching this shape, no prose before or
   after, no markdown code fences: """ + WRITER_OUTPUT_SCHEMA


def build_writer_prompt(
    topic_prompt: str,
    category: str,
    facts: list[dict],
    news_items: list[dict],
    qa_feedback: list[str] | None = None,
) -> str:
    facts_block = "\n".join(f"- {f['fact']} (source: {f.get('source', 'n/a')}, as of {f.get('as_of', 'n/a')})" for f in facts) or "(none supplied)"
    news_block = (
        "\n".join(
            f"- \"{n['title']}\" — {n.get('source', 'unknown source')}, {n.get('published') or 'date unknown'} — {n['link']}"
            for n in news_items
        )
        or "(no relevant recent news found — omit the Recent developments section)"
    )

    parts = [
        f"Topic category: {category}",
        f"Write an article about: {topic_prompt}",
        "",
        "Reference facts (only source for specific claims):",
        facts_block,
        "",
        "Recent news (only source for the Recent developments section):",
        news_block,
    ]
    if qa_feedback:
        parts += [
            "",
            "A previous draft of this article failed editorial QA for these reasons — fix them in this rewrite:",
            "\n".join(f"- {issue}" for issue in qa_feedback),
        ]
    return "\n".join(parts)


QA_SYSTEM_PROMPT = """You are the fact-checking editor for the Waterline finance blog. You review
a drafted article against the same reference facts and news items the writer
was given, and against these rules:

1. Reject (fail) if the article states any specific rate, date, statistic, or
   names a specific institution/regulation that does NOT appear in the
   supplied reference facts or news items.
2. Reject if the "Recent developments" section (if present) cites a news item
   not present in the supplied news list, or misstates what a cited item says.
3. Reject if the article gives personalized financial/legal advice ("you
   should...") rather than general education.
4. Reject if meta_description exceeds 160 characters, or fewer than 3 FAQ
   entries are present, or body_markdown is under 500 words.
5. Reject if the title duplicates or near-duplicates an existing published
   title (given below).
6. Otherwise pass.

Output ONLY a single JSON object matching this shape, no prose before or
after, no markdown code fences: """ + QA_OUTPUT_SCHEMA


def build_qa_prompt(draft: dict, facts: list[dict], news_items: list[dict], existing_titles: list[str]) -> str:
    return "\n".join(
        [
            "Reference facts the writer was given:",
            "\n".join(f"- {f['fact']}" for f in facts) or "(none)",
            "",
            "News items the writer was given:",
            "\n".join(f"- {n['title']} ({n.get('source', 'unknown')})" for n in news_items) or "(none)",
            "",
            "Existing published titles (for duplicate check):",
            "\n".join(f"- {t}" for t in existing_titles) or "(none)",
            "",
            "Draft to review (JSON):",
            json.dumps(draft, ensure_ascii=False),
        ]
    )
