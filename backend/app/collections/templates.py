"""Fixed, Waterline-owned message templates. Clients can never write message
text; only Waterline Admin can add a new template version, and every
version is checked against the conduct rules before it's saved."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collections.models import MessageTemplate

STOP_LINE = "Reply STOP to stop messages."

DEFAULT_TEMPLATES = {
    "INTRO": (
        "Hello {{debtor_first_name}}, this is a message from {{client_name}} via Waterline. Our records show an "
        "outstanding balance of {{balance}} for {{description}}. You can view the details, pay, or choose a payment "
        "plan here: {{link}}. If you've already paid or think this is wrong, let us know on the same link. "
        "Reply STOP to stop messages."
    ),
    "REMINDER": (
        "Hello {{debtor_first_name}}, a quick reminder from {{client_name}} via Waterline about the balance of "
        "{{balance}} for {{description}}. {{discount_line}} Details and payment options: {{link}}. "
        "Reply STOP to stop messages."
    ),
    # The spec drafts of the next three omitted "via Waterline" (and, for
    # DISPUTE_RESOLVED, the STOP line), which the same spec requires on every
    # message — added with the smallest wording change so the rule has no
    # exceptions.
    "PLAN_OFFER": (
        "Hello {{debtor_first_name}}, this is {{client_name}} via Waterline. We understand things can be tight. "
        "You can clear the {{balance}} balance in smaller instalments. Choose a plan that works for you: {{link}}. "
        "Reply STOP to stop messages."
    ),
    "INSTALMENT_DUE": (
        "Hello {{debtor_first_name}}, your next instalment of {{instalment_amount}} to {{client_name}} via Waterline "
        "is due on {{due_date}}. Pay and upload your receipt here: {{link}}. Reply STOP to stop messages."
    ),
    "DISPUTE_RESOLVED": (
        "Hello {{debtor_first_name}}, {{client_name}} has reviewed your query about {{description}} via Waterline. "
        "See the outcome and your updated balance here: {{link}}. Reply STOP to stop messages."
    ),
}

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


def check_template(body: str) -> None:
    lowered = body.lower()
    for pattern in FORBIDDEN_PATTERNS:
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


def ensure_defaults(db: Session) -> None:
    for key, body in DEFAULT_TEMPLATES.items():
        exists = db.execute(
            select(MessageTemplate.id).where(MessageTemplate.key == key, MessageTemplate.language == "en")
        ).first()
        if not exists:
            db.add(MessageTemplate(key=key, language="en", version=1, body=body, is_active=True, created_by="system"))
    db.flush()


def active(db: Session, key: str, language: str = "en") -> MessageTemplate:
    tpl = db.execute(
        select(MessageTemplate)
        .where(MessageTemplate.key == key, MessageTemplate.language == language, MessageTemplate.is_active.is_(True))
        .order_by(MessageTemplate.version.desc())
    ).scalars().first()
    if tpl is None:
        ensure_defaults(db)
        return active(db, key, language)
    return tpl


def new_version(db: Session, key: str, body: str, language: str, actor_id: str) -> MessageTemplate:
    if key not in DEFAULT_TEMPLATES:
        raise TemplateRejected(f"Unknown template key {key}.")
    if language not in LANGUAGES:
        raise TemplateRejected(f"Language {language} is not enabled yet.")
    check_template(body)
    ensure_defaults(db)
    current = db.execute(
        select(MessageTemplate).where(MessageTemplate.key == key, MessageTemplate.language == language)
    ).scalars().all()
    for row in current:
        row.is_active = False
    version = max((r.version for r in current), default=0) + 1
    tpl = MessageTemplate(key=key, language=language, version=version, body=body, is_active=True, created_by=actor_id)
    db.add(tpl)
    db.flush()
    return tpl


def render(body: str, values: dict[str, str]) -> str:
    text = re.sub(r"{{\s*(\w+)\s*}}", lambda m: values.get(m.group(1), ""), body)
    return re.sub(r"\s{2,}", " ", text).strip()
