"""A small railway used by the correctness tests."""

from __future__ import annotations

from datetime import date, timedelta

from app.auth import hash_password
from app.db import init_db
from app.layout import seat_plan

PASSWORD = hash_password("railseat")


def make_world(tmp_path, seats: int = 24, orientation: int = 1):
    conn = init_db(tmp_path / "rail.db")
    now = "2026-11-01 09:00:00"
    conn.execute(
        """
        INSERT INTO user(user_id, name, email, password_hash, role, age, gender, pref_window, pref_scenic, pref_shade, pref_lower)
        VALUES
          (1, 'Asha', 'asha@railseat.dev', ?, 'PASSENGER', 28, 'F', 1, 1, 1, 0),
          (2, 'Senior', 'senior@railseat.dev', ?, 'PASSENGER', 67, 'M', 0, 0, 0, 1),
          (3, 'Meera', 'meera@railseat.dev', ?, 'ADMIN', 41, 'F', 0, 0, 0, 0),
          (4, 'Arun', 'arun@railseat.dev', ?, 'PASSENGER', 33, 'M', 1, 0, 0, 0)
        """,
        (PASSWORD, PASSWORD, PASSWORD, PASSWORD),
    )
    conn.execute("INSERT INTO station(station_id, code, name) VALUES (1,'A','Alpha'),(2,'B','Beta'),(3,'C','Gamma'),(4,'D','Delta')")
    conn.execute("INSERT INTO train(train_id, train_no, name, route_name) VALUES (1,'10001','Test Express','Ghats')")
    for idx, dist, dep in ((1, 0, 0), (2, 40, 50), (3, 90, 110), (4, 130, 160)):
        conn.execute(
            """
            INSERT INTO route_stop(train_id, stop_idx, station_id, distance_km, arr_min, dep_min, bearing_out, dep, arr)
            VALUES (1,?,?,?,?,?,?,?,?)
            """,
            (idx, idx, dist, None if idx == 1 else dep - 5, dep if idx < 4 else None, 180 if idx < 4 else None,
             "08:00" if idx == 1 else None, None if idx == 1 else "09:00"),
        )
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    conn.execute(
        """
        INSERT INTO train_run(run_id, train_id, run_date, status, coach_orientation, departure_at)
        VALUES (1, 1, ?, 'SCHEDULED', ?, ?)
        """,
        (tomorrow, orientation, f"{tomorrow} 08:00:00"),
    )
    conn.execute("INSERT INTO coach(coach_id, run_id, coach_no, coach_class, position) VALUES (1,1,'S1','SL',1)")
    rows = []
    for seat in seat_plan(seats):
        rows.append((
            seat["seat_no"], 1, seat["seat_no"], seat["berth_type"], seat["physical_side"],
            seat["is_window"], seat["near_toilet"], seat["quota"], 0,
        ))
    conn.executemany(
        """
        INSERT INTO seat(seat_id, coach_id, seat_no, berth_type, physical_side, is_window, near_toilet, quota, broken)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    conn.execute(
        """
        INSERT INTO landmark(train_id, segment_idx, side, score, kind, description, time_offset_min)
        VALUES (1, 2, 'R', 10, 'ghat', 'Bhor Ghat on the right', 15),
               (1, 2, 'L', 2, 'plain', 'A quiet cutting', 12)
        """
    )
    return conn
