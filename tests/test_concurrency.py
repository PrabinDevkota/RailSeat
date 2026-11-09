from concurrent.futures import ThreadPoolExecutor

from app.db import connect
from app.services.booking import SeatTaken, hold_seat
from tests.world import make_world


def test_five_hundred_attempts_one_winner(tmp_path):
    conn = make_world(tmp_path)
    conn.close()
    db = tmp_path / "rail.db"

    def attempt(_n):
        local = connect(db)
        local.execute("PRAGMA busy_timeout=30000")
        try:
            hold_seat(local, 1, 1, 1, 1, 4)
            return "ok"
        except SeatTaken:
            return "taken"
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=500) as pool:
        results = list(pool.map(attempt, range(500)))
    assert results.count("ok") == 1
    assert results.count("taken") == 499
    check = connect(db)
    owners = check.execute(
        "SELECT DISTINCT booking_id FROM seat_segment_lock WHERE seat_id=1"
    ).fetchall()
    assert len(owners) == 1
