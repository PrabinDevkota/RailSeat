import pytest

from app.services.payments import capture_due, pay_booking
from app.services.ratings import RatingError, add_rating
from app.services.scoring import bayes_rating
from app.services.booking import hold_seat
from tests.world import make_world


def _complete(conn, user_id, seat_id, start, end):
    booking_id = hold_seat(conn, user_id, 1, seat_id, start, end)
    pay_booking(conn, booking_id, user_id)
    conn.execute("UPDATE train_run SET departure_at='2020-01-01 08:00:00', run_date='2020-01-01'")
    capture_due(conn, as_of=__import__("datetime").datetime(2020, 1, 5))
    return booking_id


def test_only_a_completed_booking_can_be_rated(tmp_path):
    conn = make_world(tmp_path)
    booking_id = hold_seat(conn, 1, 1, 1, 1, 3)
    with pytest.raises(Exception):
        add_rating(conn, 1, booking_id, 5, ["good_view"])
    pay_booking(conn, booking_id, 1)
    with pytest.raises(Exception):
        add_rating(conn, 1, booking_id, 5, ["good_view"])


def test_completed_rating_updates_the_summary(tmp_path):
    conn = make_world(tmp_path)
    booking_id = _complete(conn, 1, 1, 1, 3)
    rating_id = add_rating(conn, 1, booking_id, 5, ["good_view"])
    assert rating_id
    summary = conn.execute(
        "SELECT n, sum_stars, good_view FROM seat_rating_summary WHERE from_idx=0 AND seat_no=1"
    ).fetchone()
    assert summary["n"] == 1
    assert summary["sum_stars"] == 5
    assert summary["good_view"] == 1
    bayes = conn.execute(
        "SELECT bayes FROM v_seat_quality WHERE from_idx=0 AND seat_no=1"
    ).fetchone()["bayes"]
    assert bayes == pytest.approx(bayes_rating(5, 1, 5), rel=1e-6)


def test_bayesian_average_does_not_let_one_five_win():
    lone = bayes_rating(5, 1, 3.5)
    many = bayes_rating(4.6 * 40, 40, 3.5)
    assert many > lone
    assert lone < 4.6


def test_a_second_rating_of_the_same_booking_is_rejected(tmp_path):
    conn = make_world(tmp_path)
    booking_id = _complete(conn, 1, 1, 1, 3)
    add_rating(conn, 1, booking_id, 4, [])
    with pytest.raises(Exception):
        add_rating(conn, 1, booking_id, 5, [])
