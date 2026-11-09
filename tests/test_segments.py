import pytest

from app.services.booking import BookingError, SeatTaken, hold_seat
from app.services.scoring import travel_side
from tests.world import make_world


def test_adjacent_segments_share_a_seat_and_overlap_does_not(tmp_path):
    conn = make_world(tmp_path)
    first = hold_seat(conn, 1, 1, 1, 1, 2)
    second = hold_seat(conn, 4, 1, 1, 2, 4)
    assert first != second
    with pytest.raises(SeatTaken):
        hold_seat(conn, 2, 1, 1, 1, 4)
    locks = conn.execute(
        "SELECT segment_idx, booking_id FROM seat_segment_lock WHERE seat_id=1 ORDER BY segment_idx"
    ).fetchall()
    assert [row["segment_idx"] for row in locks] == [1, 2, 3]
    assert [row["booking_id"] for row in locks] == [first, second, second]


def test_orientation_flips_the_travel_side():
    assert travel_side("L", 1) == "L"
    assert travel_side("L", -1) == "R"
    assert travel_side("R", -1) == "L"


def test_ladies_and_senior_quota(tmp_path):
    conn = make_world(tmp_path)
    # Bay 2 lower berths are senior quota: seat 17 and 20 in a 24-seat coach.
    with pytest.raises(BookingError):
        hold_seat(conn, 4, 1, 17, 1, 4)
    senior = hold_seat(conn, 2, 1, 17, 1, 4)
    assert senior
    # Bay 1 lower berths are ladies quota: seats 9 and 12.
    with pytest.raises(BookingError):
        hold_seat(conn, 4, 1, 9, 1, 4)
    assert hold_seat(conn, 1, 1, 9, 1, 4)


def test_view_score_follows_the_scenic_side(tmp_path):
    from app.services.readmodel import seatmap

    conn = make_world(tmp_path)
    data = seatmap(conn, 1, 1, 4)
    by_no = {seat["seat_no"]: seat for seat in data["seats"]}
    # Seat 7 is a side lower on the right wall. Seat 1 is a lower on the left wall.
    assert by_no[7]["travel_side"] == "R"
    assert by_no[7]["view_score"] > by_no[1]["view_score"]
    assert by_no[2]["view_score"] < by_no[1]["view_score"]  # middle berth, same side, not a window
