"""Proposal letters: pre-filled from the prospect, editable, downloadable as
a valid Word file, kept in History, and they don't change the prospect's
status or messaging limits."""

import io
import zipfile
from xml.dom import minidom

from app.collections import prospects
from app.collections.models import Prospect
from tests.collections_factory import make_ops
from tests.conftest import requires_db

pytestmark = requires_db


def _prospect(db, **kw):
    p = Prospect(**{"segment": "school", "org_name": "Greenfield Academy", "city": "Lekki", "address": "12 Admiralty Way",
                    "contact_name": "Mrs Ada Obi", "contact_role": "Principal", "email": "info@greenfield.example",
                    "phone": "+2348031234567", **kw})
    db.add(p)
    db.flush()
    return p


def test_draft_is_prefilled_for_the_recipient(cl_api, cl_db):
    _, h = make_ops(cl_db, role="Ops")
    p = _prospect(cl_db)
    letter = cl_api.get(f"/collections/ops/prospects/{p.id}/letter", headers=h).json()["letter"]
    assert "Greenfield Academy" in letter["subject"] and "school fees" in letter["subject"]
    assert letter["recipient"].startswith("The Head of School\nGreenfield Academy\n12 Admiralty Way\nLekki")
    assert "Mrs Ada Obi" not in letter["body"]  # not confirmed yet, so greet the role
    assert letter["body"].startswith("Dear Head of School,") and letter["body"].endswith("Yours faithfully,")
    assert "20% of confirmed recoveries" in letter["body"] and "parents" in letter["body"].lower()
    assert letter["signoff"].startswith("Olu Ops\nWaterline Collections")
    assert letter["letterhead"][0] == "Waterline Collections"

    p.contact_confirmed = True
    cl_db.flush()
    letter = cl_api.get(f"/collections/ops/prospects/{p.id}/letter", headers=h).json()["letter"]
    assert letter["recipient"].startswith("Mrs Ada Obi\nPrincipal\nGreenfield Academy")
    assert letter["body"].startswith("Dear Mrs Ada Obi,") and letter["body"].endswith("Yours sincerely,")

    estate = _prospect(cl_db, segment="estate", org_name="Palm Court Estate", contact_name=None, address=None)
    letter = cl_api.get(f"/collections/ops/prospects/{estate.id}/letter", headers=h).json()["letter"]
    assert "service charges" in letter["subject"] and letter["recipient"].startswith("The Facility Manager")


def test_edited_letter_downloads_as_word_and_is_kept_in_history(cl_api, cl_db):
    _, h = make_ops(cl_db, role="Ops")
    p = _prospect(cl_db)
    edited = {"date": "12 November 2026", "recipient": "The Bursar\nGreenfield Academy", "subject": "Our proposal & terms",
              "body": "Dear Bursar,\n\nFees\nWe charge 15% <special> rate.\nLine two.\n\nYours faithfully,", "signoff": "Tosin\nWaterline"}
    r = cl_api.post(f"/collections/ops/prospects/{p.id}/letter.docx", headers=h, json=edited)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml")
    assert 'filename="Waterline-proposal-Greenfield-Academy-' in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= set(z.namelist())
    doc = z.read("word/document.xml").decode()
    minidom.parseString(doc)  # well-formed despite & and <
    text = "".join(n.firstChild.data for n in minidom.parseString(doc).getElementsByTagName("w:t") if n.firstChild)
    for piece in ("Waterline Collections", "Our proposal & terms", "We charge 15% <special> rate.", "The Bursar", "Tosin"):
        assert piece in text

    assert cl_api.post(f"/collections/ops/prospects/{p.id}/letter/printed", headers=h, json=edited).json()["ok"]
    d = cl_api.get(f"/collections/ops/prospects/{p.id}", headers=h).json()
    letters = [t for t in d["touches"] if t["channel"] == "Letter"]
    assert len(letters) == 2 and "We charge 15%" in letters[0]["body"]
    assert d["prospect"]["status"] == "New" and prospects.unanswered_count(cl_db, p) == 0
    assert d["blocked"] == {}  # a letter doesn't use up today's message


def test_letter_validation_and_auth(cl_api, cl_db):
    _, h = make_ops(cl_db, role="Ops")
    p = _prospect(cl_db)
    assert cl_api.post(f"/collections/ops/prospects/{p.id}/letter.docx", headers=h, json={"body": "  "}).status_code == 422
    assert cl_api.post(f"/collections/ops/prospects/{p.id}/letter.docx", headers=h,
                       json={"body": "x" * 12001}).status_code == 422
    assert cl_api.get(f"/collections/ops/prospects/{p.id}/letter").status_code == 401
    assert cl_api.get("/collections/ops/prospects/nope/letter", headers=h).status_code == 404
