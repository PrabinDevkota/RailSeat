"""Build a repeatable database.

demo  — four coaches, a handful of runs, enough to click through the product.
full  — 50 runs, 20 coaches, 72 seats and on the order of 100,000 bookings.

Bulk rows are loaded with triggers paused, then a snapshot of every replicated
table is written to change_log so replicas can catch up. Live bookings after
this point go through the triggers.
"""

from __future__ import annotations

import argparse
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from app.auth import hash_password
from app.db import connect, init_db, set_applying
from app.layout import COACH_PLAN, demo_coaches, seat_plan
from app.replication.triggers import snapshot_statements
from app.seed_data import FAMILY, GIVEN, STATIONS, TRAINS
from app.services.booking import compute_fare
from app.services.ratings import rebuild_summaries
from app.services.scoring import travel_side

PASSWORD = hash_password("railseat")


def _day_offset(origin_dep: str | None, minute: int | None) -> int:
    """Calendar day of a stop, counted from the origin departure clock."""
    if not origin_dep or minute is None:
        return 0
    hours, mins = origin_dep.split(":")
    return (int(hours) * 60 + int(mins) + int(minute)) // (24 * 60)


def _runs(scale: str, today: date) -> list[dict]:
    extra = [train["train_id"] for train in TRAINS if train["train_id"] > 3]
    if scale == "demo":
        offsets = [-8, -3, 1, 2]
        pairs = [(train_id, offset) for train_id in (1, 2, 3) for offset in offsets]
        pairs.append((2, 5))
        for offset in (3, 4, 6, 7, 8, 9, 10, 11):
            for train_id in (1, 2, 3):
                pairs.append((train_id, offset))
        for train_id in extra:
            for offset in range(1, 11):
                pairs.append((train_id, offset))
    else:
        pairs = []
        for train_id, count in ((1, 17), (2, 17), (3, 16)):
            pairs.extend((train_id, offset) for offset in range(-12, -12 + count))
        for train_id in extra:
            pairs.extend((train_id, offset) for offset in range(1, 9))
    runs = []
    for train_id, offset in pairs:
        kind = "past" if offset < 0 else "future"
        if train_id == 1 and offset == 1:
            kind = "hero"
        elif train_id == 1 and offset == 2:
            kind = "reversed"
        elif train_id == 2 and offset == 5 and scale == "demo":
            kind = "cancelled"
        elif train_id == 2 and offset == 1 and scale == "full":
            kind = "cancelled"
        elif train_id > 3 and offset > 0:
            kind = "open"
        status = "CANCELLED" if kind == "cancelled" else ("COMPLETED" if offset < 0 else "SCHEDULED")
        runs.append({
            "train_id": train_id,
            "run_date": (today + timedelta(days=offset)).isoformat(),
            "kind": kind,
            "status": status,
            "orientation": -1 if kind == "reversed" else 1,
        })
    return runs


def _spans(rng: random.Random, n_stops: int, parts: int) -> list[tuple[int, int]]:
    if parts <= 1 or n_stops < 3:
        return [(1, n_stops)]
    interior = list(range(2, n_stops))
    parts = min(parts, len(interior) + 1)
    cuts = sorted(rng.sample(interior, parts - 1))
    points = [1, *cuts, n_stops]
    return list(zip(points, points[1:]))


def _eligible(user: dict, quota: str) -> bool:
    if quota == "LADIES" and user["gender"] != "F":
        return False
    if quota == "SENIOR" and user["age"] < 60:
        return False
    return True


def seed(path: str | Path, scale: str = "demo", force: bool = False) -> dict:
    path = Path(path)
    if path.exists() and not force:
        raise FileExistsError(path)
    if path.exists() and force:
        for suffix in ("", "-wal", "-shm"):
            victim = Path(str(path) + suffix) if suffix else path
            if victim.exists():
                victim.unlink()
    conn = init_db(path)
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-200000")
    set_applying(conn, True)
    rng = random.Random(7)
    today = date.today()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        counts = _load(conn, scale, rng, today, now)
        rebuild_summaries(conn)
        conn.execute("DELETE FROM change_log")
        conn.execute("DELETE FROM sqlite_sequence WHERE name='change_log'")
        for statement in snapshot_statements():
            conn.execute(statement)
        conn.execute(
            "INSERT INTO audit_log(entity, entity_id, action, detail) VALUES (?,?,?,?)",
            ("seed", scale, "LOADED", "bulk snapshot"),
        )
        # The audit row was inserted while triggers were paused, so append it to the log.
        conn.execute(
            """
            INSERT INTO change_log(tbl, op, pk, payload)
            SELECT 'audit_log', 'I', audit_id,
                   json_object(
                     'audit_id', audit_id, 'entity', entity, 'entity_id', entity_id,
                     'action', action, 'detail', detail, 'actor_id', actor_id, 'created_at', created_at
                   )
            FROM audit_log
            WHERE entity='seed'
            """
        )
    finally:
        set_applying(conn, False)
        conn.execute("PRAGMA synchronous=FULL")
    conn.execute("UPDATE replica_state SET role='primary', applied_lsn=(SELECT COALESCE(MAX(lsn),0) FROM change_log)")
    counts["lsn"] = conn.execute("SELECT MAX(lsn) FROM change_log").fetchone()[0]
    conn.close()
    return counts


def _load(conn, scale: str, rng: random.Random, today: date, now: str) -> dict:
    users = _users(scale, rng, now)
    conn.executemany(
        """
        INSERT INTO user(
          user_id, name, email, password_hash, role, age, gender,
          pref_window, pref_scenic, pref_shade, pref_lower, pref_toilet, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        users,
    )
    user_rows = [
        {"user_id": u[0], "gender": u[6], "age": u[5]}
        for u in users
    ]
    station_ids = {}
    station_rows = []
    for code, (name, lat, lon) in STATIONS.items():
        station_ids[code] = len(station_ids) + 1
        station_rows.append((station_ids[code], code, name, lat, lon))
    conn.executemany("INSERT INTO station(station_id, code, name, lat, lon) VALUES (?,?,?,?,?)", station_rows)

    conn.executemany(
        "INSERT INTO train(train_id, train_no, name, route_name) VALUES (?,?,?,?)",
        [(t["train_id"], t["train_no"], t["name"], t["route_name"]) for t in TRAINS],
    )
    stop_rows = []
    distances: dict[int, dict[int, float]] = {}
    n_stops: dict[int, int] = {}
    scenic: dict[int, str] = {}
    for train in TRAINS:
        distances[train["train_id"]] = {}
        side_score = {"L": 0, "R": 0}
        origin_dep = train["stops"][0][2]
        for idx, stop in enumerate(train["stops"], start=1):
            code, arr, dep, dist, arr_min, dep_min, bearing = stop
            day = _day_offset(origin_dep, arr_min if arr_min is not None else dep_min)
            stop_rows.append((
                train["train_id"], idx, station_ids[code], arr, dep, day, dist, arr_min, dep_min, bearing,
            ))
            distances[train["train_id"]][idx] = dist
        n_stops[train["train_id"]] = len(train["stops"])
        for mark in train["landmarks"]:
            side_score[mark[1]] += mark[2]
        scenic[train["train_id"]] = "R" if side_score["R"] >= side_score["L"] else "L"
    conn.executemany(
        """
        INSERT INTO route_stop(
          train_id, stop_idx, station_id, arr, dep, day_offset, distance_km, arr_min, dep_min, bearing_out
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        stop_rows,
    )
    mark_rows = []
    for train in TRAINS:
        for seg, side, score, kind, description, offset in train["landmarks"]:
            mark_rows.append((train["train_id"], seg, side, score, kind, description, offset))
    conn.executemany(
        """
        INSERT INTO landmark(train_id, segment_idx, side, score, kind, description, time_offset_min)
        VALUES (?,?,?,?,?,?,?)
        """,
        mark_rows,
    )

    plan = demo_coaches() if scale == "demo" else list(COACH_PLAN)
    run_defs = _runs(scale, today)
    run_rows = []
    run_meta = []
    for i, run in enumerate(run_defs, start=1):
        train = next(t for t in TRAINS if t["train_id"] == run["train_id"])
        first_dep = train["stops"][0][2]
        run_rows.append((
            i, run["train_id"], run["run_date"], run["status"], run["orientation"],
            f"{run['run_date']} {first_dep}:00",
        ))
        run_meta.append({**run, "run_id": i})
    conn.executemany(
        """
        INSERT INTO train_run(run_id, train_id, run_date, status, coach_orientation, departure_at)
        VALUES (?,?,?,?,?,?)
        """,
        run_rows,
    )

    seats = seat_plan(72)
    coach_rows = []
    seat_rows = []
    coach_id = 0
    seat_id = 0
    seat_index = {}
    for run in run_meta:
        for position, (coach_no, coach_class) in enumerate(plan, start=1):
            coach_id += 1
            coach_rows.append((coach_id, run["run_id"], coach_no, coach_class, position))
            for seat in seats:
                seat_id += 1
                broken = 1 if seat["seat_no"] == 70 else 0
                seat_rows.append((
                    seat_id, coach_id, seat["seat_no"], seat["berth_type"], seat["physical_side"],
                    seat["is_window"], seat["near_toilet"], seat["quota"], broken,
                ))
                seat_index[(run["run_id"], coach_no, seat["seat_no"])] = {
                    "seat_id": seat_id,
                    "coach_no": coach_no,
                    "coach_class": coach_class,
                    "scenic": scenic[run["train_id"]],
                    **seat,
                    "broken": broken,
                    "run": run,
                }
    conn.executemany(
        "INSERT INTO coach(coach_id, run_id, coach_no, coach_class, position) VALUES (?,?,?,?,?)",
        coach_rows,
    )
    conn.executemany(
        """
        INSERT INTO seat(
          seat_id, coach_id, seat_no, berth_type, physical_side, is_window, near_toilet, quota, broken
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        seat_rows,
    )

    past_train1 = [r for r in run_meta if r["train_id"] == 1 and r["kind"] == "past"]
    asha_past = max(past_train1, key=lambda r: r["run_date"])
    cancelled = next(r for r in run_meta if r["kind"] == "cancelled")
    hero = next(r for r in run_meta if r["kind"] == "hero")

    bookings = []
    locks = []
    payments = []
    txns = []
    entries = []
    ratings = []
    tags = []
    booking_id = 0
    payment_id = 0
    entry_id = 0
    rating_id = 0

    def add_booking(seat, span, user_id, status, pay_state, created, explain, rated: bool):
        nonlocal booking_id, payment_id, entry_id, rating_id
        booking_id += 1
        start, end = span
        km = distances[seat["run"]["train_id"]][end] - distances[seat["run"]["train_id"]][start]
        fare = compute_fare(km, seat["coach_class"])
        bookings.append((
            booking_id, user_id, seat["run"]["run_id"], seat["seat_id"], start, end,
            status, None, fare, explain, created,
        ))
        for seg in range(start, end):
            locks.append((seat["run"]["run_id"], seat["seat_id"], seg, booking_id))
        if pay_state:
            payment_id += 1
            payments.append((
                payment_id, booking_id, user_id, fare, pay_state, f"seed-{payment_id}", created, created,
            ))
            _money(payment_id, user_id, fare, pay_state, created)
        if rated:
            rating_id += 1
            stars, tag_list = _stars(rng, seat)
            ratings.append((
                rating_id, booking_id, user_id, seat["run"]["run_id"], seat["seat_id"], stars, created,
            ))
            for tag in tag_list:
                tags.append((rating_id, tag))

    def _money(pid, user_id, fare, state, created):
        nonlocal entry_id
        txn = f"seed-{pid}-hold"
        txns.append((txn, pid, 1, created))
        entry_id += 1
        entries.append((entry_id, txn, "gateway", -fare, user_id, pid, "escrow hold", created))
        entry_id += 1
        entries.append((entry_id, txn, "escrow", fare, user_id, pid, "escrow hold", created))
        if state == "CAPTURED":
            txn2 = f"seed-{pid}-capture"
            txns.append((txn2, pid, 1, created))
            entry_id += 1
            entries.append((entry_id, txn2, "escrow", -fare, user_id, pid, "capture", created))
            entry_id += 1
            entries.append((entry_id, txn2, "railway_revenue", fare, user_id, pid, "capture", created))

    women = [u for u in user_rows if u["gender"] == "F"]
    seniors = [u for u in user_rows if u["age"] >= 60]
    general = user_rows

    def pick_user(quota: str) -> dict:
        pool = women if quota == "LADIES" else seniors if quota == "SENIOR" else general
        pool = [user for user in pool if user["user_id"] > 3]
        if not pool:
            pool = [user for user in general if user["user_id"] > 3]
        return rng.choice(pool)

    reserved = set()
    for seat in seat_index.values():
        run = seat["run"]
        if seat["broken"]:
            continue
        coach_no = seat["coach_no"]
        if run["run_id"] == asha_past["run_id"] and coach_no == "S1" and seat["seat_no"] == 7:
            add_booking(seat, (1, n_stops[run["train_id"]]), 1, "COMPLETED", "CAPTURED",
                        f"{run['run_date']} 12:00:00", "Earlier trip. You can rate this berth.", False)
            reserved.add(seat["seat_id"])
            continue
        if run["run_id"] == cancelled["run_id"] and seat["coach_no"] == "S1" and seat["seat_no"] == 5:
            add_booking(seat, (1, n_stops[run["train_id"]]), 1, "CONFIRMED", "HELD",
                        now, "This run is cancelled. Raise a dispute for an automatic refund.", False)
            reserved.add(seat["seat_id"])
            continue
        if run["run_id"] == asha_past["run_id"] and coach_no == "S2" and seat["seat_no"] in (8, 16, 24):
            add_booking(seat, (1, n_stops[run["train_id"]]), 2, "COMPLETED", "CAPTURED",
                        f"{run['run_date']} 18:00:00", "Ravi's earlier trip.", True)
            continue
        if run["kind"] in ("cancelled", "open"):
            continue
        spans = _plan_spans(rng, run, n_stops[run["train_id"]], seat["seat_no"])
        for span in spans:
            user = pick_user(seat["quota"])
            if not _eligible(user, seat["quota"]):
                continue
            if run["status"] == "COMPLETED":
                created = f"{run['run_date']} 18:00:00"
                rate = rng.random() < 0.45 and user["user_id"] != 1
                add_booking(seat, span, user["user_id"], "COMPLETED", "CAPTURED", created, "Historical seed", rate)
            else:
                add_booking(seat, span, user["user_id"], "CONFIRMED", "HELD", now, "Seeded confirmed ticket", False)

    conn.executemany(
        """
        INSERT INTO booking(
          booking_id, user_id, run_id, seat_id, from_idx, to_idx, status, hold_expires_at, fare, explain, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        bookings,
    )
    conn.executemany(
        "INSERT INTO seat_segment_lock(run_id, seat_id, segment_idx, booking_id) VALUES (?,?,?,?)",
        locks,
    )
    conn.executemany(
        """
        INSERT INTO payment(payment_id, booking_id, user_id, amount, state, gateway_ref, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        payments,
    )
    conn.executemany(
        "INSERT INTO ledger_txn(txn_id, payment_id, closed, created_at) VALUES (?,?,?,?)",
        txns,
    )
    conn.executemany(
        """
        INSERT INTO ledger_entries(entry_id, txn_id, account, amount, user_id, payment_id, note, created_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        entries,
    )
    if ratings:
        conn.executemany(
            """
            INSERT INTO seat_rating(rating_id, booking_id, user_id, run_id, seat_id, stars, created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            ratings,
        )
    if tags:
        conn.executemany("INSERT INTO seat_rating_tag(rating_id, tag) VALUES (?,?)", tags)

    prefs = '{"window": true, "scenic": true, "shade": true}'
    conn.execute(
        """
        INSERT INTO waitlist(user_id, run_id, from_idx, to_idx, coach_class, preferences, status, created_at)
        VALUES (?,?,?,?,?,?, 'WAITING', ?)
        """,
        (4, hero["run_id"], 1, n_stops[hero["train_id"]], "SL", prefs, now),
    )
    return {
        "users": len(users),
        "runs": len(run_meta),
        "coaches": coach_id,
        "seats": seat_id,
        "bookings": len(bookings),
        "locks": len(locks),
        "ratings": len(ratings),
        "hero_run_id": hero["run_id"],
        "cancelled_run_id": cancelled["run_id"],
        "asha_past_run_id": asha_past["run_id"],
    }


def _plan_spans(rng: random.Random, run: dict, n_stops_n: int, seat_no: int) -> list[tuple[int, int]]:
    if run["kind"] == "hero":
        if seat_no <= 24 or rng.random() > 0.12:
            return []
        return [(1, n_stops_n)]
    if run["status"] == "COMPLETED":
        parts = 3 if n_stops_n >= 8 and rng.random() < 0.4 else 2
        return _spans(rng, n_stops_n, parts)
    if rng.random() > 0.22:
        return []
    if rng.random() < 0.7:
        return [(1, n_stops_n)]
    return _spans(rng, n_stops_n, 2)


def _stars(rng: random.Random, seat: dict) -> tuple[int, list[str]]:
    side = travel_side(seat["physical_side"], seat["run"]["orientation"])
    base = 3
    if seat["is_window"] and side == seat["scenic"]:
        base = 5
    elif seat["is_window"]:
        base = 4
    stars = min(5, max(1, base + rng.choice([-1, 0, 0, 0, 1])))
    picked = []
    if seat["is_window"] and side == seat["scenic"] and rng.random() < 0.55:
        picked.append("good_view")
    if seat["near_toilet"] and rng.random() < 0.35:
        picked.append("near_toilet")
    if rng.random() < 0.03:
        picked.append("broken_window")
    if rng.random() < 0.025:
        picked.append("no_socket")
    if rng.random() < 0.04:
        picked.append("noisy")
    return stars, picked


def _users(scale: str, rng: random.Random, now: str) -> list[tuple]:
    rows = [(
        1, "Asha Kulkarni", "asha@railseat.dev", PASSWORD, "PASSENGER", 28, "F",
        1, 1, 1, 0, "FAR", now,
    ), (
        2, "Ravi Menon", "ravi@railseat.dev", PASSWORD, "PASSENGER", 35, "M",
        1, 0, 0, 0, None, now,
    ), (
        3, "Meera Iyer", "meera@railseat.dev", PASSWORD, "ADMIN", 41, "F",
        0, 0, 0, 0, None, now,
    )]
    target = 80 if scale == "demo" else 1000
    for user_id in range(4, target + 1):
        given = GIVEN[(user_id - 4) % len(GIVEN)]
        family = FAMILY[(user_id // 3) % len(FAMILY)]
        gender = ("F", "M", "O")[user_id % 3]
        age = 62 + (user_id % 8) if user_id % 7 == 0 else 20 + (user_id % 35)
        rows.append((
            user_id, f"{given} {family}", f"user{user_id}@railseat.dev", PASSWORD, "PASSENGER",
            age, gender, user_id % 2, (user_id // 2) % 2, (user_id // 3) % 2, 1 if age >= 60 else user_id % 2,
            "FAR" if user_id % 2 == 0 else "NEAR", now,
        ))
    return rows


def _attach_rake(conn, run_id: int) -> None:
    seats = seat_plan(72)
    for position, (coach_no, coach_class) in enumerate(demo_coaches(), start=1):
        coach = conn.execute(
            "INSERT INTO coach(run_id, coach_no, coach_class, position) VALUES (?,?,?,?)",
            (run_id, coach_no, coach_class, position),
        )
        coach_id = coach.lastrowid
        conn.executemany(
            """
            INSERT INTO seat(
              coach_id, seat_no, berth_type, physical_side, is_window,
              near_toilet, quota, broken
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            [
                (
                    coach_id, seat["seat_no"], seat["berth_type"], seat["physical_side"],
                    seat["is_window"], seat["near_toilet"], seat["quota"],
                    1 if seat["seat_no"] == 70 else 0,
                )
                for seat in seats
            ],
        )


def ensure_services(path: str | Path, offsets: tuple[int, ...] = tuple(range(1, 11))) -> dict:
    """Insert any station or train the catalogue has, plus upcoming runs.

    Matches trains by train number, so a database that already has the
    original three services is extended rather than copied. Existing bookings
    stay. Triggers stay on, so replicas pick the new rows up from the log.
    """
    conn = connect(path)
    conn.execute("PRAGMA busy_timeout=120000")
    today = date.today()
    added = {"stations": 0, "trains": 0, "runs": 0}
    try:
        conn.execute("BEGIN IMMEDIATE")
        for code, (name, lat, lon) in STATIONS.items():
            if conn.execute("SELECT 1 FROM station WHERE code=?", (code,)).fetchone():
                continue
            conn.execute(
                "INSERT INTO station(code, name, lat, lon) VALUES (?,?,?,?)",
                (code, name, lat, lon),
            )
            added["stations"] += 1
        conn.execute("COMMIT")

        for train in TRAINS:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT train_id FROM train WHERE train_no=?",
                (train["train_no"],),
            ).fetchone()
            if row is None:
                cur = conn.execute(
                    "INSERT INTO train(train_no, name, route_name) VALUES (?,?,?)",
                    (train["train_no"], train["name"], train["route_name"]),
                )
                train_id = cur.lastrowid
                origin_dep = train["stops"][0][2]
                for idx, stop in enumerate(train["stops"], start=1):
                    code, arr, dep, dist, arr_min, dep_min, bearing = stop
                    station_id = conn.execute(
                        "SELECT station_id FROM station WHERE code=?",
                        (code,),
                    ).fetchone()["station_id"]
                    day = _day_offset(origin_dep, arr_min if arr_min is not None else dep_min)
                    conn.execute(
                        """
                        INSERT INTO route_stop(
                          train_id, stop_idx, station_id, arr, dep, day_offset,
                          distance_km, arr_min, dep_min, bearing_out
                        ) VALUES (?,?,?,?,?,?,?,?,?,?)
                        """,
                        (train_id, idx, station_id, arr, dep, day, dist, arr_min, dep_min, bearing),
                    )
                for seg, side, score, kind, description, offset in train["landmarks"]:
                    conn.execute(
                        """
                        INSERT INTO landmark(
                          train_id, segment_idx, side, score, kind, description, time_offset_min
                        ) VALUES (?,?,?,?,?,?,?)
                        """,
                        (train_id, seg, side, score, kind, description, offset),
                    )
                added["trains"] += 1
            else:
                train_id = row["train_id"]
            dep = conn.execute(
                "SELECT dep FROM route_stop WHERE train_id=? AND stop_idx=1",
                (train_id,),
            ).fetchone()
            if dep is None or not dep["dep"]:
                conn.execute("COMMIT")
                continue
            for offset in offsets:
                if offset <= 0:
                    continue
                run_date = (today + timedelta(days=offset)).isoformat()
                if conn.execute(
                    "SELECT 1 FROM train_run WHERE train_id=? AND run_date=?",
                    (train_id, run_date),
                ).fetchone():
                    continue
                cur = conn.execute(
                    """
                    INSERT INTO train_run(train_id, run_date, status, coach_orientation, departure_at)
                    VALUES (?, ?, 'SCHEDULED', 1, ?)
                    """,
                    (train_id, run_date, f"{run_date} {dep['dep']}:00"),
                )
                _attach_rake(conn, cur.lastrowid)
                added["runs"] += 1
            conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()
    return added


def extend_horizon(path: str | Path, offsets: tuple[int, ...] = (3, 4, 6, 7, 8, 9, 10, 11)) -> int:
    """Add scheduled runs on dates the demo seed did not have. Existing rows stay."""
    conn = connect(path)
    today = date.today()
    added = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        for train_id in (1, 2, 3):
            dep = conn.execute(
                "SELECT dep FROM route_stop WHERE train_id=? AND stop_idx=1",
                (train_id,),
            ).fetchone()
            if dep is None or not dep["dep"]:
                continue
            for offset in offsets:
                run_date = (today + timedelta(days=offset)).isoformat()
                if conn.execute(
                    "SELECT 1 FROM train_run WHERE train_id=? AND run_date=?",
                    (train_id, run_date),
                ).fetchone():
                    continue
                cur = conn.execute(
                    """
                    INSERT INTO train_run(train_id, run_date, status, coach_orientation, departure_at)
                    VALUES (?, ?, 'SCHEDULED', 1, ?)
                    """,
                    (train_id, run_date, f"{run_date} {dep['dep']}:00"),
                )
                _attach_rake(conn, cur.lastrowid)
                added += 1
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()
    return added


def main():
    parser = argparse.ArgumentParser(description="Seed a RailSeat database")
    parser.add_argument("--scale", choices=("demo", "full"), default="demo")
    parser.add_argument("--db", default="data/primary.db")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--extend", action="store_true", help="Add upcoming runs for the original three trains")
    parser.add_argument(
        "--services",
        action="store_true",
        help="Add every catalogue station, train and upcoming run without wiping bookings",
    )
    args = parser.parse_args()
    if args.services:
        added = ensure_services(args.db)
        print(f"Services at {args.db}: {added['stations']} stations, {added['trains']} trains, {added['runs']} runs")
        return
    if args.extend:
        added = extend_horizon(args.db)
        print(f"Added {added} scheduled runs at {args.db}")
        return
    counts = seed(args.db, args.scale, force=args.force or True)
    print(f"Seeded {args.scale} at {args.db}")
    for key, value in counts.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
