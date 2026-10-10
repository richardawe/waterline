import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "standards" / "wcds" / "samples"

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://waterline:waterline@localhost:5432/waterline"
)


def _db_available() -> bool:
    try:
        engine = create_engine(TEST_DATABASE_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at TEST_DATABASE_URL")


@pytest.fixture()
def db_session():
    """Yields a session on a transaction that's rolled back after the test —
    keeps integration tests from polluting whatever DB they run against."""
    engine = create_engine(TEST_DATABASE_URL)
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


@pytest.fixture()
def cl_db(tmp_path, monkeypatch):
    """Like db_session, but handler-level `db.commit()` calls become savepoint
    releases, so Collections API tests can go through real HTTP + get_db and
    still leave nothing behind. Uploaded files go to a temp dir."""
    from app.config import get_settings

    monkeypatch.setenv("COLLECTIONS_STORAGE_DIR", str(tmp_path / "files"))
    get_settings.cache_clear()
    engine = create_engine(TEST_DATABASE_URL)
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
        get_settings.cache_clear()


@pytest.fixture()
def cl_api(cl_db):
    from fastapi.testclient import TestClient

    from app.collections.auth import reset_rate_limits
    from app.db import get_db
    from app.main import app

    reset_rate_limits()
    app.dependency_overrides[get_db] = lambda: cl_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
