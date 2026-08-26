"""cPanel "Setup Python App" entrypoint.

cPanel's Python App support runs on Phusion Passenger, which speaks WSGI —
FastAPI/Starlette are ASGI. `a2wsgi.ASGIMiddleware` bridges the two so
Passenger can serve this app without needing uvicorn/hypercorn as a separate
process. See ../DEPLOYMENT.md for the one-time cPanel setup this depends on
(app root, Python version, environment variables, first `pip install`).
"""

import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = str(APP_ROOT / ".venv" / "bin" / "python")

# cPanel's Application Manager may start Python apps with the server default
# interpreter and does not permit PassengerPython in per-site .htaccess files.
# Re-exec through this app's venv before importing dependencies.
if sys.executable != VENV_PYTHON and Path(VENV_PYTHON).is_file():
    os.execl(VENV_PYTHON, VENV_PYTHON, *sys.argv)

sys.path.insert(0, str(APP_ROOT))

from a2wsgi import ASGIMiddleware  # noqa: E402

from app.main import app as _asgi_app  # noqa: E402

application = ASGIMiddleware(_asgi_app)
