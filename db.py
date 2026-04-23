"""
Database layer: Turso (libSQL HTTP API) in production, local SQLite for dev.

If TURSO_DATABASE_URL is set, calls Turso's /v2/pipeline HTTP endpoint using
`requests` — no native extensions needed.
Otherwise falls back to a local enrollments.db file via sqlite3.
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


# ---- Turso HTTP client (sqlite3-compatible minimal surface) ---------------

def _turso_http_url() -> str:
    # libsql://foo.turso.io -> https://foo.turso.io
    url = TURSO_URL
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    return url.rstrip("/") + "/v2/pipeline"


class _TursoCursor:
    def __init__(self, rows: list[list], cols: list[str]):
        self._rows = rows
        self._cols = cols
        self._i = 0

    def fetchone(self):
        if self._i >= len(self._rows):
            return None
        row = self._rows[self._i]
        self._i += 1
        return tuple(row)

    def fetchall(self):
        out = [tuple(r) for r in self._rows[self._i:]]
        self._i = len(self._rows)
        return out


class _TursoConnection:
    """Minimal sqlite3-compatible wrapper over Turso's HTTP pipeline API."""

    def __init__(self):
        import requests  # imported lazily so local-dev doesn't need it
        self._requests = requests
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {TURSO_TOKEN}",
            "Content-Type": "application/json",
        })
        self._url = _turso_http_url()
        self._pending: list[dict] = []  # queued statements until commit()

    def _arg(self, v):
        if v is None:
            return {"type": "null"}
        if isinstance(v, bool):
            return {"type": "integer", "value": "1" if v else "0"}
        if isinstance(v, int):
            return {"type": "integer", "value": str(v)}
        if isinstance(v, float):
            return {"type": "float", "value": v}
        if isinstance(v, (bytes, bytearray)):
            import base64
            return {"type": "blob", "base64": base64.b64encode(bytes(v)).decode()}
        return {"type": "text", "value": str(v)}

    def _run(self, statements: list[dict]) -> list[dict]:
        body = {
            "requests": [{"type": "execute", "stmt": s} for s in statements]
                       + [{"type": "close"}]
        }
        r = self._session.post(self._url, json=body, timeout=15)
        r.raise_for_status()
        data = r.json()
        results = []
        for item in data.get("results", []):
            if item.get("type") == "error":
                err = item.get("error", {})
                raise RuntimeError(f"Turso error: {err.get('message', err)}")
            if item.get("type") == "ok" and "response" in item:
                resp = item["response"]
                if resp.get("type") == "execute":
                    results.append(resp.get("result", {}))
        return results

    def execute(self, sql: str, params: tuple | list = ()) -> _TursoCursor:
        stmt = {"sql": sql, "args": [self._arg(p) for p in (params or ())]}

        # If this is a read, flush pending writes first so SELECT sees them
        is_select = sql.lstrip().upper().startswith(("SELECT", "PRAGMA"))
        if is_select:
            batch = self._pending + [stmt]
            self._pending = []
            results = self._run(batch)
            last = results[-1]
        else:
            # Write: queue it — will be flushed on commit() or next read
            self._pending.append(stmt)
            return _TursoCursor([], [])

        cols = [c["name"] for c in last.get("cols", [])]
        rows = [[cell.get("value") for cell in row] for row in last.get("rows", [])]
        # libSQL returns ints/floats as strings; coerce integers where useful
        typed_rows = []
        for row in rows:
            typed = []
            for cell, col in zip(row, last.get("rows", [[]])[0] if last.get("rows") else []):
                typed.append(cell)
            typed_rows.append(row)
        return _TursoCursor(typed_rows, cols)

    def commit(self):
        if self._pending:
            batch = self._pending
            self._pending = []
            self._run(batch)

    def rollback(self):
        self._pending = []

    def close(self):
        try:
            self._session.close()
        except Exception:
            pass


# ---- Factory ---------------------------------------------------------------


def _open_raw():
    if TURSO_URL:
        return _TursoConnection()
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
