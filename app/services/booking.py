"""Segment locks, holds, cancellation. The database, not this file, rejects a double book."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from app.config import FARE_PER_KM, HOLD_MINUTES, MIN_FARE
from app.db import write_tx

class SeatTaken(Exception):
    pass


class BookingError(Exception):
    pass


def compute_fare(distance_km: float, coach_class: str) -> float:
    raw = float(distance_km) * FARE_PER_KM[coach_class]
    return round(max(MIN_FARE[coach_class], raw), 2)


def journey_facts(conn, run_id: int, seat_id: int, from_idx: int, to_idx: int):
    row = conn.execute(
        """
        SELECT co.coach_class, co.coach_no, s.seat_no, s.quota, s.broken,
               s.berth_type, s.physical_side, s.is_window,
               rs2.distance_km - rs1.distance_km AS km,
               tr.departure_at, tr.status AS run_status, tr.train_id,
               rs1.dep_min
        FROM seat s
        JOIN coach co ON co.coach_id = s.coach_id
        JOIN train_run tr ON tr.run_id = co.run_id
        JOIN route_stop rs1 ON rs1.train_id = tr.train_id AND rs1.stop_idx = ?
        JOIN route_stop rs2 ON rs2.train_id = tr.train_id AND rs2.stop_idx = ?
        WHERE s.seat_id = ? AND co.run_id = ?
        """,
        (from_idx, to_idx, seat_id, run_id),
    ).fetchone()
    if row is None:
        raise BookingError("Unknown seat or journey")
    if from_idx >= to_idx:
        raise BookingError("Journey is empty")
    return row


def book_seat_tx(
    conn,
    user_id: int,
    run_id: int,
    seat_id: int,
    from_idx: int,
    to_idx: int,
    fare: float,
    explain: str | None = None,
    ttl_minutes: int = HOLD_MINUTES,
) -> int:
    """Insert the booking and its segment rows. Caller owns the transaction.

    A savepoint keeps a failed attempt from poisoning a larger transaction
    (waitlist promotion runs in the same transaction as the cancellation).
    """
    conn.execute("SAVEPOINT seat_try")
    try:
        cur = conn.execute(
            """
            INSERT INTO booking(
              user_id, run_id, seat_id, from_idx, to_idx, status, hold_expires_at, fare, explain
            ) VALUES (?,?,?,?,?,'HELD', datetime('now','localtime', ?), ?, ?)
            """,
            (user_id, run_id, seat_id, from_idx, to_idx, f"+{ttl_minutes} minutes", fare, explain),
        )
        booking_id = cur.lastrowid
        try:
            conn.executemany(
                "INSERT INTO seat_segment_lock(run_id, seat_id, segment_idx, booking_id) VALUES (?,?,?,?)",
                [(run_id, seat_id, seg, booking_id) for seg in range(from_idx, to_idx)],
            )
        except sqlite3.IntegrityError as exc:
            raise SeatTaken() from exc
        conn.execute("RELEASE SAVEPOINT seat_try")
        return booking_id
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT seat_try")
        conn.execute("RELEASE SAVEPOINT seat_try")
        raise


def assert_allowed(conn, user_id: int, run_id: int, seat_id: int, from_idx: int, to_idx: int):
    user = conn.execute("SELECT * FROM user WHERE user_id=?", (user_id,)).fetchone()
    if user is None:
        raise BookingError("Unknown passenger")
    facts = journey_facts(conn, run_id, seat_id, from_idx, to_idx)
    if facts["run_status"] not in ("SCHEDULED", "RUNNING"):
        raise BookingError("This train is not open for booking")
    if facts["broken"]:
        raise BookingError("This berth is flagged unusable")
    flagged = conn.execute(
        """
        SELECT 1 FROM maintenance_flag
        WHERE train_id=? AND coach_no=? AND seat_no=? AND active=1
        """,
        (facts["train_id"], facts["coach_no"], facts["seat_no"]),
    ).fetchone()
    if flagged:
        raise BookingError("This berth is under a maintenance flag")
    if facts["quota"] == "LADIES" and user["gender"] != "F":
        raise BookingError("Ladies quota berth")
    if facts["quota"] == "SENIOR" and user["age"] < 60:
        raise BookingError("Senior citizen quota berth")
    fare = compute_fare(facts["km"], facts["coach_class"])
    return user, facts, fare


def hold_seat(conn, user_id: int, run_id: int, seat_id: int, from_idx: int, to_idx: int,
              explain: str | None = None, ttl_minutes: int = HOLD_MINUTES) -> int:
    _, _, fare = assert_allowed(conn, user_id, run_id, seat_id, from_idx, to_idx)

    def tx(c):
        return book_seat_tx(c, user_id, run_id, seat_id, from_idx, to_idx, fare, explain, ttl_minutes)

    return write_tx(conn, tx)


def release_locks(conn, booking_id: int) -> None:
    conn.execute("DELETE FROM seat_segment_lock WHERE booking_id=?", (booking_id,))


def expire_holds(conn) -> list[int]:
    from app.services.waitlist import promote_for_run

    def tx(c):
        rows = c.execute(
            """
            SELECT booking_id, run_id FROM booking
            WHERE status='HELD' AND hold_expires_at < datetime('now','localtime')
            """
        ).fetchall()
        for row in rows:
            release_locks(c, row["booking_id"])
            c.execute("UPDATE booking SET status='EXPIRED' WHERE booking_id=?", (row["booking_id"],))
            promote_for_run(c, row["run_id"])
        return [row["booking_id"] for row in rows]

    return write_tx(conn, tx)


def cancel_booking(conn, booking_id: int, user_id: int, now: datetime | None = None) -> dict:
    from app.services.payments import refund_on_cancel
    from app.services.waitlist import promote_for_run

    def tx(c):
        booking = c.execute("SELECT * FROM booking WHERE booking_id=?", (booking_id,)).fetchone()
        if booking is None:
            raise BookingError("Unknown booking")
        if booking["user_id"] != user_id:
            raise BookingError("This booking belongs to someone else")
        if booking["status"] not in ("HELD", "CONFIRMED"):
            raise BookingError("This booking cannot be cancelled")
        payment = c.execute(
            "SELECT * FROM payment WHERE booking_id=? ORDER BY payment_id DESC LIMIT 1",
            (booking_id,),
        ).fetchone()
        money = None
        if payment is not None and payment["state"] == "HELD":
            money = refund_on_cancel(c, payment, booking, now)
        elif payment is not None and payment["state"] not in ("INITIATED",):
            raise BookingError("Payment is no longer held. Raise a dispute instead of cancelling.")
        release_locks(c, booking_id)
        c.execute("UPDATE booking SET status='CANCELLED' WHERE booking_id=?", (booking_id,))
        promoted = promote_for_run(c, booking["run_id"])
        return {"booking_id": booking_id, "refund": money, "promoted": promoted}

    return write_tx(conn, tx)
