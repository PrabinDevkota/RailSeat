from tests.world import make_world


def test_explain_names_the_segment_index(tmp_path):
    from app.services.booking import hold_seat

    conn = make_world(tmp_path)
    hold_seat(conn, 1, 1, 1, 1, 4)
    # The real table is WITHOUT ROWID, so the primary key already starts with
    # run_id. The composite index is measured on a heap copy, which is what the
    # before/after benchmark does too.
    conn.execute("CREATE TABLE lock_probe AS SELECT * FROM seat_segment_lock")
    before = " ".join(
        str(cell)
        for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT booking_id FROM lock_probe WHERE run_id=1 AND segment_idx=2"
        )
        for cell in row
    )
    conn.execute("CREATE INDEX idx_probe ON lock_probe(run_id, segment_idx)")
    after = " ".join(
        str(cell)
        for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT booking_id FROM lock_probe WHERE run_id=1 AND segment_idx=2"
        )
        for cell in row
    )
    assert "idx_probe" not in before
    assert "idx_probe" in after
    conn.close()
