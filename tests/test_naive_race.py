import threading
from concurrent.futures import ThreadPoolExecutor

from app.db import connect
from app.services import naive
from app.services.naive import ensure_table, naive_book
from tests.world import make_world


def test_check_then_insert_double_books(tmp_path):
    conn = make_world(tmp_path)
    ensure_table(conn)
    conn.close()
    db = tmp_path / "rail.db"
    # Every attempt must finish its read before any attempt writes, or SQLite
    # can serialize the race and the demonstration does not fail.
    barrier = threading.Barrier(40)
    original = naive.time.sleep

    def release(_seconds):
        barrier.wait(timeout=5)

    naive.time.sleep = release

    def attempt(n):
        local = connect(db)
        try:
            return naive_book(local, n, 1, 1, 1, 3)
        finally:
            local.close()

    try:
        with ThreadPoolExecutor(max_workers=40) as pool:
            results = list(pool.map(attempt, range(40)))
    finally:
        naive.time.sleep = original
    assert sum(1 for ok in results if ok) > 1
    check = connect(db)
    rows = check.execute("SELECT COUNT(*) AS n FROM naive_lock WHERE segment_idx=1").fetchone()["n"]
    assert rows > 1
