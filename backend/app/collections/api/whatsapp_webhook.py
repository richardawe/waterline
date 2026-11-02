"""Public webhook Meta calls for WhatsApp: GET to verify the subscription,
POST with incoming messages and delivery statuses. POSTs are only processed
when they carry a valid X-Hub-Signature-256 for the saved app secret.
Always answers 200 to a signed POST (Meta retries anything else for days),
logging processing errors instead."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.collections import whatsapp
from app.db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/collections/whatsapp", tags=["collections-whatsapp"])


@router.get("/webhook")
def verify(request: Request, db: Session = Depends(get_db)):
    q = request.query_params
    expected = whatsapp.stored(db).get("verify_token")
    if q.get("hub.mode") == "subscribe" and expected and q.get("hub.verify_token") == expected:
        return PlainTextResponse(q.get("hub.challenge") or "")
    raise HTTPException(403, "Verification failed.")


@router.post("/webhook")
async def receive(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    secret = whatsapp.stored(db).get("app_secret")
    if not whatsapp.signature_ok(secret, raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "Bad signature.")
    try:
        result = whatsapp.handle_webhook(db, json.loads(raw.decode() or "{}"))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("whatsapp webhook processing failed")
        return {"ok": False}
    return {"ok": True, **result}
