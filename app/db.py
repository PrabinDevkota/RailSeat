"""Connections, pragmas and immediate transactions."""

from __future__ import annotations

import random
import sqlite3
import time
from pathlib import Path

from app.replication.triggers import change_trigger_sql

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "schema.sql"

# Counters the metrics page reads. Writes are short, so a plain int is enough.
lock_waits = 0


def connect(path: str | Path, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if readonly and not path.exists():
        raise FileNotFoundError(path)
    mode = "ro" if readonly else "rwc"
    uri = f"{path.as_uri()}?mode={mode}"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
    return conn


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.OperationalError:
        pass


def write_tx(conn: sqlite3.Connection, fn, retries: int = 8):
    """Run fn(conn) inside BEGIN IMMEDIATE, retrying on writer contention."""
    global lock_waits
    last = None
    for attempt in range(retries):
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            last = exc
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            lock_waits += 1
            time.sleep((2 ** attempt) * 0.01 + random.random() * 0.01)
            continue
        try:
            result = fn(conn)
            conn.execute("COMMIT")
            return result
        except sqlite3.OperationalError as exc:
            _rollback(conn)
            last = exc
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            lock_waits += 1
            time.sleep((2 ** attempt) * 0.01 + random.random() * 0.01)
        except Exception:
            _rollback(conn)
            raise
    raise RuntimeError(f"write transaction failed after retries: {last}")


def latest_lsn(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(lsn), 0) FROM change_log").fetchone()
    return int(row[0])


def init_db(path: str | Path) -> sqlite3.Connection:
    conn = connect(path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.executescript(change_trigger_sql())
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def set_applying(conn: sqlite3.Connection, applying: bool) -> None:
    conn.execute("UPDATE apply_mode SET applying=? WHERE id=1", (1 if applying else 0,))
