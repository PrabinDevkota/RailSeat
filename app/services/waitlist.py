"""Oldest compatible waitlist request is allocated inside the cancelling transaction."""

from __future__ import annotations

import json

from app.db import write_tx
from app.services.booking import BookingError


def join_waitlist(conn, user_id: int, run_id: int, from_idx: int, to_idx: int,
                  coach_class: str, preferences: dict | None) -> int:
    if coach_class not in ("SL", "3A", "2A", "1A"):
        raise BookingError("Unknown class")

    def tx(c):
        run = c.execute("SELECT status FROM train_run WHERE run_id=?", (run_id,)).fetchone()
        if run is None or run["status"] not in ("SCHEDULED", "RUNNING"):
            raise BookingError("This train is not open")
        cur = c.execute(
            """
            INSERT INTO waitlist(user_id, run_id, from_idx, to_idx, coach_class, preferences, status)
            VALUES (?,?,?,?,?,?, 'WAITING')
            """,
            (user_id, run_id, from_idx, to_idx, coach_class, json.dumps(preferences or {})),
        )
        c.execute(
            "INSERT INTO inbox(user_id, body) VALUES (?, ?)",
            (user_id, f"You are on the waitlist for run {run_id}, {coach_class}."),
        )
        return cur.lastrowid

    return write_tx(conn, tx)


def promote_for_run(conn, run_id: int) -> int | None:
    """Allocate the oldest waiting request if a seat of that class is free. Same transaction."""
    from app.services.allocation import allocate_tx

    row = conn.execute(
        """
        SELECT * FROM waitlist
        WHERE run_id=? AND status='WAITING'
        ORDER BY created_at, waitlist_id
        LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    prefs = json.loads(row["preferences"] or "{}")
    user = conn.execute("SELECT * FROM user WHERE user_id=?", (row["user_id"],)).fetchone()
    try:
        result = allocate_tx(
            conn,
            user=dict(user),
            run_id=run_id,
            from_idx=row["from_idx"],
            to_idx=row["to_idx"],
            coach_class=row["coach_class"],
            prefs=prefs,
            strategy="L2" if prefs else "L3",
        )
    except Exception:
        return None
    if result is None:
        return None
    conn.execute(
        """
        UPDATE waitlist
        SET status='PROMOTED', promoted_booking_id=?
        WHERE waitlist_id=?
        """,
        (result["booking_id"], row["waitlist_id"]),
    )
    conn.execute(
        "INSERT INTO inbox(user_id, body) VALUES (?, ?)",
        (
            row["user_id"],
            f"A seat opened on run {run_id}. Booking {result['booking_id']} is held for 5 minutes. {result['explain']}",
        ),
    )
    return result["booking_id"]
