"""Ops console: Facebook Page posts and the Page connection.

Who can do what:
- Anyone in ops writes posts and saves drafts.
- Publishing (now or scheduled): Admins always; Ops only when Config →
  facebook_posts_need_approval is false. Otherwise Ops send a post for
  approval and an Admin approves (publishes) or rejects it.
- Connecting the Page (Channels) is Admin only.
Every step is in the audit log, and every publish passes the safety check
(no debtor names or phone numbers).
"""

from datetime import date, datetime, time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collections import audit, emailer, facebook, settings_store, storage
from app.collections.auth import OpsContext, require_ops, require_ops_admin
from app.collections.facebook import FacebookError, UnsafePost
from app.collections.models import FbPost, OpsUser
from app.collections.timeutil import lagos_datetime, utcnow
from app.db import get_db

router = APIRouter(prefix="/collections/ops", tags=["collections-ops"])

EDITABLE = ("Draft", "Pending approval", "Rejected", "Failed")
DELETABLE = ("Draft", "Rejected", "Failed", "Cancelled")


def need_approval(db: Session) -> bool:
    return bool(settings_store.get(db, "facebook_posts_need_approval"))


def can_publish(db: Session, ctx: OpsContext) -> bool:
    return ctx.user.role == "Admin" or not need_approval(db)


def _names(db: Session) -> dict:
    return {u.id: u.name for u in db.execute(select(OpsUser)).scalars()}


def _out(p: FbPost, names: dict, ctx: OpsContext, publish_ok: bool) -> dict:
    mine = p.created_by == ctx.user.id
    admin = ctx.user.role == "Admin"
    return {
        "id": p.id, "message": p.message, "link": p.link, "image_url": storage.signed_url(p.image_key),
        "has_image": bool(p.image_key), "scheduled_for": p.scheduled_for, "status": p.status,
        "permalink": p.permalink, "error": p.error, "reject_reason": p.reject_reason,
        "created_by": names.get(p.created_by), "approved_by": names.get(p.approved_by),
        "published_by": names.get(p.published_by), "published_at": p.published_at,
        "created_at": p.created_at, "updated_at": p.updated_at,
        "can_edit": p.status in EDITABLE and (mine or admin),
        "can_publish": p.status in EDITABLE and publish_ok,
        "can_submit": p.status in ("Draft", "Rejected", "Failed") and (mine or admin) and not publish_ok,
        "can_reject": p.status == "Pending approval" and admin,
        "can_cancel": p.status == "Scheduled" and publish_ok,
        "can_delete": p.status in DELETABLE and (mine or admin),
    }


def _get(db: Session, post_id: str) -> FbPost:
    p = db.get(FbPost, post_id)
    if p is None:
        raise HTTPException(404, "Not found.")
    return p


def _parse_when(value: str | None) -> datetime | None:
    """"YYYY-MM-DDTHH:MM" from the form, in Lagos time."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        d, t = value[:10], value[11:16]
        return lagos_datetime(date.fromisoformat(d), time.fromisoformat(t))
    except ValueError as exc:
        raise HTTPException(422, "Pick a date and time for the schedule.") from exc


def _sync_due(db: Session) -> None:
    """Scheduled posts whose time has passed: ask Facebook if they went out."""
    now = utcnow()
    due = db.execute(select(FbPost).where(FbPost.status == "Scheduled", FbPost.scheduled_for <= now)).scalars().all()
    for p in due[:20]:
        try:
            st = facebook.post_state(db, p.fb_post_id)
        except FacebookError:
            continue
        if st.get("is_published"):
            p.status, p.published_at = "Published", p.scheduled_for
            p.permalink = st.get("permalink_url") or p.permalink


async def _read_image(file: UploadFile | None) -> bytes | None:
    if file is None or not file.filename:
        return None
    data = await file.read(storage.MAX_BYTES + 1)
    if len(data) > storage.MAX_BYTES:
        raise HTTPException(413, "The image is larger than 5 MB.")
    return data


def _store_image(data: bytes) -> str:
    try:
        if storage.sniff(data) not in ("jpg", "png"):
            raise storage.FileRejected("Use a JPG or PNG image.")
        return storage.save(data, "facebook")
    except storage.FileRejected as exc:
        raise HTTPException(422, "Use a JPG or PNG image (5 MB max)." if "PDF" in str(exc) else str(exc)) from exc


def _content(db: Session, message: str, link: str | None) -> tuple[str, str | None]:
    message = (message or "").replace("\r\n", "\n").strip()
    if not message:
        raise HTTPException(422, "Write the post first.")
    if len(message) > facebook.MAX_MESSAGE:
        raise HTTPException(422, f"The post is too long ({facebook.MAX_MESSAGE} characters max).")
    try:
        link = facebook.clean_link(link)
    except FacebookError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        facebook.check_safe(db, message, link)
    except UnsafePost as exc:
        raise HTTPException(422, str(exc)) from exc
    return message, link


def _notify_admins(db: Session, p: FbPost, who: OpsUser) -> None:
    for admin in db.execute(select(OpsUser).where(OpsUser.role == "Admin", OpsUser.is_active.is_(True))).scalars():
        if admin.id == who.id:
            continue
        emailer.send(db, admin.email, f"Facebook post to approve from {who.name}",
                     f"{who.name} sent a Facebook post for approval:\n\n{p.message[:600]}\n\n"
                     f"Approve or reject it in the ops console under Facebook:\n"
                     f"{emailer.site_url('/collections/ops/#facebook')}", kind="fb_post_submitted")


def _do_publish(db: Session, p: FbPost, ctx: OpsContext) -> None:
    """Safety check, then publish or schedule. A Facebook failure is kept on
    the post (status Failed) and reported as 502."""
    try:
        facebook.check_safe(db, p.message, p.link)
    except UnsafePost as exc:
        raise HTTPException(422, str(exc)) from exc
    if not facebook.connected(db):
        raise HTTPException(409, "The Facebook Page isn't connected yet. An Admin connects it under Channels.")
    if p.scheduled_for:
        try:
            facebook.check_schedule(p.scheduled_for)
        except FacebookError as exc:
            raise HTTPException(422, str(exc)) from exc
    was_pending = p.status == "Pending approval"
    try:
        res = facebook.publish(db, message=p.message, link=p.link, image_key=p.image_key, scheduled_for=p.scheduled_for)
    except FacebookError as exc:
        p.status, p.error = "Failed", str(exc)[:1000]
        audit.record(db, ctx.actor, "facebook.post_failed", "FbPost", p.id, after={"error": p.error})
        db.commit()
        raise HTTPException(502, f"Facebook didn't take the post: {exc}") from exc
    p.fb_post_id, p.permalink, p.error, p.reject_reason = res["id"], res["permalink"], None, None
    p.published_by = ctx.user.id
    if was_pending or (p.created_by != ctx.user.id and ctx.user.role == "Admin"):
        p.approved_by = ctx.user.id
    if res["scheduled"]:
        p.status = "Scheduled"
    else:
        p.status, p.published_at = "Published", utcnow()
    audit.record(db, ctx.actor, "facebook.post_scheduled" if res["scheduled"] else "facebook.post_published", "FbPost",
                 p.id, after={"fb_post_id": p.fb_post_id, "scheduled_for": p.scheduled_for, "approved": was_pending})


# ---------------------------------------------------------------- posts


@router.get("/facebook/posts")
def list_posts(ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    if facebook.connected(db):
        _sync_due(db)
        db.commit()
    names = _names(db)
    publish_ok = can_publish(db, ctx)
    posts = db.execute(select(FbPost).order_by(FbPost.created_at.desc()).limit(200)).scalars().all()
    return {"connected": facebook.connected(db), "page": {k: v for k, v in facebook.public_settings(db).items()
                                                          if k in ("page_name", "page_link")},
            "need_approval": need_approval(db), "can_publish": publish_ok,
            "links": {"Collections home": emailer.site_url("/collections/"), "Schools page": emailer.site_url("/collections/schools/"),
                      "Estates page": emailer.site_url("/collections/estates/")},
            "posts": [_out(p, names, ctx, publish_ok) for p in posts]}


@router.post("/facebook/posts", status_code=201)
async def create_post(
    message: str = Form(""),
    link: str = Form(""),
    scheduled_for: str = Form(""),
    action: str = Form("draft"),  # draft / submit / publish
    image: UploadFile | None = File(None),
    ctx: OpsContext = Depends(require_ops),
    db: Session = Depends(get_db),
):
    if action not in ("draft", "submit", "publish"):
        raise HTTPException(422, "Unknown action.")
    if action == "publish" and not can_publish(db, ctx):
        raise HTTPException(403, "Posts need an Admin's approval first. Send it for approval instead.")
    message, link = _content(db, message, link)
    when = _parse_when(scheduled_for)
    if when and action != "draft":
        try:
            facebook.check_schedule(when)
        except FacebookError as exc:
            raise HTTPException(422, str(exc)) from exc
    data = await _read_image(image)
    p = FbPost(message=message, link=link, scheduled_for=when, image_key=_store_image(data) if data else None,
               status="Draft", created_by=ctx.user.id)
    db.add(p)
    db.flush()
    audit.record(db, ctx.actor, "facebook.post_created", "FbPost", p.id,
                 after={"link": link, "image": bool(p.image_key), "scheduled_for": when})
    if action == "submit":
        p.status = "Pending approval"
        audit.record(db, ctx.actor, "facebook.post_submitted", "FbPost", p.id)
        _notify_admins(db, p, ctx.user)
    elif action == "publish":
        _do_publish(db, p, ctx)
    db.commit()
    return _out(p, _names(db), ctx, can_publish(db, ctx))


@router.put("/facebook/posts/{post_id}")
async def update_post(
    post_id: str,
    message: str = Form(""),
    link: str = Form(""),
    scheduled_for: str = Form(""),
    remove_image: bool = Form(False),
    image: UploadFile | None = File(None),
    ctx: OpsContext = Depends(require_ops),
    db: Session = Depends(get_db),
):
    p = _get(db, post_id)
    if p.status not in EDITABLE or (p.created_by != ctx.user.id and ctx.user.role != "Admin"):
        raise HTTPException(409, f"This post is {p.status.lower()}, so it can't be edited.")
    p.message, p.link = _content(db, message, link)
    p.scheduled_for = _parse_when(scheduled_for)
    data = await _read_image(image)
    if data or remove_image:
        old = p.image_key
        p.image_key = _store_image(data) if data else None
        storage.delete(old)
    if p.status in ("Rejected", "Failed"):
        p.status = "Draft"
    audit.record(db, ctx.actor, "facebook.post_edited", "FbPost", p.id,
                 after={"link": p.link, "image": bool(p.image_key), "scheduled_for": p.scheduled_for})
    db.commit()
    return _out(p, _names(db), ctx, can_publish(db, ctx))


@router.post("/facebook/posts/{post_id}/submit")
def submit_post(post_id: str, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    p = _get(db, post_id)
    if p.status not in ("Draft", "Rejected", "Failed") or (p.created_by != ctx.user.id and ctx.user.role != "Admin"):
        raise HTTPException(409, f"This post is {p.status.lower()}.")
    try:
        facebook.check_safe(db, p.message, p.link)
    except UnsafePost as exc:
        raise HTTPException(422, str(exc)) from exc
    p.status, p.reject_reason = "Pending approval", None
    audit.record(db, ctx.actor, "facebook.post_submitted", "FbPost", p.id)
    _notify_admins(db, p, ctx.user)
    db.commit()
    return _out(p, _names(db), ctx, can_publish(db, ctx))


@router.post("/facebook/posts/{post_id}/publish")
def publish_post(post_id: str, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    """Publish now or schedule (per the post). For a post awaiting approval, this is the Admin's approval."""
    p = _get(db, post_id)
    if not can_publish(db, ctx):
        raise HTTPException(403, "Posts need an Admin's approval first.")
    if p.status not in EDITABLE:
        raise HTTPException(409, f"This post is already {p.status.lower()}.")
    _do_publish(db, p, ctx)
    db.commit()
    return _out(p, _names(db), ctx, True)


class RejectIn(BaseModel):
    reason: str = Field(min_length=2, max_length=1000)


@router.post("/facebook/posts/{post_id}/reject")
def reject_post(post_id: str, body: RejectIn, ctx: OpsContext = Depends(require_ops_admin), db: Session = Depends(get_db)):
    p = _get(db, post_id)
    if p.status != "Pending approval":
        raise HTTPException(409, f"This post is {p.status.lower()}.")
    p.status, p.reject_reason = "Rejected", body.reason.strip()
    audit.record(db, ctx.actor, "facebook.post_rejected", "FbPost", p.id, after={"reason": p.reject_reason})
    db.commit()
    return _out(p, _names(db), ctx, True)


@router.post("/facebook/posts/{post_id}/cancel")
def cancel_post(post_id: str, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    """Withdraw a scheduled post before it goes out."""
    p = _get(db, post_id)
    if not can_publish(db, ctx):
        raise HTTPException(403, "Only someone who can publish can cancel a scheduled post.")
    if p.status != "Scheduled":
        raise HTTPException(409, f"This post is {p.status.lower()}.")
    try:
        facebook.delete_post(db, p.fb_post_id)
    except FacebookError as exc:
        raise HTTPException(502, f"Facebook didn't cancel it: {exc}") from exc
    p.status = "Cancelled"
    audit.record(db, ctx.actor, "facebook.post_cancelled", "FbPost", p.id, after={"fb_post_id": p.fb_post_id})
    db.commit()
    return _out(p, _names(db), ctx, True)


@router.delete("/facebook/posts/{post_id}")
def delete_post(post_id: str, ctx: OpsContext = Depends(require_ops), db: Session = Depends(get_db)):
    p = _get(db, post_id)
    if p.status not in DELETABLE or (p.created_by != ctx.user.id and ctx.user.role != "Admin"):
        raise HTTPException(409, f"This post is {p.status.lower()}, so it can't be deleted here.")
    storage.delete(p.image_key)
    audit.record(db, ctx.actor, "facebook.post_deleted", "FbPost", p.id, before={"status": p.status})
    db.delete(p)
    db.commit()
    return {"deleted": post_id}


# ---------------------------------------------------------------- the Page connection (Admin)


class FacebookChannelIn(BaseModel):
    page_id: str | None = Field(default=None, max_length=64)
    access_token: str | None = Field(default=None, max_length=1000)


@router.get("/channels/facebook")
def facebook_channel(ctx: OpsContext = Depends(require_ops_admin), db: Session = Depends(get_db)):
    return facebook.public_settings(db)


@router.put("/channels/facebook")
def save_facebook_channel(body: FacebookChannelIn, request: Request, ctx: OpsContext = Depends(require_ops_admin),
                          db: Session = Depends(get_db)):
    data = body.model_dump(exclude_none=True)
    try:
        facebook.save_settings(db, data, ctx.user.id)
    except FacebookError as exc:
        raise HTTPException(422, str(exc)) from exc
    check = note = None
    if facebook.connected(db):
        try:
            note = facebook.refresh_page(db).get("note")
        except FacebookError as exc:
            check = str(exc)
    audit.record(db, ctx.actor, "channel.facebook_saved", "Channel", "facebook",
                 after={"page_id": data.get("page_id"), "token_changed": bool(data.get("access_token"))})
    db.commit()
    return {**facebook.public_settings(db), "check_error": check, "note": note}
