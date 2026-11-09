import subprocess
import sys
import textwrap
from pathlib import Path

from app.db import connect
from tests.world import make_world

ROOT = str(Path(__file__).resolve().parents[1])


def test_crash_mid_booking_leaves_nothing(tmp_path):
    conn = make_world(tmp_path)
    db = tmp_path / "rail.db"
    conn.close()
    script = textwrap.dedent(
        f"""
        import os, sys
        sys.path.insert(0, {ROOT!r})
        from app.db import connect
        from app.services.booking import book_seat_tx
        conn = connect({str(db)!r})
        conn.execute("BEGIN IMMEDIATE")
        book_seat_tx(conn, 1, 1, 1, 1, 4, 100.0, "crash")
        os._exit(137)
        """
    )
    subprocess.run([sys.executable, "-c", script], check=False)
    again = connect(db)
    assert again.execute("SELECT COUNT(*) FROM booking").fetchone()[0] == 0
    assert again.execute("SELECT COUNT(*) FROM seat_segment_lock").fetchone()[0] == 0


def test_commit_survives_a_later_crash(tmp_path):
    from app.services.booking import hold_seat

    conn = make_world(tmp_path)
    hold_seat(conn, 1, 1, 3, 1, 3)
    conn.close()
    db = tmp_path / "rail.db"
    script = textwrap.dedent(
        f"""
        import os, sys
        sys.path.insert(0, {ROOT!r})
        from app.db import connect
        conn = connect({str(db)!r})
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO inbox(user_id, body) VALUES (1, 'not committed')")
        os._exit(137)
        """
    )
    subprocess.run([sys.executable, "-c", script], check=False)
    again = connect(db)
    assert again.execute("SELECT COUNT(*) FROM booking").fetchone()[0] == 1
    assert again.execute("SELECT COUNT(*) FROM inbox").fetchone()[0] == 0
