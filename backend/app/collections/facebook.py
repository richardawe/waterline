"""Facebook Page posts for Waterline Collections (ops → Facebook).

Connection: cl_channel["facebook"] holds the Page ID and a Page access token
(from a system user in the 3d7 Technologies business portfolio), entered by
an Admin in ops → Channels. The token is encrypted and never returned.

Posting: text, an optional link and one optional image, published now or
scheduled. Scheduling is Facebook's own (published=false +
scheduled_publish_time), so nothing depends on our daily cron. An image is
uploaded unpublished first, then attached to the post, which works for both
"now" and scheduled posts.

Safety: before anything goes public, check_safe() refuses a post whose text
or link contains the name or phone number of anyone in our debtor records.
A public post naming someone who owes money would be shaming. Images can't
be checked automatically.

HTTP uses urllib (standard library): no new pip packages on the server.
"""

import json
import re
import secrets
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collections import crypto, storage
from app.collections.models import Channel, DebtorAccount
from app.collections.phone import normalise_phone
from app.collections.timeutil import utcnow

KEY = "facebook"
GRAPH = "https://graph.facebook.com"
API_VERSION = "v23.0"
MAX_MESSAGE = 5000
# Facebook accepts scheduled posts from 10 minutes to (at most) a few months
# ahead; we keep to a range it always accepts.
SCHEDULE_MIN = timedelta(minutes=10)
SCHEDULE_MAX = timedelta(days=30)


class FacebookError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class UnsafePost(ValueError):
    pass


# ---------------------------------------------------------------- settings


def _row(db: Session) -> Channel | None:
    return db.get(Channel, KEY)


def stored(db: Session) -> dict:
    row = _row(db)
    return json.loads(row.settings) if row and row.settings else {}


def config(db: Session) -> dict | None:
    """Posting uses the Page's own token ("page_token"), fetched with the
    system user token on "Save and check"; until then, the saved token."""
    s = stored(db)
    if not (s.get("page_id") and s.get("access_token")):
        return None
    cfg = {"api_version": API_VERSION, **s}
    cfg["access_token"] = s.get("page_token") or s["access_token"]
    return cfg


def connected(db: Session) -> bool:
    return config(db) is not None


def public_settings(db: Session) -> dict:
    s = stored(db)
    return {"page_id": s.get("page_id") or "", "has_token": bool(s.get("access_token")), "connected": connected(db),
            "page_name": s.get("page_name"), "page_link": s.get("page_link")}


def _save(db: Session, data: dict, actor_id: str | None = None) -> None:
    row = _row(db)
    if row is None:
        row = Channel(key=KEY)
        db.add(row)
        db.flush()
    row.settings = json.dumps(data)
    if actor_id:
        row.updated_by = actor_id


def save_settings(db: Session, data: dict, actor_id: str) -> None:
    s = stored(db)
    if data.get("page_id") is not None:
        page_id = re.sub(r"\s", "", str(data["page_id"]))[:64]
        if page_id and not (page_id.isascii() and page_id.isdigit()):
            raise FacebookError("The Page ID should be digits only (Page → About → Page transparency, or Meta Business Settings).")
        if page_id != s.get("page_id"):
            s.pop("page_name", None)
            s.pop("page_link", None)
        s["page_id"] = page_id
    if data.get("access_token"):  # blank keeps the saved one
        token = "".join(ch for ch in str(data["access_token"]) if not (ch.isspace() or unicodedata.category(ch) == "Cf"))
        if not token.isascii() or not token.isprintable():
            raise FacebookError("The access token contains characters that can't be part of it (for example \"…\" from a "
                                "shortened copy). Copy the full token from Meta again.")
        s["access_token"] = token
        s.pop("page_token", None)  # fetched again from the new token
    if data.get("page_id") is not None and s.get("page_id") != stored(db).get("page_id"):
        s.pop("page_token", None)
    _save(db, s, actor_id)


def refresh_page(db: Session) -> dict:
    """Ask Facebook about the Page and fetch the Page's own token (posting
    uses it). Starts from the Pages the entered token can reach
    (/me/accounts), so a wrong Page ID, such as the system user's own ID, is
    corrected when the token reaches exactly one Page, and explained otherwise."""
    cfg = config(db)
    if cfg is None:
        raise FacebookError("Enter the Page ID and Page access token first.")
    s = stored(db)
    entered = {**cfg, "access_token": s["access_token"]}  # the system user's token, as entered
    try:
        pages = _call(entered, "GET", "/me/accounts?fields=id,name,link,access_token&limit=50").get("data") or []
    except FacebookError:
        pages = []
    page = next((p for p in pages if str(p.get("id")) == s["page_id"]), None)
    note = None
    if page is None and len(pages) == 1:
        page = pages[0]
        note = f"The Page ID was changed to {page['id']}, the Page this token manages (the ID entered wasn't it)."
    if page is None and pages:
        raise FacebookError("That ID isn't one of the Pages this token can manage. Use one of these Page IDs: "
                            + "; ".join(f"{p.get('name')}: {p.get('id')}" for p in pages) + ".")
    if page is None:
        raise FacebookError(_no_page_reason(entered, s["page_id"]))
    if not page.get("access_token"):
        raise FacebookError(f"Found the Page “{page.get('name')}”, but this token can't post to it yet: it's missing "
                            "pages_manage_posts. Add it to the app, generate a new token with it ticked, and save it here.")
    s.update(page_id=str(page["id"]), page_name=page.get("name"), page_link=page.get("link"), page_token=page["access_token"])
    _save(db, s)
    return {"name": page.get("name"), "link": page.get("link"), "note": note}


def _no_page_reason(cfg: dict, object_id: str) -> str:
    """The token reaches no Page. Say whether the ID is even a Page."""
    try:
        meta = _call(cfg, "GET", f"/{object_id}?metadata=1&fields=id,name")
        kind = ((meta.get("metadata") or {}).get("type") or "").lower()
        name = meta.get("name")
    except FacebookError:
        kind, name = "", None
    perms = ("Also check the token: generate it for the Waterline app with pages_show_list, pages_manage_posts and "
             "pages_read_engagement ticked, from a system user that has the Page assigned.")
    if kind and kind != "page":
        return (f"The ID {object_id} is a {kind.replace('_', ' ')}" + (f" (“{name}”)" if name else "") + ", not a Page. "
                "Use the Page ID from the Page's About → Page transparency. " + perms)
    return "This token can't see any Facebook Page. " + perms


# ---------------------------------------------------------------- HTTP


def _call(cfg: dict, method: str, path: str, payload: dict | None = None, *, files: dict | None = None) -> dict:
    url = f"{GRAPH}/{cfg['api_version']}{path}"
    headers = {"Authorization": f"Bearer {cfg['access_token']}"}
    data = None
    if files:
        data, ctype = _multipart(payload or {}, files)
        headers["Content-Type"] = ctype
    elif payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60 if files else 20) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read().decode()).get("error", {})
        except Exception:
            err = {}
        detail = err.get("error_user_msg") or err.get("message") or str(exc)
        raise FacebookError(_friendly(err.get("code"), detail), err.get("code")) from exc
    except urllib.error.URLError as exc:
        raise FacebookError(f"Couldn't reach Facebook: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise FacebookError(f"Couldn't reach Facebook ({type(exc).__name__}). Try again in a minute.") from exc
    except ValueError:
        # http.client refuses header values it can't send; its message quotes the token.
        raise FacebookError("The saved access token contains characters that can't be sent. "
                            "Paste it in again under Channels.") from None
    try:
        return json.loads(raw.decode() or "{}")
    except ValueError as exc:
        raise FacebookError("Facebook sent back a reply we couldn't read. Try again in a minute.") from exc


def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = "----wl" + secrets.token_hex(12)
    out = []
    for k, v in fields.items():
        out.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    for k, (filename, content, ctype) in files.items():
        out.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{filename}\"\r\n"
                   f"Content-Type: {ctype}\r\n\r\n".encode() + content + b"\r\n")
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


def _friendly(code, detail: str) -> str:
    hints = {
        190: "The Page access token is invalid or expired. Generate a new one and save it under Channels.",
        200: "The token doesn't have permission to post to this Page (it needs pages_manage_posts).",
        10: "The token doesn't have permission to post to this Page (it needs pages_manage_posts).",
        100: f"Facebook didn't accept the request: {detail}",
        368: "Facebook has temporarily blocked posting from this Page. Check the Page's notifications.",
    }
    return hints.get(code, detail)


# ---------------------------------------------------------------- safety


_PHONEISH = re.compile(r"\+?\d[\d\s\-().]{7,}\d")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " " + re.sub(r"[^a-z0-9]+", " ", text).strip() + " "


def _name_forms(name: str) -> set[str]:
    """The full name, plus first+last in either order (Nigerian names are
    often written surname first). Single-word names are skipped: too many
    false alarms on ordinary words."""
    parts = _norm(name).split()
    if len(parts) < 2:
        return set()
    forms = {" ".join(parts), f"{parts[0]} {parts[-1]}", f"{parts[-1]} {parts[0]}"}
    return {f" {f} " for f in forms}


def _phone_indexes(text: str) -> set[str]:
    out = set()
    for m in _PHONEISH.finditer(text or ""):
        raw = m.group(0)
        candidates = []
        try:
            candidates.append(normalise_phone(raw))
        except ValueError:
            pass
        digits = re.sub(r"\D", "", raw)
        if 8 <= len(digits) <= 15:
            candidates.append("+" + digits)
        out.update(crypto.blind_index(c) for c in candidates)
    return out


def check_safe(db: Session, *texts: str | None) -> None:
    """Raises UnsafePost if any text names or gives the phone number of
    someone in our debtor records. The message doesn't repeat what matched."""
    text = " ".join(t for t in texts if t)
    idx = _phone_indexes(text)
    if idx and db.execute(select(DebtorAccount.id).where(DebtorAccount.phone_index.in_(idx)).limit(1)).first():
        raise UnsafePost("This post contains the phone number of someone in our debtor records. Posts must never "
                         "identify people who owe money. Remove it and try again.")
    norm = _norm(text)
    for name in db.execute(select(DebtorAccount.debtor_name)).scalars():
        if any(f in norm for f in _name_forms(name or "")):
            raise UnsafePost("This post contains the name of someone in our debtor records. Posts must never "
                             "identify people who owe money. Remove it and try again.")


# ---------------------------------------------------------------- publishing


def check_schedule(when: datetime | None, now: datetime | None = None) -> None:
    if when is None:
        return
    now = now or utcnow()
    if when < now + SCHEDULE_MIN:
        raise FacebookError("Schedule it at least 10 minutes from now, or post it now.")
    if when > now + SCHEDULE_MAX:
        raise FacebookError("Schedule it within the next 30 days.")


def publish(db: Session, *, message: str, link: str | None, image_key: str | None,
            scheduled_for: datetime | None) -> dict:
    """Posts to the Page. Returns {"id", "permalink", "scheduled"}; raises FacebookError."""
    cfg = config(db)
    if cfg is None:
        raise FacebookError("The Facebook Page isn't connected yet. An Admin connects it under Channels.")
    check_schedule(scheduled_for)
    page = cfg["page_id"]
    post: dict = {"message": message}
    if image_key:
        path = storage.path_for(image_key)
        if path is None:
            raise FacebookError("The image file is missing. Remove it and add it again.")
        data = path.read_bytes()
        ext = storage.sniff(data)
        photo = _call(cfg, "POST", f"/{page}/photos",
                      {"published": "false", "temporary": "true" if scheduled_for else "false"},
                      files={"source": (f"image.{ext}", data, storage.CONTENT_TYPES[ext])})
        post["attached_media"] = [{"media_fbid": photo["id"]}]
        if link:  # a photo post can't carry a link preview, so the link goes in the text
            post["message"] = f"{message}\n\n{link}"
    elif link:
        post["link"] = link
    if scheduled_for:
        post["published"] = False
        post["scheduled_publish_time"] = int(scheduled_for.astimezone(timezone.utc).timestamp())
    res = _call(cfg, "POST", f"/{page}/feed", post)
    post_id = res.get("id")
    permalink = None
    if post_id:
        try:
            permalink = _call(cfg, "GET", f"/{post_id}?fields=permalink_url").get("permalink_url")
        except FacebookError:
            permalink = None
    return {"id": post_id, "permalink": permalink, "scheduled": bool(scheduled_for)}


def post_state(db: Session, post_id: str) -> dict:
    cfg = config(db)
    if cfg is None:
        raise FacebookError("The Facebook Page isn't connected.")
    return _call(cfg, "GET", f"/{post_id}?fields=is_published,permalink_url")


def delete_post(db: Session, post_id: str) -> None:
    cfg = config(db)
    if cfg is None:
        raise FacebookError("The Facebook Page isn't connected.")
    _call(cfg, "DELETE", f"/{post_id}")


def clean_link(link: str | None) -> str | None:
    link = (link or "").strip()
    if not link:
        return None
    parsed = urllib.parse.urlparse(link)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or len(link) > 500 or any(c.isspace() for c in link):
        raise FacebookError("The link should be a full web address starting with https://.")
    return link
