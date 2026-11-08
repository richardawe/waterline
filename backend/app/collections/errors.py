"""Unhandled errors on /collections/* come back as JSON, with CORS headers.

Starlette answers an unhandled exception from its outermost layer, outside
CORSMiddleware, so the 500 carries no Access-Control-Allow-Origin header.
The browser then hides the response and cl.js can only say "We couldn't
reach Waterline". This middleware sits inside CORSMiddleware: it logs the
traceback (Passenger's error log) and returns {"detail": ...}, so the
console shows what went wrong.

Ops console routes (staff only) get the error type and message; everything
else under /collections (client app, debtor payment links, public forms)
gets a plain message. Both get a reference that matches the log entry."""

import json
import logging
import secrets

logger = logging.getLogger("app.collections.errors")

PREFIX = "/collections"
OPS_PREFIX = "/collections/ops/"


class CollectionsErrorMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if not (path == PREFIX or path.startswith(PREFIX + "/")):
            return await self.app(scope, receive, send)

        started = False

        async def send_wrapper(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            if started:  # too late to send a different response
                raise
            ref = secrets.token_hex(4)
            logger.exception("unhandled error on %s %s (ref %s)", scope.get("method"), path, ref)
            if path.startswith(OPS_PREFIX):
                detail = f"Something went wrong on the server ({type(exc).__name__}: {str(exc)[:300]}). Reference {ref}."
            else:
                detail = f"Something went wrong on our side. Please try again later. Reference {ref}."
            body = json.dumps({"detail": detail}).encode()
            await send({"type": "http.response.start", "status": 500, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store, max-age=0")]})
            await send({"type": "http.response.body", "body": body})
