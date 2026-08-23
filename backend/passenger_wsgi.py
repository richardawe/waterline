"""cPanel "Setup Python App" entrypoint.

cPanel's Python App support runs on Phusion Passenger, which speaks WSGI —
FastAPI/Starlette are ASGI. `a2wsgi.ASGIMiddleware` bridges the two so
Passenger can serve this app without needing uvicorn/hypercorn as a separate
process. See ../DEPLOYMENT.md for the one-time cPanel setup this depends on
(app root, Python version, environment variables, first `pip install`).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from a2wsgi import ASGIMiddleware  # noqa: E402

from app.main import app as _asgi_app  # noqa: E402

application = ASGIMiddleware(_asgi_app)
