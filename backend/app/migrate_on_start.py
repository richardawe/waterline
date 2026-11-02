"""Apply pending Alembic migrations when the app starts on the server.

Called from passenger_wsgi.py, so every deploy (which stamps
tmp/restart.txt) brings the database up to date without anyone running
`alembic upgrade head` in cPanel Terminal. SSH is blocked on the host, so
this is the only automated way to migrate.

- A Postgres advisory lock makes sure only one Passenger worker migrates;
  the others wait for it, then find nothing to do.
- A failure is logged and the app still starts, so one bad migration can't
  take the whole API (admin, blog, collections) offline. The error and the
  current revision are written to tmp/migrate.log next to the app.
- Set AUTO_MIGRATE=false in the server .env to turn it off."""

import logging
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

APP_ROOT = Path(__file__).resolve().parent.parent
_LOCK_ID = 7_302_101


def _log(line: str) -> None:
    try:
        (APP_ROOT / "tmp").mkdir(exist_ok=True)
        with open(APP_ROOT / "tmp" / "migrate.log", "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat()} {line}\n")
    except OSError:
        pass


def upgrade_to_head() -> None:
    if os.environ.get("AUTO_MIGRATE", "true").lower() in ("0", "false", "no"):
        return
    try:
        from alembic import command
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from sqlalchemy import create_engine, text

        from app.config import get_settings

        cfg = Config()  # no ini file: keeps alembic from reconfiguring the app's logging
        cfg.set_main_option("script_location", str(APP_ROOT / "alembic"))
        engine = create_engine(get_settings().database_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _LOCK_ID})
            try:
                before = MigrationContext.configure(conn).get_current_revision()
                command.upgrade(cfg, "head")
                after = MigrationContext.configure(conn).get_current_revision()
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_ID})
                conn.commit()
        engine.dispose()
        if before != after:
            _log(f"migrated {before} -> {after}")
    except Exception:
        _log("migration FAILED:\n" + traceback.format_exc())
        logger.exception("automatic database migration failed")


def status() -> dict:
    """For ops → Channels: the database's revision vs. the code's, and the
    last lines of tmp/migrate.log (there's no cPanel access to read it)."""
    out: dict = {"current": None, "head": None, "log": []}
    try:
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory
        from sqlalchemy import create_engine

        from app.config import get_settings

        cfg = Config()
        cfg.set_main_option("script_location", str(APP_ROOT / "alembic"))
        out["head"] = ScriptDirectory.from_config(cfg).get_current_head()
        engine = create_engine(get_settings().database_url)
        with engine.connect() as conn:
            out["current"] = MigrationContext.configure(conn).get_current_revision()
        engine.dispose()
    except Exception as exc:
        out["error"] = str(exc)[:300]
    try:
        lines = (APP_ROOT / "tmp" / "migrate.log").read_text(encoding="utf-8").splitlines()
        out["log"] = lines[-8:]
    except OSError:
        pass
    return out
