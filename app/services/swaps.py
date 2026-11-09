"""Two confirmed passengers on one run exchange seats in a single transaction."""

from __future__ import annotations

import sqlite3

from app.db import write_tx
from app.services.booking import SeatTaken

class SwapError(Exception):
    pass


def propose_swap(conn, user_id: int, booking_id: int, other_booking_id: int) -> int:
    def tx(c):
        a = c.execute("SELECT * FROM booking WHERE booking_id=?", (booking_id,)).fetchone()
        b = c.execute("SELECT * FROM booking WHERE booking_id=?", (other_booking_id,)).fetchone()
        if a is None or b is None:
            raise SwapError("Booking not found")
        if a["user_id"] != user_id:
            raise SwapError("You can only offer your own seat")
        if a["run_id"] != b["run_id"]:
            raise SwapError("Swaps stay on the same train run")
        if a["status"] != "CONFIRMED" or b["status"] != "CONFIRMED":
            raise SwapError("Both tickets must be confirmed")
        if a["user_id"] == b["user_id"]:
            raise SwapError("Pick the other passenger's booking")
        _fits(c, a["user_id"], b["seat_id"])
        _fits(c, b["user_id"], a["seat_id"])
        open_offer = c.execute(
            """
            SELECT 1 FROM seat_swap
            WHERE status='PROPOSED'
              AND ((booking_a=? AND booking_b=?) OR (booking_a=? AND booking_b=?))
            """,
            (a["booking_id"], b["booking_id"], b["booking_id"], a["booking_id"]),
        ).fetchone()
        if open_offer:
            raise SwapError("An offer is already open for these tickets")
        cur = c.execute(
            """
            INSERT INTO seat_swap(run_id, booking_a, booking_b, status)
            VALUES (?, ?, ?, 'PROPOSED')
            """,
            (a["run_id"], a["booking_id"], b["booking_id"]),
        )
        offerer = c.execute("SELECT name FROM user WHERE user_id=?", (user_id,)).fetchone()
        mine = _berth(c, a["seat_id"])
        theirs = _berth(c, b["seat_id"])
        c.execute(
            "INSERT INTO inbox(user_id, body) VALUES (?,?)",
            (
                b["user_id"],
                f"{offerer['name']} offers {mine} (booking {a['booking_id']}) for your {theirs} (booking {b['booking_id']}). Accept swap {cur.lastrowid} in Messages.",
            ),
        )
        return cur.lastrowid

    return write_tx(conn, tx)


def accept_swap(conn, user_id: int, swap_id: int) -> dict:
    def tx(c):
        swap = c.execute("SELECT * FROM seat_swap WHERE swap_id=?", (swap_id,)).fetchone()
        if swap is None or swap["status"] != "PROPOSED":
            raise SwapError("Swap is not open")
        a = c.execute("SELECT * FROM booking WHERE booking_id=?", (swap["booking_a"],)).fetchone()
        b = c.execute("SELECT * FROM booking WHERE booking_id=?", (swap["booking_b"],)).fetchone()
        if b["user_id"] != user_id:
            raise SwapError("Only the other passenger can accept")
        if a["status"] != "CONFIRMED" or b["status"] != "CONFIRMED":
            raise SwapError("Both tickets must still be confirmed")
        _fits(c, a["user_id"], b["seat_id"])
        _fits(c, b["user_id"], a["seat_id"])
        seat_a, seat_b = a["seat_id"], b["seat_id"]
        c.execute("SAVEPOINT swap_try")
        try:
            c.execute("DELETE FROM seat_segment_lock WHERE booking_id IN (?, ?)", (a["booking_id"], b["booking_id"]))
            c.execute("UPDATE booking SET seat_id=? WHERE booking_id=?", (seat_b, a["booking_id"]))
            c.execute("UPDATE booking SET seat_id=? WHERE booking_id=?", (seat_a, b["booking_id"]))
            try:
                c.executemany(
                    "INSERT INTO seat_segment_lock(run_id, seat_id, segment_idx, booking_id) VALUES (?,?,?,?)",
                    [(a["run_id"], seat_b, seg, a["booking_id"]) for seg in range(a["from_idx"], a["to_idx"])]
                    + [(b["run_id"], seat_a, seg, b["booking_id"]) for seg in range(b["from_idx"], b["to_idx"])],
                )
            except sqlite3.IntegrityError as exc:
                raise SeatTaken() from exc
            c.execute("UPDATE seat_swap SET status='ACCEPTED' WHERE swap_id=?", (swap_id,))
            c.execute("RELEASE SAVEPOINT swap_try")
        except Exception:
            c.execute("ROLLBACK TO SAVEPOINT swap_try")
            c.execute("RELEASE SAVEPOINT swap_try")
            raise
        c.execute(
            "INSERT INTO inbox(user_id, body) VALUES (?,?)",
            (a["user_id"], f"Seat swap {swap_id} was accepted."),
        )
        return {"swap_id": swap_id, "status": "ACCEPTED"}

    return write_tx(conn, tx)


def _berth(conn, seat_id: int) -> str:
    row = conn.execute(
        """
        SELECT c.coach_no, s.seat_no, s.berth_type
        FROM seat s JOIN coach c ON c.coach_id = s.coach_id
        WHERE s.seat_id=?
        """,
        (seat_id,),
    ).fetchone()
    return f"{row['coach_no']} berth {row['seat_no']} {row['berth_type']}"


def _fits(conn, user_id: int, seat_id: int) -> None:
    user = conn.execute("SELECT gender, age FROM user WHERE user_id=?", (user_id,)).fetchone()
    seat = conn.execute("SELECT quota FROM seat WHERE seat_id=?", (seat_id,)).fetchone()
    if seat["quota"] == "LADIES" and user["gender"] != "F":
        raise SwapError("That berth is reserved for ladies")
    if seat["quota"] == "SENIOR" and int(user["age"]) < 60:
        raise SwapError("That berth is reserved for senior citizens")
