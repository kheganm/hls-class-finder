"""
Database layer: Turso (libSQL) in production, local SQLite for dev.

If TURSO_DATABASE_URL is set, connects to Turso over the network.
Otherwise falls back to a local enrollments.db file.
"""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

TURSO_URL = os.environ.get("TURSO_DATABASE_URL", "").strip()
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "").strip()

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS enrollments (
    user_id TEXT NOT NULL,
    section_id TEXT NOT NULL,
    PRIMARY KEY (user_id, section_id)
)
"""


def _open_raw():
    if TURSO_URL:
        # libsql_experimental mimics the sqlite3 Python API
        import libsql_experimental as libsql
        return libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
    db_path = Path(__file__).parent / "enrollments.db"
    return sqlite3.connect(str(db_path))


# Run schema migration once at import time
_bootstrap = _open_raw()
_bootstrap.execute(_CREATE_SQL)
_bootstrap.commit()
_bootstrap.close()


@contextmanager
def get_db():
    conn = _open_raw()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass
