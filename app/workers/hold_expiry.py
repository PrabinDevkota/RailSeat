"""Release seats whose five-minute hold has lapsed, then promote the waitlist."""

from __future__ import annotations

from app.db import connect
from app.services.booking import expire_holds


def expire_once(db_path) -> list[int]:
    conn = connect(db_path)
    try:
        return expire_holds(conn)
    finally:
        conn.close()
