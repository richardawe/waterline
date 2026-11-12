"""Proposal letters to prospects (ops → Prospects → a prospect → Proposal letter).

draft() pre-fills the letter from who it's going to (segment wording, the
organisation, a named head only once ops confirmed them, the segment's
current default fee). Ops edit every part except the letterhead, then
download it as Word (.docx, built here with the standard library: new pip
packages can't be installed on the server) or print it / save it as PDF
from the browser. Either way the final text is kept in the prospect's
History as a "Letter" entry. A letter isn't a message we sent, so it doesn't
change the prospect's status or count towards the messaging limits.
"""

import io
import re
import zipfile
from datetime import date
from xml.sax.saxutils import escape

from sqlalchemy.orm import Session

from app.collections import emailer, segments, settings_store
from app.collections.models import Prospect, ProspectTouch
from app.collections.prospects import COMPANY, _audience, greeting

LIMITS = {"date": 60, "recipient": 600, "subject": 200, "body": 12000, "signoff": 600}

_PLURAL = {"school": "schools", "estate": "estates and facility managers"}


class LetterError(ValueError):
    pass


def letterhead(db: Session) -> list[str]:
    return [
        "Waterline Collections",
        f"A service of {COMPANY}",
        "Lagos, Nigeria",
        f"{settings_store.get(db, 'contact_email')} · {settings_store.get(db, 'contact_whatsapp')} · waterline.ng/collections",
    ]


def _fee(db: Session, segment: str) -> str:
    try:
        return segments.describe_fee(segments.default_fee_schedule(db, segment))
    except Exception:  # an unknown or retired segment: fall back to the standard rate
        return "20% of confirmed recoveries"


def _recipient(p: Prospect) -> str:
    lines = []
    if p.contact_confirmed and p.contact_name:
        lines.append(p.contact_name.strip())
        if p.contact_role:
            lines.append(p.contact_role.strip())
    else:
        lines.append({"school": "The Head of School", "estate": "The Facility Manager"}.get(p.segment, "The Manager"))
    lines.append(p.org_name.strip())
    for part in (p.address, p.city):
        part = (part or "").strip()
        if part and part.lower() not in " ".join(lines).lower():
            lines.append(part)
    return "\n".join(lines)


def draft(db: Session, p: Prospect, sender_name: str, today: date) -> dict:
    a = _audience(p)
    named = bool(p.contact_confirmed and p.contact_name)
    who = _PLURAL.get(p.segment, "organisations")
    payers = a["payers"]
    body = (
        f"{greeting(p)},\n\n"
        f"Waterline Collections helps {who} recover {a['owed']} from past terms and periods, without their teams "
        f"having to chase {payers} themselves. This letter sets out how we would work with {p.org_name}.\n\n"
        "How it works\n"
        f"1. You share the list of who owes, as a spreadsheet or one at a time, and check it before anything goes out.\n"
        f"2. We send polite reminders by WhatsApp and email in your name, and offer instalment plans within limits "
        f"you approve.\n"
        f"3. {payers.capitalize()} pay straight into {a['account']}. We never hold your money.\n"
        "4. You see every message, plan and payment in your own client portal.\n\n"
        "How we behave\n"
        f"We never threaten or embarrass anyone, and we only contact the person who owes: never relatives, "
        f"guarantors or employers. Anyone who asks us to stop is not contacted again.\n\n"
        "Fees\n"
        f"There is no setup or monthly fee. Our fee is {_fee(db, p.segment)}, so you pay nothing on balances that "
        "aren't recovered. The balances you have already written off are a good place to start.\n\n"
        "Next steps\n"
        f"If this is of interest, you can sign up at {emailer.site_url(a['page'])} or reply to this letter, and we "
        "can begin with your oldest balances. I would welcome a 15-minute call to answer any questions.\n\n"
        f"Yours {'sincerely' if named else 'faithfully'},"
    )
    signoff = "\n".join([sender_name, "Waterline Collections", COMPANY])
    return {
        "letterhead": letterhead(db), "date": f"{today.day} {today:%B %Y}", "recipient": _recipient(p),
        "subject": f"Proposal: recovering {a['owed']} for {p.org_name}", "body": body, "signoff": signoff,
    }


def clean(data: dict) -> dict:
    out = {}
    for k, limit in LIMITS.items():
        v = str(data.get(k) or "").replace("\r\n", "\n").strip()
        if len(v) > limit:
            raise LetterError(f"The {k} is too long ({limit} characters max).")
        out[k] = v
    if not out["body"]:
        raise LetterError("The letter is empty.")
    return out


def as_text(db: Session, letter: dict) -> str:
    parts = ["\n".join(letterhead(db)), letter["date"], letter["recipient"]]
    if letter["subject"]:
        parts.append(letter["subject"])
    parts += [letter["body"], letter["signoff"]]
    return "\n\n".join(x for x in parts if x)


def record(db: Session, p: Prospect, letter: dict, how: str, ops_user_id: str) -> ProspectTouch:
    touch = ProspectTouch(prospect_id=p.id, channel="Letter", template_key="proposal", subject=letter["subject"][:300] or None,
                          body=f"[{how}]\n\n" + as_text(db, letter), status="Logged", ops_user_id=ops_user_id)
    db.add(touch)
    db.flush()
    return touch


def filename(p: Prospect, today: date) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", p.org_name).strip("-")[:60] or "prospect"
    return f"Waterline-proposal-{slug}-{today.isoformat()}.docx"


# ---------------------------------------------------------------- .docx

_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_HEADING = re.compile(r"^(How it works|How we behave|Fees|Next steps)$")


def _run(text: str, *, bold=False, size=22, color=None) -> str:
    props = "<w:rFonts w:ascii=\"Arial\" w:hAnsi=\"Arial\" w:cs=\"Arial\"/>" + ("<w:b/>" if bold else "") + \
            (f"<w:color w:val=\"{color}\"/>" if color else "") + f"<w:sz w:val=\"{size}\"/><w:szCs w:val=\"{size}\"/>"
    return f"<w:r><w:rPr>{props}</w:rPr><w:t xml:space=\"preserve\">{escape(_BAD_XML.sub('', text))}</w:t></w:r>"


def _para(runs: str, *, after=0, align=None) -> str:
    ppr = f"<w:spacing w:before=\"0\" w:after=\"{after}\" w:line=\"276\" w:lineRule=\"auto\"/>" + \
          (f"<w:jc w:val=\"{align}\"/>" if align else "")
    return f"<w:p><w:pPr>{ppr}</w:pPr>{runs}</w:p>"


def _block(text: str, *, after_last=240, bold_headings=False) -> str:
    """Blank lines separate paragraphs; single line breaks stay as line breaks."""
    out = []
    for chunk in re.split(r"\n\s*\n", text.strip()):
        lines = chunk.split("\n")
        runs = []
        for i, line in enumerate(lines):
            if i:
                runs.append("<w:r><w:br/></w:r>")
            runs.append(_run(line, bold=bool(bold_headings and _HEADING.match(line.strip()))))
        out.append(_para("".join(runs), after=after_last))
    return "".join(out)


def to_docx(db: Session, letter: dict) -> bytes:
    head = letterhead(db)
    body = [_para(_run(head[0], bold=True, size=32, color="0F6B4F"))]
    body += [_para(_run(line, size=18, color="555555")) for line in head[1:-1]]
    body.append(_para(_run(head[-1], size=18, color="555555"), after=480))
    body.append(_para(_run(letter["date"]), after=240))
    if letter["recipient"]:
        body.append(_block(letter["recipient"]))
    if letter["subject"]:
        body.append(_para(_run(letter["subject"], bold=True), after=240))
    body.append(_block(letter["body"], bold_headings=True))
    if letter["signoff"]:
        body.append(_para("<w:r><w:br/></w:r>"))
        body.append(_block(letter["signoff"]))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + "".join(body) +
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'  # A4
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="567" w:footer="567" w:gutter="0"/>'
        '</w:sectPr></w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()
