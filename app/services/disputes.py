"""Rules first, arbitrator second. Money moves in the same transaction as the decision."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.db import write_tx
from app.services.booking import release_locks
from app.services.payments import _move_out, _set_state

class DisputeError(Exception):
    pass


@dataclass
class Decision:
    outcome: str
    pct: float
    auto: bool
    reason: str


def defect_reports(conn, seat_id: int, run_id: int, exclude_user: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT r.user_id) AS n
        FROM seat_rating r
        JOIN seat_rating_tag t ON t.rating_id = r.rating_id
        WHERE r.seat_id=? AND r.run_id=? AND r.user_id != ?
          AND t.tag = 'broken_window'
        """,
        (seat_id, run_id, exclude_user),
    ).fetchone()
    return int(row["n"])


def evaluate_dispute(conn, dispute) -> Decision | None:
    run = conn.execute("SELECT status FROM train_run WHERE run_id=?", (dispute["run_id"],)).fetchone()
    if run["status"] == "CANCELLED":
        return Decision("REFUND_FULL", 100, True, "Train cancelled")
    if dispute["reason"] == "SEAT_UNUSABLE":
        reports = defect_reports(conn, dispute["seat_id"], dispute["run_id"], dispute["user_id"])
        if reports >= 2:
            return Decision("REFUND_PARTIAL", 50, True, "Defect corroborated by other travellers")
    return None


def _apply_money(conn, payment, booking, decision: Decision) -> dict:
    if decision.outcome == "REJECTED":
        # A confirmed ticket that has not departed goes back to escrow.
        # A completed journey keeps the fare: escrow, if any, moves to revenue.
        if booking["status"] == "CONFIRMED":
            _set_state(conn, payment["payment_id"], "HELD")
            return {"refund": 0.0, "pct": 0}
        if payment["state"] in ("DISPUTED", "UNDER_REVIEW", "HELD"):
            from app.services.payments import account_balance, post_entries
            if account_balance(conn, payment["payment_id"], "escrow") > 0:
                fare = round(float(payment["amount"]), 2)
                post_entries(
                    conn,
                    f"pay-{payment['payment_id']}-reject",
                    [("escrow", -fare), ("railway_revenue", fare)],
                    payment["payment_id"],
                    payment["user_id"],
                    "dispute rejected",
                )
        _set_state(conn, payment["payment_id"], "RESOLVED_REJECTED")
        return {"refund": 0.0, "pct": 0}
    pct = decision.pct / 100.0
    state = "RESOLVED_REFUND" if decision.outcome == "REFUND_FULL" else "RESOLVED_PARTIAL"
    money = _move_out(conn, payment, pct, "dispute")
    _set_state(conn, payment["payment_id"], state)
    if decision.outcome == "REFUND_FULL" and booking["status"] in ("HELD", "CONFIRMED"):
        release_locks(conn, booking["booking_id"])
        conn.execute(
            "UPDATE booking SET status='CANCELLED' WHERE booking_id=?",
            (booking["booking_id"],),
        )
    money["pct"] = decision.pct
    return money


def _record_decision(conn, dispute_id: int, decision: Decision, arbitrator_id: int | None) -> None:
    conn.execute(
        """
        INSERT INTO dispute_decision(dispute_id, outcome, refund_pct, auto, reason, arbitrator_id)
        VALUES (?,?,?,?,?,?)
        """,
        (dispute_id, decision.outcome, decision.pct, 1 if decision.auto else 0, decision.reason, arbitrator_id),
    )
    status = "AUTO_RESOLVED" if decision.auto else "RESOLVED"
    conn.execute("UPDATE dispute SET status=? WHERE dispute_id=?", (status, dispute_id))


def raise_dispute(conn, user_id: int, booking_id: int, reason: str, detail: str | None,
                  photo_path: str | None) -> dict:
    def tx(c):
        booking = c.execute("SELECT * FROM booking WHERE booking_id=?", (booking_id,)).fetchone()
        if booking is None or booking["user_id"] != user_id:
            raise DisputeError("Booking not found")
        if booking["status"] not in ("CONFIRMED", "COMPLETED"):
            raise DisputeError("Only a confirmed or completed journey can be disputed")
        payment = c.execute(
            "SELECT * FROM payment WHERE booking_id=? ORDER BY payment_id DESC LIMIT 1",
            (booking_id,),
        ).fetchone()
        if payment is None or payment["state"] not in ("HELD", "CAPTURED"):
            raise DisputeError("Nothing is held for this booking")
        open_row = c.execute(
            "SELECT 1 FROM dispute WHERE booking_id=? AND status IN ('OPEN','UNDER_REVIEW')",
            (booking_id,),
        ).fetchone()
        if open_row:
            raise DisputeError("A dispute is already open")
        seat_id = booking["seat_id"]
        run_id = booking["run_id"]
        cur = c.execute(
            """
            INSERT INTO dispute(
              booking_id, user_id, payment_id, run_id, seat_id, reason, detail, photo_path, status
            ) VALUES (?,?,?,?,?,?,?,?,'OPEN')
            """,
            (booking_id, user_id, payment["payment_id"], run_id, seat_id, reason, detail, photo_path),
        )
        dispute_id = cur.lastrowid
        _set_state(c, payment["payment_id"], "DISPUTED")
        dispute = c.execute("SELECT * FROM dispute WHERE dispute_id=?", (dispute_id,)).fetchone()
        decision = evaluate_dispute(c, dispute)
        if decision is None:
            sla = (datetime.now() + timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
            c.execute(
                "UPDATE dispute SET status='UNDER_REVIEW', sla_due_at=? WHERE dispute_id=?",
                (sla, dispute_id),
            )
            _set_state(c, payment["payment_id"], "UNDER_REVIEW")
            return {"dispute_id": dispute_id, "status": "UNDER_REVIEW", "auto": False}
        _apply_money(c, payment, booking, decision)
        _record_decision(c, dispute_id, decision, None)
        c.execute(
            "INSERT INTO inbox(user_id, body) VALUES (?,?)",
            (user_id, f"Dispute {dispute_id} resolved automatically: {decision.reason}."),
        )
        return {
            "dispute_id": dispute_id,
            "status": "AUTO_RESOLVED",
            "auto": True,
            "outcome": decision.outcome,
            "reason": decision.reason,
        }

    return write_tx(conn, tx)


def decide_dispute(conn, dispute_id: int, arbitrator_id: int, outcome: str, pct: float | None,
                   reason: str) -> dict:
    if outcome not in ("REFUND_FULL", "REFUND_PARTIAL", "REJECTED"):
        raise DisputeError("Unknown outcome")
    if outcome == "REFUND_FULL":
        pct = 100
    elif outcome == "REJECTED":
        pct = 0
    elif pct is None:
        pct = 50

    def tx(c):
        dispute = c.execute("SELECT * FROM dispute WHERE dispute_id=?", (dispute_id,)).fetchone()
        if dispute is None or dispute["status"] not in ("OPEN", "UNDER_REVIEW"):
            raise DisputeError("Dispute is not waiting for a decision")
        role = c.execute("SELECT role FROM user WHERE user_id=?", (arbitrator_id,)).fetchone()
        if role is None or role["role"] not in ("ARBITRATOR", "ADMIN"):
            raise DisputeError("Not an arbitrator")
        payment = c.execute("SELECT * FROM payment WHERE payment_id=?", (dispute["payment_id"],)).fetchone()
        booking = c.execute("SELECT * FROM booking WHERE booking_id=?", (dispute["booking_id"],)).fetchone()
        decision = Decision(outcome, float(pct), False, reason or "Arbitrator decision")
        money = _apply_money(c, payment, booking, decision)
        _record_decision(c, dispute_id, decision, arbitrator_id)
        c.execute(
            "INSERT INTO inbox(user_id, body) VALUES (?,?)",
            (dispute["user_id"], f"Dispute {dispute_id}: {decision.reason}."),
        )
        return {"dispute_id": dispute_id, "outcome": outcome, "refund": money.get("refund", 0)}

    return write_tx(conn, tx)


def cancel_run(conn, run_id: int, actor_id: int) -> dict:
    """Admin marks a run cancelled and refunds every held ticket in one transaction."""

    def tx(c):
        role = c.execute("SELECT role FROM user WHERE user_id=?", (actor_id,)).fetchone()
        if role is None or role["role"] != "ADMIN":
            raise DisputeError("Admin only")
        run = c.execute("SELECT * FROM train_run WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise DisputeError("Unknown run")
        c.execute("UPDATE train_run SET status='CANCELLED' WHERE run_id=?", (run_id,))
        rows = c.execute(
            """
            SELECT b.*, p.payment_id, p.amount, p.user_id AS pay_user, p.state
            FROM booking b
            LEFT JOIN payment p ON p.booking_id = b.booking_id AND p.state='HELD'
            WHERE b.run_id=? AND b.status IN ('HELD', 'CONFIRMED')
            """,
            (run_id,),
        ).fetchall()
        refunded = 0
        from app.services.payments import refund_train_cancelled
        for row in rows:
            if row["payment_id"]:
                payment = c.execute("SELECT * FROM payment WHERE payment_id=?", (row["payment_id"],)).fetchone()
                refund_train_cancelled(c, payment)
                refunded += 1
            release_locks(c, row["booking_id"])
            if row["status"] in ("HELD", "CONFIRMED"):
                c.execute("UPDATE booking SET status='CANCELLED' WHERE booking_id=?", (row["booking_id"],))
            c.execute(
                "INSERT INTO inbox(user_id, body) VALUES (?,?)",
                (row["user_id"], f"Run {run_id} was cancelled. A full refund has been posted if you had paid."),
            )
        c.execute(
            "INSERT INTO audit_log(entity, entity_id, action, detail, actor_id) VALUES (?,?,?,?,?)",
            ("train_run", str(run_id), "CANCELLED", f"refunded {refunded}", actor_id),
        )
        return {"run_id": run_id, "bookings": len(rows), "refunded": refunded}

    return write_tx(conn, tx)
