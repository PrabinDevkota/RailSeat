"""Verified ratings. The trigger rejects anyone without a completed booking."""

from __future__ import annotations

from app.db import write_tx

ALLOWED_TAGS = ("good_view", "broken_window", "no_socket", "near_toilet", "noisy", "too_sunny")


class RatingError(Exception):
    pass


def add_rating(conn, user_id: int, booking_id: int, stars: int, tags: list[str]) -> int:
    if stars < 1 or stars > 5:
        raise RatingError("Stars must be from 1 to 5")
    unknown = [t for t in tags if t not in ALLOWED_TAGS]
    if unknown:
        raise RatingError(f"Unknown tag: {unknown[0]}")

    def tx(c):
        booking = c.execute("SELECT * FROM booking WHERE booking_id=?", (booking_id,)).fetchone()
        if booking is None:
            raise RatingError("Unknown booking")
        cur = c.execute(
            """
            INSERT INTO seat_rating(booking_id, user_id, run_id, seat_id, stars)
            VALUES (?,?,?,?,?)
            """,
            (booking_id, user_id, booking["run_id"], booking["seat_id"], stars),
        )
        rating_id = cur.lastrowid
        c.executemany(
            "INSERT INTO seat_rating_tag(rating_id, tag) VALUES (?,?)",
            [(rating_id, tag) for tag in dict.fromkeys(tags)],
        )
        return rating_id

    return write_tx(conn, tx)


def rebuild_summaries(conn) -> None:
    """Used by the bulk seed, which loads with triggers paused."""
    conn.execute("DELETE FROM seat_rating_summary")
    conn.execute("DELETE FROM maintenance_flag")
    row = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(stars), 0) AS s FROM seat_rating").fetchone()
    conn.execute("UPDATE rating_global SET n=?, sum_stars=? WHERE id=1", (row["n"], row["s"]))
    conn.execute(
        """
        INSERT INTO seat_rating_summary(
          train_id, coach_no, seat_no, from_idx, to_idx, n, sum_stars,
          good_view, broken_window, no_socket, near_toilet, noisy, too_sunny)
        SELECT tr.train_id, c.coach_no, s.seat_no, 0, 0, COUNT(*), SUM(r.stars), 0, 0, 0, 0, 0, 0
        FROM seat_rating r
        JOIN seat s ON s.seat_id = r.seat_id
        JOIN coach c ON c.coach_id = s.coach_id
        JOIN train_run tr ON tr.run_id = r.run_id
        GROUP BY tr.train_id, c.coach_no, s.seat_no
        """
    )
    conn.execute(
        """
        INSERT INTO seat_rating_summary(
          train_id, coach_no, seat_no, from_idx, to_idx, n, sum_stars,
          good_view, broken_window, no_socket, near_toilet, noisy, too_sunny)
        SELECT tr.train_id, c.coach_no, s.seat_no, b.from_idx, b.to_idx,
               COUNT(*), SUM(r.stars), 0, 0, 0, 0, 0, 0
        FROM seat_rating r
        JOIN booking b ON b.booking_id = r.booking_id
        JOIN seat s ON s.seat_id = r.seat_id
        JOIN coach c ON c.coach_id = s.coach_id
        JOIN train_run tr ON tr.run_id = r.run_id
        GROUP BY tr.train_id, c.coach_no, s.seat_no, b.from_idx, b.to_idx
        """
    )
    conn.execute("DROP TABLE IF EXISTS _tag_n")
    conn.execute(
        """
        CREATE TEMP TABLE _tag_n AS
        SELECT tr.train_id AS train_id, c.coach_no AS coach_no, s.seat_no AS seat_no,
               0 AS from_idx, 0 AS to_idx, t.tag AS tag, COUNT(*) AS n
        FROM seat_rating_tag t
        JOIN seat_rating r ON r.rating_id = t.rating_id
        JOIN seat s ON s.seat_id = r.seat_id
        JOIN coach c ON c.coach_id = s.coach_id
        JOIN train_run tr ON tr.run_id = r.run_id
        GROUP BY tr.train_id, c.coach_no, s.seat_no, t.tag
        UNION ALL
        SELECT tr.train_id, c.coach_no, s.seat_no, b.from_idx, b.to_idx, t.tag, COUNT(*)
        FROM seat_rating_tag t
        JOIN seat_rating r ON r.rating_id = t.rating_id
        JOIN booking b ON b.booking_id = r.booking_id
        JOIN seat s ON s.seat_id = r.seat_id
        JOIN coach c ON c.coach_id = s.coach_id
        JOIN train_run tr ON tr.run_id = r.run_id
        GROUP BY tr.train_id, c.coach_no, s.seat_no, b.from_idx, b.to_idx, t.tag
        """
    )
    conn.execute(
        "CREATE INDEX idx_tag_n ON _tag_n(train_id, coach_no, seat_no, from_idx, to_idx, tag)"
    )
    for tag, column in (
        ("good_view", "good_view"),
        ("broken_window", "broken_window"),
        ("no_socket", "no_socket"),
        ("near_toilet", "near_toilet"),
        ("noisy", "noisy"),
        ("too_sunny", "too_sunny"),
    ):
        conn.execute(
            f"""
            UPDATE seat_rating_summary
            SET {column} = COALESCE((
              SELECT n FROM _tag_n
              WHERE train_id = seat_rating_summary.train_id
                AND coach_no = seat_rating_summary.coach_no
                AND seat_no = seat_rating_summary.seat_no
                AND from_idx = seat_rating_summary.from_idx
                AND to_idx = seat_rating_summary.to_idx
                AND tag = ?
            ), 0)
            """,
            (tag,),
        )
    conn.execute("DROP TABLE IF EXISTS _tag_n")
    conn.execute(
        """
        INSERT INTO maintenance_flag(train_id, coach_no, seat_no, reason, active)
        SELECT train_id, coach_no, seat_no, 'repeated defect tags', 1
        FROM seat_rating_summary
        WHERE from_idx = 0 AND to_idx = 0 AND broken_window + no_socket >= 3
        """
    )
