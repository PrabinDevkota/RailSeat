"""Escrow state machine and the double-entry ledger."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.config import DISPUTE_WINDOW_HOURS
from app.db import write_tx
from app.services.booking import BookingError
from app.services.scoring import parse_dt

class PaymentError(Exception):
    pass


def post_entries(conn, txn_id: str, entries: list[tuple[str, float]], payment_id: int | None,
                 user_id: int | None, note: str) -> None:
    cleaned = [(acc, round(float(amt), 2)) for acc, amt in entries if round(float(amt), 2) != 0]
    if round(sum(amt for _, amt in cleaned), 2) != 0:
        raise ValueError("unbalanced ledger transaction")
    if not cleaned:
        return
    conn.execute(
        "INSERT INTO ledger_txn(txn_id, payment_id, closed) VALUES (?,?,0)",
        (txn_id, payment_id),
    )
    conn.executemany(
        """
        INSERT INTO ledger_entries(txn_id, account, amount, user_id, payment_id, note)
        VALUES (?,?,?,?,?,?)
        """,
        [(txn_id, acc, amt, user_id, payment_id, note) for acc, amt in cleaned],
    )
    conn.execute("UPDATE ledger_txn SET closed=1 WHERE txn_id=?", (txn_id,))


def _set_state(conn, payment_id: int, state: str) -> None:
    conn.execute(
        "UPDATE payment SET state=?, updated_at=datetime('now','localtime') WHERE payment_id=?",
        (state, payment_id),
    )


def account_balance(conn, payment_id: int, account: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM ledger_entries WHERE payment_id=? AND account=?",
        (payment_id, account),
    ).fetchone()
    return round(float(row["s"]), 2)


def hold_funds(conn, booking, gateway_ref: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO payment(booking_id, user_id, amount, state, gateway_ref)
        VALUES (?,?,?,'INITIATED',?)
        """,
        (booking["booking_id"], booking["user_id"], booking["fare"], gateway_ref),
    )
    payment_id = cur.lastrowid
    _set_state(conn, payment_id, "HELD")
    fare = round(float(booking["fare"]), 2)
    post_entries(
        conn,
        f"pay-{payment_id}-hold",
        [("gateway", -fare), ("escrow", fare)],
        payment_id,
        booking["user_id"],
        "escrow hold",
    )
    return payment_id


def pay_booking(conn, booking_id: int, user_id: int, fail: bool = False) -> dict:
    def tx(c):
        booking = c.execute("SELECT * FROM booking WHERE booking_id=?", (booking_id,)).fetchone()
        if booking is None:
            raise PaymentError("Unknown booking")
        if booking["user_id"] != user_id:
            raise PaymentError("This booking belongs to someone else")
        if booking["status"] != "HELD":
            raise PaymentError("Seat is not on hold")
        expired = c.execute(
            """
            SELECT 1 FROM booking
            WHERE booking_id=? AND hold_expires_at < datetime('now','localtime')
            """,
            (booking_id,),
        ).fetchone()
        if expired:
            raise PaymentError("Hold has expired")
        existing = c.execute(
            "SELECT payment_id, state FROM payment WHERE booking_id=? AND state='HELD'",
            (booking_id,),
        ).fetchone()
        if existing:
            return {"payment_id": existing["payment_id"], "state": "HELD", "already": True}
        if fail:
            raise PaymentError("Mock gateway declined the payment")
        gateway_ref = f"gw-{booking_id}-{int(datetime.now().timestamp())}"
        payment_id = hold_funds(c, booking, gateway_ref)
        c.execute(
            "UPDATE booking SET status='CONFIRMED', hold_expires_at=NULL WHERE booking_id=?",
            (booking_id,),
        )
        c.execute(
            "INSERT INTO inbox(user_id, body) VALUES (?, ?)",
            (user_id, f"Payment for booking {booking_id} is in escrow (₹{booking['fare']:.0f})."),
        )
        return {"payment_id": payment_id, "state": "HELD", "gateway_ref": gateway_ref}

    return write_tx(conn, tx)


def _move_out(conn, payment, pct: float, note: str) -> dict:
    """Return money sitting in escrow or revenue. pct is the share that goes to the passenger."""
    escrow = account_balance(conn, payment["payment_id"], "escrow")
    revenue = account_balance(conn, payment["payment_id"], "railway_revenue")
    held = round(escrow + revenue, 2)
    if held <= 0:
        return {"refund": 0.0, "fee": 0.0}
    refund = round(held * pct, 2)
    fee = round(held - refund, 2)
    source = "escrow" if escrow >= held - 0.001 and escrow > 0 else "railway_revenue"
    entries = []
    if refund:
        entries.append((source, -refund))
        entries.append(("user_wallet", refund))
    if fee and source == "escrow":
        entries.append(("escrow", -fee))
        entries.append(("railway_revenue", fee))
    if entries:
        post_entries(
            conn,
            f"pay-{payment['payment_id']}-{note}",
            entries,
            payment["payment_id"],
            payment["user_id"],
            note,
        )
    return {"refund": refund, "fee": fee, "source": source}


def refund_rule(conn, hours_before: float) -> float:
    row = conn.execute(
        """
        SELECT refund_pct FROM refund_rule
        WHERE ? >= min_hours_before
          AND (max_hours_before IS NULL OR ? < max_hours_before)
        ORDER BY min_hours_before DESC
        LIMIT 1
        """,
        (hours_before, hours_before),
    ).fetchone()
    if row is None:
        raise PaymentError("No refund rule matches")
    return float(row["refund_pct"])


def passenger_departure(conn, booking) -> datetime:
    row = conn.execute(
        """
        SELECT tr.departure_at, rs.dep_min
        FROM train_run tr
        JOIN route_stop rs ON rs.train_id = tr.train_id AND rs.stop_idx = ?
        WHERE tr.run_id = ?
        """,
        (booking["from_idx"], booking["run_id"]),
    ).fetchone()
    return parse_dt(row["departure_at"]) + timedelta(minutes=row["dep_min"] or 0)


def refund_on_cancel(conn, payment, booking, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    hours = (passenger_departure(conn, booking) - now).total_seconds() / 3600
    pct = refund_rule(conn, hours) / 100.0
    _set_state(conn, payment["payment_id"], "CANCELLED_BY_USER")
    money = _move_out(conn, payment, pct, "cancel")
    _set_state(conn, payment["payment_id"], "REFUNDED")
    money["pct"] = round(pct * 100, 2)
    money["hours_before"] = round(hours, 2)
    return money


def refund_train_cancelled(conn, payment) -> dict:
    _set_state(conn, payment["payment_id"], "TRAIN_CANCELLED")
    money = _move_out(conn, payment, 1.0, "train-cancel")
    _set_state(conn, payment["payment_id"], "REFUNDED")
    money["pct"] = 100
    return money


def capture_payment(conn, payment, booking_id: int) -> None:
    fare = round(float(payment["amount"]), 2)
    post_entries(
        conn,
        f"pay-{payment['payment_id']}-capture",
        [("escrow", -fare), ("railway_revenue", fare)],
        payment["payment_id"],
        payment["user_id"],
        "capture after dispute window",
    )
    _set_state(conn, payment["payment_id"], "CAPTURED")
    conn.execute("UPDATE booking SET status='COMPLETED' WHERE booking_id=?", (booking_id,))


def capture_due(conn, as_of: datetime | None = None, force_booking_id: int | None = None) -> list[int]:
    as_of = as_of or datetime.now()

    def tx(c):
        rows = c.execute(
            """
            SELECT p.payment_id, p.amount, p.user_id, p.state, p.booking_id,
                   b.to_idx, tr.departure_at, tr.train_id, rs.arr_min
            FROM payment p
            JOIN booking b ON b.booking_id = p.booking_id
            JOIN train_run tr ON tr.run_id = b.run_id
            JOIN route_stop rs ON rs.train_id = tr.train_id AND rs.stop_idx = b.to_idx
            WHERE p.state = 'HELD' AND b.status = 'CONFIRMED'
            """
        ).fetchall()
        done = []
        for row in rows:
            if force_booking_id is not None and row["booking_id"] != force_booking_id:
                continue
            arrival = parse_dt(row["departure_at"]) + timedelta(minutes=row["arr_min"] or 0)
            if force_booking_id is None and as_of < arrival + timedelta(hours=DISPUTE_WINDOW_HOURS):
                continue
            capture_payment(c, row, row["booking_id"])
            done.append(row["booking_id"])
        return done

    return write_tx(conn, tx)


def wallet_balance(conn, user_id: int) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM ledger_entries WHERE user_id=? AND account='user_wallet'",
        (user_id,),
    ).fetchone()
    return round(float(row["s"]), 2)


def ledger_is_balanced(conn) -> bool:
    bad = conn.execute("SELECT COUNT(*) AS n FROM v_unbalanced_txns").fetchone()["n"]
    total = conn.execute("SELECT COALESCE(ROUND(SUM(amount), 2), 0) AS s FROM ledger_entries").fetchone()["s"]
    return bad == 0 and abs(float(total)) < 0.001
