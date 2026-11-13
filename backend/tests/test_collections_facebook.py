"""Facebook Page posts: connection is Admin-only with the token never
returned; Ops publish directly or via Admin approval (Config switch);
images are uploaded then attached; scheduling uses Facebook's own
scheduling; posts naming a debtor or giving their phone number are refused;
every step is audited."""

from datetime import timedelta

from sqlalchemy import select

from app.collections import crypto, facebook, settings_store
from app.collections.models import AuditLog, DebtorAccount, FbPost
from app.collections.timeutil import to_lagos, utcnow
from tests.collections_factory import make_account, make_client, make_ops
from tests.conftest import requires_db

pytestmark = requires_db

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class FakeGraph:
    def __init__(self):
        self.calls = []
        self.tokens = []
        self.fail = None
        self.pages = [{"id": "61550000000000", "name": "Waterline Collections",
                       "link": "https://facebook.com/waterlinecollections", "access_token": "EAAPageOwn-token"}]

    def __call__(self, cfg, method, path, payload=None, *, files=None):
        self.calls.append((method, path, payload, files))
        if self.fail and path.endswith(self.fail):
            raise facebook.FacebookError("(#100) Something went wrong", 100)
        if path.startswith("/me/accounts"):
            self.tokens.append(cfg["access_token"])
            return {"data": self.pages}
        if "metadata=1" in path:
            return {"id": path[1:].split("?")[0], "name": "waterline-collections", "metadata": {"type": "user"}}
        self.tokens.append(cfg["access_token"])
        if path.endswith("/photos"):
            return {"id": "photo1"}
        if path.endswith("/feed"):
            return {"id": "123_456"}
        if "fields=permalink_url" in path and "is_published" not in path:
            return {"permalink_url": "https://facebook.com/123/posts/456"}
        if "is_published" in path:
            return {"is_published": True, "permalink_url": "https://facebook.com/123/posts/456"}
        if method == "DELETE":
            return {"success": True}
        raise AssertionError(path)


def _connect(db, monkeypatch) -> FakeGraph:
    fake = FakeGraph()
    monkeypatch.setattr(facebook, "_call", fake)
    facebook.save_settings(db, {"page_id": "61550000000000", "access_token": "EAAPage-secret"}, "admin")
    db.flush()
    return fake


def _post(cl_api, h, action="draft", **fields):
    data = {"message": "Unpaid school fees from past terms? We can help.", "action": action, **fields}
    files = {"image": ("pic.png", fields.pop("image"), "image/png")} if "image" in fields else None
    data.pop("image", None)
    return cl_api.post("/collections/ops/facebook/posts", headers=h, data=data, files=files)


def _later(minutes=120):
    return to_lagos(utcnow() + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M")


def test_page_connection_is_admin_only_and_token_never_returned(cl_api, cl_db, monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(facebook, "_call", fake)
    _, ops_h = make_ops(cl_db, role="Ops")
    _, admin_h = make_ops(cl_db, role="Admin")
    url = "/collections/ops/channels/facebook"
    body = {"page_id": "61550000000000", "access_token": "EAAPage-secret"}
    assert cl_api.put(url, headers=ops_h, json=body).status_code == 403
    r = cl_api.put(url, headers=admin_h, json=body)
    assert r.status_code == 200 and r.json()["connected"] and r.json()["page_name"] == "Waterline Collections"
    assert r.json()["check_error"] is None and r.json()["note"] is None
    for text in (r.text, cl_api.get(url, headers=admin_h).text):
        assert "EAAPage-secret" not in text and "EAAPageOwn-token" not in text
    # the check used the system user token; posting then uses the Page's own token
    assert set(fake.tokens) == {"EAAPage-secret"}
    assert facebook.config(cl_db)["access_token"] == "EAAPageOwn-token"
    assert cl_api.put(url, headers=admin_h, json={"page_id": "abc"}).status_code == 422


def test_wrong_page_id_is_corrected_or_explained(cl_api, cl_db, monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(facebook, "_call", fake)
    _, admin_h = make_ops(cl_db, role="Admin")
    url = "/collections/ops/channels/facebook"
    # the system user's own ID by mistake: the one Page the token manages is used instead
    r = cl_api.put(url, headers=admin_h, json={"page_id": "61596294633935", "access_token": "EAAsys"})
    assert r.json()["check_error"] is None and r.json()["page_id"] == "61550000000000"
    assert "changed to 61550000000000" in r.json()["note"]
    assert facebook.config(cl_db)["access_token"] == "EAAPageOwn-token"

    fake.pages = fake.pages + [{"id": "999", "name": "Other Page", "access_token": "x"}]
    r = cl_api.put(url, headers=admin_h, json={"page_id": "123", "access_token": "EAAsys2"})
    assert "Waterline Collections: 61550000000000" in r.json()["check_error"] and "Other Page: 999" in r.json()["check_error"]

    # a token that reaches no Page, with a user's ID: both problems are named
    fake.pages = []
    r = cl_api.put(url, headers=admin_h, json={"page_id": "61596294633935", "access_token": "EAAsys3"})
    err = r.json()["check_error"]
    assert "is a user" in err and "not a Page" in err and "pages_show_list" in err
    assert facebook.config(cl_db)["access_token"] == "EAAsys3"  # no stale Page token left behind

    # the Page is reachable but without posting rights
    fake.pages = [{"id": "61550000000000", "name": "Waterline Collections"}]
    r = cl_api.put(url, headers=admin_h, json={"page_id": "61550000000000", "access_token": "EAAsys4"})
    assert "can't post to it yet" in r.json()["check_error"]


def test_ops_need_approval_by_default_admin_approves(cl_api, cl_db, monkeypatch):
    fake = _connect(cl_db, monkeypatch)
    _, ops_h = make_ops(cl_db, role="Ops")
    _, admin_h = make_ops(cl_db, role="Admin")
    assert _post(cl_api, ops_h, "publish").status_code == 403

    r = _post(cl_api, ops_h, "submit", link="https://waterline.ng/collections/schools/")
    assert r.status_code == 201 and r.json()["status"] == "Pending approval"
    pid = r.json()["id"]
    assert cl_api.post(f"/collections/ops/facebook/posts/{pid}/publish", headers=ops_h).status_code == 403
    lst = cl_api.get("/collections/ops/facebook/posts", headers=admin_h).json()
    row = next(p for p in lst["posts"] if p["id"] == pid)
    assert row["can_publish"] and row["can_reject"]

    r = cl_api.post(f"/collections/ops/facebook/posts/{pid}/publish", headers=admin_h)
    assert r.status_code == 200 and r.json()["status"] == "Published"
    assert r.json()["permalink"] == "https://facebook.com/123/posts/456" and r.json()["approved_by"]
    feed = next(c for c in fake.calls if c[1].endswith("/feed"))
    assert feed[2]["link"] == "https://waterline.ng/collections/schools/" and "published" not in feed[2]
    actions = {a for (a,) in cl_db.execute(select(AuditLog.action).where(AuditLog.entity_id == pid))}
    assert {"facebook.post_created", "facebook.post_submitted", "facebook.post_published"} <= actions


def test_reject_then_edit_and_resubmit(cl_api, cl_db, monkeypatch):
    _connect(cl_db, monkeypatch)
    _, ops_h = make_ops(cl_db, role="Ops")
    _, admin_h = make_ops(cl_db, role="Admin")
    pid = _post(cl_api, ops_h, "submit").json()["id"]
    assert cl_api.post(f"/collections/ops/facebook/posts/{pid}/reject", headers=ops_h, json={"reason": "no"}).status_code == 403
    r = cl_api.post(f"/collections/ops/facebook/posts/{pid}/reject", headers=admin_h, json={"reason": "Too salesy"})
    assert r.json()["status"] == "Rejected" and r.json()["reject_reason"] == "Too salesy"
    r = cl_api.put(f"/collections/ops/facebook/posts/{pid}", headers=ops_h, data={"message": "Softer wording."})
    assert r.json()["status"] == "Draft"
    assert cl_api.post(f"/collections/ops/facebook/posts/{pid}/submit", headers=ops_h).json()["status"] == "Pending approval"


def test_ops_publish_directly_when_approval_is_off_with_image_and_schedule(cl_api, cl_db, monkeypatch):
    fake = _connect(cl_db, monkeypatch)
    settings_store.set_value(cl_db, "facebook_posts_need_approval", False)
    cl_db.flush()
    _, ops_h = make_ops(cl_db, role="Ops")

    r = _post(cl_api, ops_h, "publish", image=PNG, link="https://waterline.ng/collections/", scheduled_for=_later())
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "Scheduled" and r.json()["has_image"]
    photo = next(c for c in fake.calls if c[1].endswith("/photos"))
    assert photo[2] == {"published": "false", "temporary": "true"} and photo[3]["source"][1] == PNG
    feed = next(c for c in fake.calls if c[1].endswith("/feed"))[2]
    assert feed["attached_media"] == [{"media_fbid": "photo1"}] and feed["published"] is False
    assert feed["message"].endswith("https://waterline.ng/collections/") and "link" not in feed
    assert abs(feed["scheduled_publish_time"] - (utcnow() + timedelta(minutes=120)).timestamp()) < 120

    # once the time has passed, the list asks Facebook and marks it published
    p = cl_db.get(FbPost, r.json()["id"])
    p.scheduled_for = utcnow() - timedelta(minutes=1)
    cl_db.flush()
    lst = cl_api.get("/collections/ops/facebook/posts", headers=ops_h).json()
    assert next(x for x in lst["posts"] if x["id"] == p.id)["status"] == "Published"

    pid = _post(cl_api, ops_h, "publish", scheduled_for=_later(300)).json()["id"]
    r = cl_api.post(f"/collections/ops/facebook/posts/{pid}/cancel", headers=ops_h)
    assert r.json()["status"] == "Cancelled" and fake.calls[-1][0] == "DELETE"

    assert _post(cl_api, ops_h, "publish", scheduled_for=_later(3)).status_code == 422  # too soon
    assert _post(cl_api, ops_h, "publish", scheduled_for=_later(60 * 24 * 40)).status_code == 422  # too far


def test_facebook_failure_is_kept_as_failed_post(cl_api, cl_db, monkeypatch):
    fake = _connect(cl_db, monkeypatch)
    _, admin_h = make_ops(cl_db, role="Admin")
    fake.fail = "/feed"
    r = _post(cl_api, admin_h, "publish")
    assert r.status_code == 502
    p = cl_db.execute(select(FbPost).order_by(FbPost.created_at.desc()).limit(1)).scalar_one()
    assert p.status == "Failed" and "Something went wrong" in p.error
    fake.fail = None
    assert cl_api.post(f"/collections/ops/facebook/posts/{p.id}/publish", headers=admin_h).json()["status"] == "Published"


def test_posts_naming_a_debtor_or_their_phone_are_refused(cl_api, cl_db, monkeypatch):
    _connect(cl_db, monkeypatch)
    _, admin_h = make_ops(cl_db, role="Admin")
    client, _ = make_client(cl_db)
    make_account(cl_db, client, phone="+2348035550123", name="Chinedu Okafor Eze")
    cl_db.flush()

    for text in ("Thanks to Chinedu Okafor Eze for paying!", "Big shout out to EZE, chinedu.", "Call 0803 555 0123 today",
                 "Reach +234-803-555-0123"):
        r = _post(cl_api, admin_h, "publish", message=text)
        assert r.status_code == 422, text
        assert "debtor records" in r.json()["detail"] and "Chinedu" not in r.json()["detail"]
    assert _post(cl_api, admin_h, "draft", link="https://example.com/chinedu-eze").status_code == 422
    # ordinary words, single names and our own number are fine
    ok = "Chinedu, our new teammate, says hello. WhatsApp us on +234 903 410 4100."
    assert _post(cl_api, admin_h, "publish", message=ok).status_code == 201
    assert not cl_db.execute(select(DebtorAccount.id).where(DebtorAccount.phone_index == crypto.blind_index("+2349034104100"))).first()


def test_only_jpg_or_png_and_delete_rules(cl_api, cl_db, monkeypatch):
    _connect(cl_db, monkeypatch)
    _, ops_h = make_ops(cl_db, role="Ops")
    _, other_h = make_ops(cl_db, role="Ops")
    assert _post(cl_api, ops_h, "draft", image=b"%PDF-1.4 hello").status_code == 422
    assert _post(cl_api, ops_h, "draft", message="  ").status_code == 422
    pid = _post(cl_api, ops_h, "draft").json()["id"]
    assert cl_api.delete(f"/collections/ops/facebook/posts/{pid}", headers=other_h).status_code == 409
    assert cl_api.delete(f"/collections/ops/facebook/posts/{pid}", headers=ops_h).status_code == 200
