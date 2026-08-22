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
