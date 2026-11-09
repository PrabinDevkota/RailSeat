from app.services.allocation import allocate
from app.services.readmodel import booking_detail
from tests.world import make_world


def test_preference_allocation_picks_the_scenic_window(tmp_path):
    conn = make_world(tmp_path)
    result = allocate(
        conn, 1, 1, 1, 4, "SL",
        {"window": True, "scenic": True, "shade": False},
        "L2",
    )
    detail = booking_detail(conn, result["booking_id"])
    assert detail["is_window"] == 1
    assert detail["travel_side"] == "R"
    assert "landmark" in result["explain"].lower() or "side" in result["explain"].lower()


def test_random_baseline_still_respects_the_lock(tmp_path):
    conn = make_world(tmp_path, seats=16)
    first = allocate(conn, 1, 1, 1, 4, "SL", {}, "random")
    second = allocate(conn, 4, 1, 1, 4, "SL", {}, "random")
    assert first["seat_id"] != second["seat_id"]
