from datetime import datetime

import pytest

from app.db import write_tx
from app.services.booking import cancel_booking, hold_seat
from app.services.disputes import raise_dispute
from app.services.payments import capture_due, ledger_is_balanced, pay_booking
from tests.world import make_world


def _far_future(conn):
    conn.execute(
        "UPDATE train_run SET run_date='2026-12-20', departure_at='2026-12-20 08:00:00' WHERE run_id=1"
    )


def test_illegal_payment_transition_is_rejected(tmp_path):
    conn = make_world(tmp_path)
    _far_future(conn)
    booking_id = hold_seat(conn, 1, 1, 1, 1, 3)
    pay_booking(conn, booking_id, 1)

    def jump(c):
        c.execute("UPDATE payment SET state='REFUNDED'")

    with pytest.raises(Exception):
        write_tx(conn, jump)
    assert conn.execute("SELECT state FROM payment").fetchone()[0] == "HELD"


def test_cancel_before_departure_refunds_in_full(tmp_path):
    conn = make_world(tmp_path)
    _far_future(conn)
    booking_id = hold_seat(conn, 1, 1, 1, 1, 3)
    pay_booking(conn, booking_id, 1)
    result = cancel_booking(conn, booking_id, 1, now=datetime(2026, 12, 1, 8, 0, 0))
    assert result["refund"]["pct"] == 100
    assert conn.execute("SELECT state FROM payment").fetchone()[0] == "REFUNDED"
    assert conn.execute("SELECT status FROM booking WHERE booking_id=?", (booking_id,)).fetchone()[0] == "CANCELLED"
    assert ledger_is_balanced(conn)
    wallet = conn.execute(
        "SELECT ROUND(SUM(amount),2) FROM ledger_entries WHERE account='user_wallet'"
    ).fetchone()[0]
    fare = conn.execute("SELECT amount FROM payment").fetchone()[0]
    assert wallet == fare


def test_capture_after_the_dispute_window(tmp_path):
    conn = make_world(tmp_path)
    conn.execute("UPDATE train_run SET departure_at='2020-01-01 08:00:00', run_date='2020-01-01'")
    booking_id = hold_seat(conn, 1, 1, 1, 1, 4)
    pay_booking(conn, booking_id, 1)
    done = capture_due(conn, as_of=datetime(2020, 1, 5, 12, 0, 0))
    assert done == [booking_id]
    assert conn.execute("SELECT state FROM payment").fetchone()[0] == "CAPTURED"
    assert conn.execute("SELECT status FROM booking WHERE booking_id=?", (booking_id,)).fetchone()[0] == "COMPLETED"
    assert ledger_is_balanced(conn)


def test_cancelled_train_dispute_refunds_automatically(tmp_path):
    conn = make_world(tmp_path)
    _far_future(conn)
    booking_id = hold_seat(conn, 1, 1, 3, 1, 4)
    pay_booking(conn, booking_id, 1)
    conn.execute("UPDATE train_run SET status='CANCELLED' WHERE run_id=1")
    result = raise_dispute(conn, 1, booking_id, "TRAIN_CANCELLED", "The board said cancelled", None)
    assert result["auto"] is True
    assert result["outcome"] == "REFUND_FULL"
    assert conn.execute("SELECT state FROM payment").fetchone()[0] == "RESOLVED_REFUND"
    assert ledger_is_balanced(conn)
