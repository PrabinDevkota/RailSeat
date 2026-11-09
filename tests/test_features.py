"""Seat swap and the journey-guide window from the product spec."""

import pytest

from app.services.booking import hold_seat
from app.services.readmodel import journey_guide
from app.services.swaps import SwapError, accept_swap, propose_swap
from tests.world import make_world


def _confirmed(conn, user_id, seat_id):
    booking_id = hold_seat(conn, user_id, 1, seat_id, 1, 4)
    conn.execute("UPDATE booking SET status='CONFIRMED', hold_expires_at=NULL WHERE booking_id=?", (booking_id,))
    return booking_id


def test_confirmed_passengers_exchange_berths(tmp_path):
    conn = make_world(tmp_path)
    asha = _confirmed(conn, 1, 2)
    arun = _confirmed(conn, 4, 3)
    swap_id = propose_swap(conn, 1, asha, arun)
    assert conn.execute("SELECT status FROM seat_swap WHERE swap_id=?", (swap_id,)).fetchone()[0] == "PROPOSED"
    assert "Asha" in conn.execute("SELECT body FROM inbox WHERE user_id=4").fetchone()[0]
    assert accept_swap(conn, 4, swap_id)["status"] == "ACCEPTED"
    assert conn.execute("SELECT seat_id FROM booking WHERE booking_id=?", (asha,)).fetchone()[0] == 3
    assert conn.execute("SELECT seat_id FROM booking WHERE booking_id=?", (arun,)).fetchone()[0] == 2
    locks = conn.execute(
        "SELECT booking_id, seat_id, COUNT(*) AS n FROM seat_segment_lock GROUP BY booking_id, seat_id"
    ).fetchall()
    assert {(row["booking_id"], row["seat_id"], row["n"]) for row in locks} == {(asha, 3, 3), (arun, 2, 3)}


def test_a_man_cannot_take_a_ladies_berth_by_swap(tmp_path):
    conn = make_world(tmp_path)
    asha = _confirmed(conn, 1, 9)
    arun = _confirmed(conn, 4, 3)
    with pytest.raises(SwapError, match="ladies"):
        propose_swap(conn, 1, asha, arun)


def test_guide_names_the_station_and_a_time_window(tmp_path):
    conn = make_world(tmp_path)
    guide = journey_guide(conn, 1, 1, 4, 2)
    assert guide["items"]
    assert all(item["after_station"] == "Beta" and item["time"] < item["until"] for item in guide["items"])
    assert any("Bhor Ghat" in item["description"] for item in guide["items"])
