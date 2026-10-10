"""Unauthenticated Collections endpoints: public config, upload template,
client sign-up, email verification, login and password reset."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collections import (
    audit,
    crypto,
    emailer,
    service,
    settings_store,
    totp,
    upload,
)
from app.collections.audit import Actor
from app.collections.auth import (
    client_ip,
    create_session,
    end_all_sessions,
    end_session,
    rate_limit,
)
from app.collections.models import AuthToken, Client, ClientStatus, ClientUser, OpsUser
from app.collections.passwords import hash_password, password_problem, verify_password
from app.collections.phone import normalise_phone
from app.collections.timeutil import utcnow
from app.db import get_db

router = APIRouter(prefix="/collections/public", tags=["collections-public"])

BUSINESS_TYPES = ["School", "Estate", "Gym", "Internet provider", "Other"]
NIGERIAN_STATES = [
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue", "Borno", "Cross River", "Delta",
    "Ebonyi", "Edo", "Ekiti", "Enugu", "FCT", "Gombe", "Imo", "Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi",
    "Kogi", "Kwara", "Lagos", "Nasarawa", "Niger", "Ogun", "Ondo", "Osun", "Oyo", "Plateau", "Rivers", "Sokoto",
    "Taraba", "Yobe", "Zamfara",
]
VERIFY_TTL = timedelta(hours=48)
RESET_TTL = timedelta(hours=2)
INVITE_TTL = timedelta(days=7)
GENERIC_LOGIN_ERROR = "Email or password is incorrect."


@router.get("/config")
def public_config(db: Session = Depends(get_db)):
    return {
        "fee_percent": settings_store.get(db, "default_fee_percent"),
        "contact_email": settings_store.get(db, "contact_email"),
        "contact_whatsapp": settings_store.get(db, "contact_whatsapp"),
        "business_types": BUSINESS_TYPES,
        "states": NIGERIAN_STATES,
    }


@router.get("/upload-template.csv")
def template_csv():
    return Response(
        upload.template_csv(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="waterline-collections-template.csv"'},
    )


@router.get("/upload-template.xlsx")
def template_xlsx():
    return Response(
        upload.template_xlsx(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="waterline-collections-template.xlsx"'},
    )


# ---------------------------------------------------------------- sign-up


class SignupIn(BaseModel):
    business_name: str = Field(min_length=2, max_length=200)
    business_type: str
    contact_name: str = Field(min_length=2, max_length=160)
    role: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=254)
    phone: str
    state: str
    city: str = Field(min_length=2, max_length=80)
    bank_name: str = Field(min_length=2, max_length=120)
    account_number: str
    account_name: str = Field(min_length=2, max_length=200)
    password: str
    accept_terms: bool
    accept_dpa: bool
    accept_privacy: bool


def issue_link(db: Session, user: ClientUser, purpose: str, ttl: timedelta) -> str:
    token = crypto.new_token()
    db.add(AuthToken(token_hash=crypto.hash_token(token), purpose=purpose, user_id=user.id, expires_at=utcnow() + ttl))
    return token


def send_verification(db: Session, user: ClientUser) -> None:
    token = issue_link(db, user, "verify_email", VERIFY_TTL)
    emailer.send(
        db, user.email, "Confirm your email for Waterline Collections",
        f"Hello {user.name.split()[0]},\n\nPlease confirm your email address to finish setting up your account:\n\n"
        f"{emailer.site_url('/collections/verify/?token=' + token)}\n\nThis link expires in 48 hours.",
        kind="client_verify_email", client_id=user.client_id,
    )


def _email_taken(db: Session, email: str) -> bool:
    return db.execute(select(ClientUser.id).where(ClientUser.email_index == crypto.blind_index(email))).first() is not None


@router.post("/signup", status_code=201, dependencies=[Depends(rate_limit("signup", 5, 3600))])
def signup(body: SignupIn, request: Request, db: Session = Depends(get_db)):
    errors = {}
    email = body.email.strip().lower()
    if not upload.EMAIL_RE.match(email):
        errors["email"] = "Enter a valid email address."
    if body.business_type not in BUSINESS_TYPES:
        errors["business_type"] = "Choose a business type."
    if body.state not in NIGERIAN_STATES:
        errors["state"] = "Choose a state."
    try:
        phone = normalise_phone(body.phone)
    except ValueError:
        errors["phone"] = "Enter a Nigerian mobile number."
    account_number = body.account_number.strip()
    if not (account_number.isdigit() and len(account_number) == 10):
        errors["account_number"] = "Account number must be 10 digits (NUBAN)."
    problem = password_problem(body.password)
    if problem:
        errors["password"] = problem
    if not (body.accept_terms and body.accept_dpa and body.accept_privacy):
        errors["accept"] = "Please accept the Terms of Service, Data Processing Agreement and Privacy Policy."
    if errors:
        raise HTTPException(422, {"message": "Please fix the highlighted fields.", "fields": errors})
    if _email_taken(db, email):
        raise HTTPException(409, "An account with this email already exists. Try logging in.")

    now = utcnow()
    client = Client(
        business_name=body.business_name.strip(), business_type=body.business_type, state=body.state,
        city=body.city.strip(), status=ClientStatus.PENDING,
        fee_percent=settings_store.get(db, "default_fee_percent"),
        attribution_days=settings_store.get(db, "default_attribution_days"),
        upload_row_cap=settings_store.get(db, "first_upload_row_cap"),
        bank_name=body.bank_name.strip(), account_number=account_number, account_name=body.account_name.strip(),
        terms_accepted_at=now, dpa_accepted_at=now, privacy_accepted_at=now,
    )
    db.add(client)
    db.flush()
    user = ClientUser(
        client_id=client.id, name=body.contact_name.strip(), email=email, email_index=crypto.blind_index(email),
        phone=phone, job_title=body.role.strip(), role="Owner", password_hash=hash_password(body.password),
    )
    db.add(user)
    db.flush()
    actor = Actor(type="client", id=user.id, ip=client_ip(request), client_id=client.id)
    audit.record(db, actor, "client.signed_up", "Client", client.id,
                 after={"business_name": client.business_name, "business_type": client.business_type,
                        "terms_accepted_at": now, "dpa_accepted_at": now, "privacy_accepted_at": now})
    send_verification(db, user)
    emailer.send(db, service._ops_address(), f"New Collections sign-up: {client.business_name}",
                 f"{client.business_name} ({client.business_type}, {client.city}, {client.state}) signed up and is waiting "
                 "for verification in the ops Approvals list.", kind="ops_new_signup", client_id=client.id)
    db.commit()
    return {"message": "Check your email for a link to confirm your address."}


class TokenIn(BaseModel):
    token: str


def _consume(db: Session, token: str, purpose: str) -> tuple[AuthToken, ClientUser]:
    row = db.execute(select(AuthToken).where(AuthToken.token_hash == crypto.hash_token(token))).scalar_one_or_none()
    if row is None or row.purpose != purpose or row.used_at or row.expires_at < utcnow():
        raise HTTPException(400, "This link is invalid or has expired.")
    row.used_at = utcnow()
    return row, db.get(ClientUser, row.user_id)


@router.post("/verify-email", dependencies=[Depends(rate_limit("verify", 20, 3600))])
def verify_email(body: TokenIn, request: Request, db: Session = Depends(get_db)):
    _, user = _consume(db, body.token, "verify_email")
    user.email_verified_at = user.email_verified_at or utcnow()
    audit.record(db, Actor(type="client", id=user.id, ip=client_ip(request), client_id=user.client_id),
                 "user.email_verified", "ClientUser", user.id)
    token = create_session(db, "client", user.id, client_ip(request))
    db.commit()
    return {"token": token}


class EmailIn(BaseModel):
    email: str


@router.post("/resend-verification", dependencies=[Depends(rate_limit("resend", 5, 3600))])
def resend_verification(body: EmailIn, db: Session = Depends(get_db)):
    user = db.execute(
        select(ClientUser).where(ClientUser.email_index == crypto.blind_index(body.email.strip().lower()))
    ).scalar_one_or_none()
    if user and not user.email_verified_at and user.is_active:
        send_verification(db, user)
        db.commit()
    return {"message": "If that address needs confirming, we've sent a new link."}


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/login", dependencies=[Depends(rate_limit("login", 10, 900))])
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    user = db.execute(
        select(ClientUser).where(ClientUser.email_index == crypto.blind_index(body.email.strip().lower()))
    ).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, GENERIC_LOGIN_ERROR)
    if not user.email_verified_at:
        raise HTTPException(403, {"message": "Please confirm your email first. We can send a new link.", "code": "unverified"})
    if user.client.status == ClientStatus.REJECTED:
        raise HTTPException(403, "This business account was not approved. Contact us if you think this is a mistake.")
    token = create_session(db, "client", user.id, client_ip(request))
    audit.record(db, Actor(type="client", id=user.id, ip=client_ip(request), client_id=user.client_id),
                 "user.logged_in", "ClientUser", user.id)
    db.commit()
    return {"token": token}


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    end_session(db, request)
    db.commit()
    return {"ok": True}


@router.post("/forgot-password", dependencies=[Depends(rate_limit("forgot", 5, 3600))])
def forgot_password(body: EmailIn, db: Session = Depends(get_db)):
    user = db.execute(
        select(ClientUser).where(ClientUser.email_index == crypto.blind_index(body.email.strip().lower()))
    ).scalar_one_or_none()
    if user and user.is_active:
        token = issue_link(db, user, "reset_password", RESET_TTL)
        emailer.send(db, user.email, "Reset your Waterline Collections password",
                     f"Someone asked to reset your password. If that was you, use this link within 2 hours:\n\n"
                     f"{emailer.site_url('/collections/reset/?token=' + token)}\n\nIf it wasn't you, ignore this email.",
                     kind="client_reset_password", client_id=user.client_id)
        db.commit()
    return {"message": "If an account exists for that email, we've sent a reset link."}


class ResetIn(BaseModel):
    token: str
    password: str
    purpose: str = "reset_password"  # or "invite" for a teammate setting their first password


@router.post("/reset-password", dependencies=[Depends(rate_limit("reset", 10, 3600))])
def reset_password(body: ResetIn, request: Request, db: Session = Depends(get_db)):
    if body.purpose not in ("reset_password", "invite"):
        raise HTTPException(400, "This link is invalid or has expired.")
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(422, problem)
    _, user = _consume(db, body.token, body.purpose)
    user.password_hash = hash_password(body.password)
    user.email_verified_at = user.email_verified_at or utcnow()  # the link itself proves the address
    end_all_sessions(db, "client", user.id)
    audit.record(db, Actor(type="client", id=user.id, ip=client_ip(request), client_id=user.client_id),
                 "user.password_set", "ClientUser", user.id, after={"via": body.purpose})
    token = create_session(db, "client", user.id, client_ip(request))
    db.commit()
    return {"token": token}


# ---------------------------------------------------------------- ops login (separate auth + TOTP)


class OpsLoginIn(BaseModel):
    email: str
    password: str
    code: str


@router.post("/ops-login", dependencies=[Depends(rate_limit("ops-login", 5, 900))])
def ops_login(body: OpsLoginIn, request: Request, db: Session = Depends(get_db)):
    user = db.execute(select(OpsUser).where(OpsUser.email == body.email.strip().lower())).scalar_one_or_none()
    ok = user is not None and user.is_active and verify_password(body.password, user.password_hash)
    if not ok or not totp.verify(user.totp_secret, body.code):
        raise HTTPException(401, "Email, password or code is incorrect.")
    token = create_session(db, "ops", user.id, client_ip(request))
    audit.record(db, Actor(type="ops", id=user.id, ip=client_ip(request)), "ops.logged_in", "OpsUser", user.id)
    db.commit()
    return {"token": token, "name": user.name, "role": user.role}
