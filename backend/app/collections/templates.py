"""Fixed, Waterline-owned message templates, one set per client segment.
Clients can never write message text; only Waterline Admin can add a new
template version, and every version is checked against the global conduct
rules plus the segment's own banned wording before it's saved."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collections.models import MessageTemplate

STOP_LINE = "Reply STOP to stop messages."

# Template bodies are defined per segment in app.collections.segments; this
# module owns the conduct check, versioning and rendering.

PLACEHOLDERS = {
    "debtor_first_name", "client_name", "description", "balance", "link",
    "discount_line", "instalment_amount", "due_date",
}

LANGUAGES = {"en"}  # Pidgin, Yoruba, Hausa, Igbo to follow (phase 2)

# Conduct rules: no threats, enforcement agencies, blacklisting, children's
# schooling consequences, shaming, or disclosure to others.
FORBIDDEN_PATTERNS = [
    r"\bpolice\b", r"\bcourts?\b", r"\barrest", r"\bblacklist", r"\bblack-list", r"\bjail\b", r"\bprison\b",
    r"\blawyers?\b", r"\blegal action\b", r"\bsue\b", r"\blawsuit\b", r"\befcc\b", r"\bcredit bureau\b",
    r"\bresults?\b", r"\bexams?\b", r"\bexamination", r"\bexclu", r"\bexpel", r"\bsend (him|her|them|your child) home\b",
    r"\bshame", r"\bdisgrace", r"\bembarrass", r"\bdefaulters? list\b", r"\bname and shame\b",
    r"\bthreat", r"\bconsequences\b", r"\bfinal warning\b", r"\bor else\b",
    r"\b(share|shared|sharing|publish|tell|inform)\b.{0,40}\b(neighbou?rs?|family|employer|friends|others|public|everyone)\b",
]


class TemplateRejected(ValueError):
    pass


def check_template(body: str, segment=None) -> None:
    lowered = body.lower()
    patterns = FORBIDDEN_PATTERNS + list(getattr(segment, "extra_forbidden", ()) or ())
    for pattern in patterns:
        if re.search(pattern, lowered):
            raise TemplateRejected(f"Template contains forbidden wording (matched '{pattern}').")
    unknown = set(re.findall(r"{{\s*(\w+)\s*}}", body)) - PLACEHOLDERS
    if unknown:
        raise TemplateRejected(f"Unknown placeholders: {', '.join(sorted(unknown))}")
    for required, label in (("{{client_name}}", "the client's name"), ("{{link}}", "the payment link")):
        if required not in body:
            raise TemplateRejected(f"Template must include {label} ({required}).")
    if "via Waterline" not in body:
        raise TemplateRejected('Template must include "via Waterline".')
    if STOP_LINE not in body:
        raise TemplateRejected(f'Template must include "{STOP_LINE}"')


def ensure_defaults(db: Session, segment_key: str | None = None) -> None:
    from app.collections import segments

    keys = [segment_key] if segment_key else list(segments.SEGMENTS)
    for seg_key in keys:
        seg = segments.get(seg_key)
        have = set(db.execute(
            select(MessageTemplate.key).where(MessageTemplate.segment == seg_key, MessageTemplate.language == "en")
        ).scalars())
        for key, body in seg.templates.items():
            if key not in have:
                db.add(MessageTemplate(segment=seg_key, key=key, language="en", version=1, body=body,
                                       is_active=True, created_by="system"))
    db.flush()


def active(db: Session, key: str, segment_key: str, language: str = "en") -> MessageTemplate:
    def lookup():
        return db.execute(
            select(MessageTemplate)
            .where(MessageTemplate.segment == segment_key, MessageTemplate.key == key,
                   MessageTemplate.language == language, MessageTemplate.is_active.is_(True))
            .order_by(MessageTemplate.version.desc())
        ).scalars().first()

    tpl = lookup()
    if tpl is None:
        ensure_defaults(db, segment_key)
        tpl = lookup()
    return tpl


def new_version(db: Session, segment_key: str, key: str, body: str, language: str, actor_id: str) -> MessageTemplate:
    from app.collections import segments

    try:
        seg = segments.get(segment_key)
    except KeyError as exc:
        raise TemplateRejected(f"Unknown segment {segment_key}.") from exc
    if key not in segments.TEMPLATE_KEYS:
        raise TemplateRejected(f"Unknown template key {key}.")
    if language not in LANGUAGES:
        raise TemplateRejected(f"Language {language} is not enabled yet.")
    check_template(body, seg)
    ensure_defaults(db, segment_key)
    current = db.execute(
        select(MessageTemplate).where(MessageTemplate.segment == segment_key, MessageTemplate.key == key,
                                      MessageTemplate.language == language)
    ).scalars().all()
    for row in current:
        row.is_active = False
    version = max((r.version for r in current), default=0) + 1
    tpl = MessageTemplate(segment=segment_key, key=key, language=language, version=version, body=body,
                          is_active=True, created_by=actor_id)
    db.add(tpl)
    db.flush()
    return tpl


def render(body: str, values: dict[str, str]) -> str:
    text = re.sub(r"{{\s*(\w+)\s*}}", lambda m: values.get(m.group(1), ""), body)
    return re.sub(r"\s{2,}", " ", text).strip()
