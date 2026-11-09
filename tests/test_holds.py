from app.services.booking import expire_holds, hold_seat
from app.services.waitlist import join_waitlist
from tests.world import make_world


def test_expired_hold_frees_the_seat_and_promotes_the_waitlist(tmp_path):
    conn = make_world(tmp_path)
    booking_id = hold_seat(conn, 1, 1, 1, 1, 4)
    conn.execute(
        "UPDATE booking SET hold_expires_at=datetime('now','localtime','-2 minutes') WHERE booking_id=?",
        (booking_id,),
    )
    join_waitlist(conn, 4, 1, 1, 4, "SL", {"window": True, "scenic": True})
    expired = expire_holds(conn)
    assert expired == [booking_id]
    assert conn.execute("SELECT status FROM booking WHERE booking_id=?", (booking_id,)).fetchone()[0] == "EXPIRED"
    assert conn.execute(
        "SELECT COUNT(*) FROM seat_segment_lock WHERE booking_id=?", (booking_id,)
    ).fetchone()[0] == 0
    waiting = conn.execute("SELECT status, promoted_booking_id FROM waitlist").fetchone()
    assert waiting["status"] == "PROMOTED"
    promoted = conn.execute(
        "SELECT status, user_id FROM booking WHERE booking_id=?",
        (waiting["promoted_booking_id"],),
    ).fetchone()
    assert promoted["status"] == "HELD"
    assert promoted["user_id"] == 4
