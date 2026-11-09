import random
import sqlite3

import pytest

from app.db import write_tx
from app.services.booking import BookingError, SeatTaken, cancel_booking, hold_seat
from app.services.disputes import decide_dispute, raise_dispute
from app.services.payments import ledger_is_balanced, pay_booking
from tests.world import make_world


def test_unbalanced_ledger_cannot_close(tmp_path):
    conn = make_world(tmp_path)

    def tx(c):
        c.execute("INSERT INTO ledger_txn(txn_id, closed) VALUES ('bad', 0)")
        c.execute(
            "INSERT INTO ledger_entries(txn_id, account, amount, note) VALUES ('bad', 'escrow', 10, 'no pair')"
        )
        c.execute("UPDATE ledger_txn SET closed=1 WHERE txn_id='bad'")

    with pytest.raises(sqlite3.IntegrityError):
        write_tx(conn, tx)
    assert conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0] == 0


def test_ten_thousand_operations_stay_balanced(tmp_path):
    conn = make_world(tmp_path, seats=72)
    conn.execute(
        "UPDATE train_run SET run_date='2026-12-20', departure_at='2026-12-20 08:00:00'"
    )
    rng = random.Random(11)
    open_ids = []
    disputed = []
    ops = 0
    while ops < 10000:
        roll = rng.random()
        if disputed and roll < 0.08:
            dispute_id = disputed.pop()
            outcome = rng.choice(["REFUND_FULL", "REFUND_PARTIAL", "REJECTED"])
            decide_dispute(conn, dispute_id, 3, outcome, 40, "bench")
            ops += 1
            continue
        if open_ids and roll < 0.45:
            booking_id = open_ids.pop()
            if rng.random() < 0.2:
                result = raise_dispute(conn, 1, booking_id, "WRONG_DESCRIPTION", None, None)
                if result["status"] == "UNDER_REVIEW":
                    disputed.append(result["dispute_id"])
            else:
                cancel_booking(conn, booking_id, 1, now=__import__("datetime").datetime(2026, 12, 1))
            ops += 1
            continue
        seat_id = rng.randint(1, 72)
        try:
            booking_id = hold_seat(conn, 1, 1, seat_id, 1, 2)
            pay_booking(conn, booking_id, 1)
            open_ids.append(booking_id)
            ops += 2
        except (SeatTaken, BookingError):
            ops += 1
    for booking_id in open_ids:
        cancel_booking(conn, booking_id, 1, now=__import__("datetime").datetime(2026, 12, 1))
    for dispute_id in disputed:
        decide_dispute(conn, dispute_id, 3, "REJECTED", 0, "close")
    assert conn.execute("SELECT COUNT(*) FROM v_unbalanced_txns").fetchone()[0] == 0
    assert ledger_is_balanced(conn)
