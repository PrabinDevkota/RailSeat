"""The broken check-then-insert used only to show why the primary key matters."""

from __future__ import annotations

import time


def ensure_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS naive_lock (
          run_id INTEGER,
          seat_id INTEGER,
          segment_idx INTEGER,
          user_id INTEGER
        )
        """
    )


def naive_book(conn, user_id: int, run_id: int, seat_id: int, from_idx: int, to_idx: int) -> bool:
    """Check, pause, then insert. No unique constraint and no immediate transaction."""
    taken = conn.execute(
        """
        SELECT 1 FROM naive_lock
        WHERE run_id=? AND seat_id=? AND segment_idx>=? AND segment_idx<?
        """,
        (run_id, seat_id, from_idx, to_idx),
    ).fetchone()
    time.sleep(0.01)
    if taken:
        return False
    conn.executemany(
        "INSERT INTO naive_lock(run_id, seat_id, segment_idx, user_id) VALUES (?,?,?,?)",
        [(run_id, seat_id, seg, user_id) for seg in range(from_idx, to_idx)],
    )
    return True
