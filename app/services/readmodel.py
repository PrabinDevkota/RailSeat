"""Read queries for search, the seat map and the journey guide."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.services.booking import compute_fare
from app.services.ticket_pdf import ticket_is_downloadable
from app.services.scoring import (
    bayes_rating,
    blend_score,
    circular_mean,
    clock_at,
    global_mean,
    scale_view,
    sun_side,
    travel_side,
    view_score,
)

TAG_COLUMNS = (
    "good_view", "broken_window", "no_socket", "near_toilet", "noisy", "too_sunny",
)


def _dict(row) -> dict | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def stations(conn) -> list[dict]:
    rows = conn.execute("SELECT code, name FROM station ORDER BY name").fetchall()
    return [_dict(r) for r in rows]


def resolve_station(conn, text: str) -> str | None:
    """Match a code, a full name, or a unique prefix such as 'Pune' or 'Mumbai'."""
    raw = (text or "").strip()
    if not raw:
        return None
    exact = conn.execute(
        """
        SELECT code FROM station
        WHERE code = ? COLLATE NOCASE OR name = ? COLLATE NOCASE
        """,
        (raw, raw),
    ).fetchall()
    if len(exact) == 1:
        return exact[0]["code"]
    like = conn.execute(
        """
        SELECT code FROM station
        WHERE code LIKE ? COLLATE NOCASE OR name LIKE ? COLLATE NOCASE
        ORDER BY length(name)
        """,
        (raw + "%", "%" + raw + "%"),
    ).fetchall()
    if len(like) == 1:
        return like[0]["code"]
    return None


def _clock(minutes: int) -> str:
    hours, mins = divmod(max(0, int(minutes)), 60)
    return f"{hours}h {mins:02d}m"


def _search_query(conn, origin: str, dest: str, date: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT tr.run_id, tr.run_date, tr.status, tr.departure_at, tr.coach_orientation,
               t.train_id, t.train_no, t.name AS train_name, t.route_name,
               rs1.stop_idx AS from_idx, rs1.dep AS dep, rs1.dep_min AS dep_min,
               rs2.stop_idx AS to_idx, rs2.arr AS arr, rs2.arr_min AS arr_min,
               rs2.distance_km - rs1.distance_km AS distance_km,
               s1.code AS from_code, s1.name AS from_name,
               s2.code AS to_code, s2.name AS to_name
        FROM train_run tr
        JOIN train t ON t.train_id = tr.train_id
        JOIN route_stop rs1 ON rs1.train_id = t.train_id
        JOIN route_stop rs2 ON rs2.train_id = t.train_id AND rs2.stop_idx > rs1.stop_idx
        JOIN station s1 ON s1.station_id = rs1.station_id AND s1.code = ?
        JOIN station s2 ON s2.station_id = rs2.station_id AND s2.code = ?
        WHERE tr.run_date = ?
          AND tr.status IN ('SCHEDULED', 'RUNNING', 'CANCELLED')
        ORDER BY CASE tr.status WHEN 'CANCELLED' THEN 1 ELSE 0 END, rs1.dep_min
        """,
        (origin, dest, date),
    ).fetchall()
    return [_dict(r) for r in rows]


def _attach_free(conn, row: dict) -> dict:
    present = [
        r["coach_class"]
        for r in conn.execute(
            "SELECT DISTINCT coach_class FROM coach WHERE run_id=?",
            (row["run_id"],),
        )
    ]
    counts = conn.execute(
        """
        SELECT c.coach_class AS cls, COUNT(*) AS free_n
        FROM seat s
        JOIN coach c ON c.coach_id = s.coach_id
        WHERE c.run_id = ?
          AND s.broken = 0
          AND NOT EXISTS (
            SELECT 1 FROM seat_segment_lock k
            WHERE k.run_id = c.run_id AND k.seat_id = s.seat_id
              AND k.segment_idx >= ? AND k.segment_idx < ?
          )
          AND NOT EXISTS (
            SELECT 1 FROM maintenance_flag m
            JOIN train_run tr ON tr.train_id = m.train_id
            WHERE tr.run_id = c.run_id AND m.coach_no = c.coach_no
              AND m.seat_no = s.seat_no AND m.active = 1
          )
        GROUP BY c.coach_class
        """,
        (row["run_id"], row["from_idx"], row["to_idx"]),
    ).fetchall()
    free = {r["cls"]: int(r["free_n"]) for r in counts}
    order = [cls for cls in ("SL", "3A", "2A", "1A") if cls in present]
    row["free"] = {cls: free.get(cls, 0) for cls in order}
    row["classes"] = [
        {"code": cls, "free": free.get(cls, 0), "fare": compute_fare(row["distance_km"], cls)}
        for cls in order
    ]
    row["fare_from"] = min((item["fare"] for item in row["classes"]), default=0)
    dep = row["dep_min"] or 0
    arr = row["arr_min"] if row["arr_min"] is not None else dep
    row["duration_min"] = max(0, arr - dep)
    row["duration"] = _clock(row["duration_min"])
    row["bookable"] = row["status"] in ("SCHEDULED", "RUNNING")
    return row


def search_trains(conn, origin: str, dest: str, date: str) -> dict:
    origin_code = resolve_station(conn, origin)
    dest_code = resolve_station(conn, dest)
    unknown = [text for text, code in ((origin, origin_code), (dest, dest_code)) if text and not code]
    query = {"from": origin_code or origin, "to": dest_code or dest, "date": date}
    if unknown or origin_code is None or dest_code is None:
        return {"query": query, "suggested_date": None, "results": [], "unknown": unknown, "same": False}
    if origin_code == dest_code:
        return {"query": query, "suggested_date": None, "results": [], "unknown": [], "same": True}
    origin, dest = origin_code, dest_code
    rows = [_attach_free(conn, r) for r in _search_query(conn, origin, dest, date)]
    suggested = None
    if not rows:
        nxt = conn.execute(
            """
            SELECT DISTINCT tr.run_date
            FROM train_run tr
            JOIN route_stop rs1 ON rs1.train_id = tr.train_id
            JOIN route_stop rs2 ON rs2.train_id = tr.train_id AND rs2.stop_idx > rs1.stop_idx
            JOIN station s1 ON s1.station_id = rs1.station_id AND s1.code = ?
            JOIN station s2 ON s2.station_id = rs2.station_id AND s2.code = ?
            WHERE tr.run_date > ?
              AND tr.status IN ('SCHEDULED', 'RUNNING')
            ORDER BY tr.run_date
            LIMIT 1
            """,
            (origin, dest, date),
        ).fetchone()
        if nxt:
            suggested = nxt["run_date"]
            rows = [_attach_free(conn, r) for r in _search_query(conn, origin, dest, suggested)]
    return {
        "query": {"from": origin, "to": dest, "date": date},
        "suggested_date": suggested,
        "results": rows,
        "unknown": [],
        "same": False,
    }


def highlights(conn, limit: int = 3) -> list[dict]:
    today = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute(
        """
        SELECT tr.run_id, tr.run_date, tr.departure_at, t.train_no, t.name AS train_name, t.route_name,
               rs1.stop_idx AS from_idx, s1.code AS from_code, s1.name AS from_name, rs1.dep,
               rs2.stop_idx AS to_idx, s2.code AS to_code, s2.name AS to_name, rs2.arr
        FROM train_run tr
        JOIN train t ON t.train_id = tr.train_id
        JOIN route_stop rs1 ON rs1.train_id = t.train_id AND rs1.stop_idx = 1
        JOIN route_stop rs2 ON rs2.train_id = t.train_id
          AND rs2.stop_idx = (SELECT MAX(stop_idx) FROM route_stop WHERE train_id = t.train_id)
        JOIN station s1 ON s1.station_id = rs1.station_id
        JOIN station s2 ON s2.station_id = rs2.station_id
        WHERE tr.status = 'SCHEDULED' AND tr.run_date >= ?
        ORDER BY tr.run_date, tr.departure_at
        LIMIT ?
        """,
        (today, limit),
    ).fetchall()
    return [_dict(r) for r in rows]


def _side_sums(conn, train_id: int, from_idx: int, to_idx: int) -> dict[str, float]:
    rows = conn.execute(
        """
        SELECT side, COALESCE(SUM(score), 0) AS score
        FROM landmark
        WHERE train_id=? AND segment_idx>=? AND segment_idx<?
        GROUP BY side
        """,
        (train_id, from_idx, to_idx),
    ).fetchall()
    sums = {"L": 0.0, "R": 0.0}
    for row in rows:
        sums[row["side"]] = float(row["score"])
    return sums


def _summaries(conn, train_id: int, from_idx: int, to_idx: int) -> dict:
    rows = conn.execute(
        """
        SELECT * FROM v_seat_quality
        WHERE train_id=? AND ((from_idx=0 AND to_idx=0) OR (from_idx=? AND to_idx=?))
        """,
        (train_id, from_idx, to_idx),
    ).fetchall()
    overall, exact = {}, {}
    for row in rows:
        key = (row["coach_no"], row["seat_no"])
        item = _dict(row)
        if row["from_idx"] == 0 and row["to_idx"] == 0:
            overall[key] = item
        else:
            exact[key] = item
    return {"overall": overall, "exact": exact}


def _sun_for_journey(conn, train_id: int, from_idx: int, to_idx: int, departure_at: str) -> dict:
    rows = conn.execute(
        """
        SELECT stop_idx, bearing_out, dep_min FROM route_stop
        WHERE train_id=? AND stop_idx>=? AND stop_idx<? AND bearing_out IS NOT NULL
        """,
        (train_id, from_idx, to_idx),
    ).fetchall()
    bearings = [r["bearing_out"] for r in rows]
    bearing = circular_mean(bearings) if bearings else 180
    start = conn.execute(
        "SELECT dep_min FROM route_stop WHERE train_id=? AND stop_idx=?",
        (train_id, from_idx),
    ).fetchone()
    end = conn.execute(
        "SELECT arr_min FROM route_stop WHERE train_id=? AND stop_idx=?",
        (train_id, to_idx),
    ).fetchone()
    dep = datetime.strptime(departure_at, "%Y-%m-%d %H:%M:%S")
    start_min = start["dep_min"] or 0
    end_min = end["arr_min"] if end and end["arr_min"] is not None else start_min
    mid = dep + timedelta(minutes=(start_min + end_min) / 2)
    hour = mid.hour + mid.minute / 60
    side = sun_side(bearing, hour)
    if side is None:
        note = "Midday sun is overhead, so neither side is strongly shaded."
    else:
        label = "right" if side == "R" else "left"
        note = f"Around {mid.strftime('%H:%M')} the sun is on the {label} side of travel."
    return {"side": side, "hour": round(hour, 2), "bearing": round(bearing, 1), "note": note}


def seatmap(conn, run_id: int, from_idx: int | None = None, to_idx: int | None = None, coach_no: str | None = None) -> dict:
    run = conn.execute(
        """
        SELECT tr.*, t.train_no, t.name AS train_name, t.route_name
        FROM train_run tr JOIN train t ON t.train_id = tr.train_id
        WHERE tr.run_id=?
        """,
        (run_id,),
    ).fetchone()
    if run is None:
        raise KeyError("run")
    stops = conn.execute(
        """
        SELECT rs.stop_idx, rs.arr, rs.dep, rs.distance_km, s.code, s.name
        FROM route_stop rs JOIN station s ON s.station_id = rs.station_id
        WHERE rs.train_id=? ORDER BY rs.stop_idx
        """,
        (run["train_id"],),
    ).fetchall()
    by_idx = {s["stop_idx"]: s for s in stops}
    if not stops:
        raise KeyError("stops")
    if from_idx is None:
        from_idx = stops[0]["stop_idx"]
    if to_idx is None:
        to_idx = stops[-1]["stop_idx"]
    if from_idx not in by_idx or to_idx not in by_idx or from_idx >= to_idx:
        raise KeyError("stops")
    side_sums = _side_sums(conn, run["train_id"], from_idx, to_idx)
    scenic = max(side_sums, key=side_sums.get) if any(side_sums.values()) else None
    summaries = _summaries(conn, run["train_id"], from_idx, to_idx)
    mean = global_mean(conn)
    sun = _sun_for_journey(conn, run["train_id"], from_idx, to_idx, run["departure_at"])
    flags = {
        (r["coach_no"], r["seat_no"])
        for r in conn.execute(
            "SELECT coach_no, seat_no FROM maintenance_flag WHERE train_id=? AND active=1",
            (run["train_id"],),
        )
    }
    coaches = conn.execute(
        "SELECT coach_id, coach_no, coach_class, position FROM coach WHERE run_id=? ORDER BY position",
        (run_id,),
    ).fetchall()
    seats = conn.execute(
        """
        SELECT s.*, c.coach_no, c.coach_class, c.position,
               CASE WHEN EXISTS (
                 SELECT 1 FROM seat_segment_lock k
                 WHERE k.run_id=? AND k.seat_id=s.seat_id
                   AND k.segment_idx>=? AND k.segment_idx<?
               ) THEN 1 ELSE 0 END AS taken
        FROM seat s JOIN coach c ON c.coach_id = s.coach_id
        WHERE c.run_id=?
        ORDER BY c.position, s.seat_no
        """,
        (run_id, from_idx, to_idx, run_id),
    ).fetchall()
    coach_free: dict[str, int] = {c["coach_no"]: 0 for c in coaches}
    rendered = []
    distance = by_idx[to_idx]["distance_km"] - by_idx[from_idx]["distance_km"]
    for seat in seats:
        key = (seat["coach_no"], seat["seat_no"])
        side = travel_side(seat["physical_side"], run["coach_orientation"])
        raw = view_score(side_sums.get(side, 0.0), seat["is_window"])
        view_5 = scale_view(raw)
        summary = summaries["exact"].get(key) or summaries["overall"].get(key)
        n = int(summary["n"]) if summary else 0
        stars_sum = float(summary["sum_stars"]) if summary else 0.0
        bayes = bayes_rating(stars_sum, n, mean) if n else None
        display = blend_score(view_5, bayes or mean, n)
        tags = []
        if summary:
            tags = [name for name in TAG_COLUMNS if summary.get(name)]
        taken = bool(seat["taken"]) or bool(seat["broken"])
        if not taken and (seat["coach_no"], seat["seat_no"]) not in flags:
            coach_free[seat["coach_no"]] += 1
        rendered.append({
            "seat_id": seat["seat_id"],
            "seat_no": seat["seat_no"],
            "coach_no": seat["coach_no"],
            "coach_class": seat["coach_class"],
            "berth_type": seat["berth_type"],
            "physical_side": seat["physical_side"],
            "travel_side": side,
            "is_window": seat["is_window"],
            "near_toilet": seat["near_toilet"],
            "quota": seat["quota"],
            "broken": seat["broken"],
            "taken": bool(seat["taken"]),
            "maintenance": key in flags,
            "view_score": round(raw, 2),
            "view_5": view_5,
            "bayes": None if bayes is None else round(bayes, 3),
            "n": n,
            "display": display,
            "tags": tags,
            "tag_counts": {name: int(summary[name]) for name in TAG_COLUMNS} if summary else {},
            "scenic_match": scenic is not None and side == scenic and side_sums[scenic] > 0,
            "sun_side": sun["side"],
            "sun_hit": sun["side"] is not None and side == sun["side"],
        })
    coach_rows = []
    for coach in coaches:
        coach_rows.append({
            "coach_no": coach["coach_no"],
            "coach_class": coach["coach_class"],
            "free": coach_free[coach["coach_no"]],
            "fare": compute_fare(distance, coach["coach_class"]),
        })
    if coach_no:
        rendered = [s for s in rendered if s["coach_no"] == coach_no]
    return {
        "run": {
            "run_id": run["run_id"],
            "train_id": run["train_id"],
            "train_no": run["train_no"],
            "train_name": run["train_name"],
            "route_name": run["route_name"],
            "run_date": run["run_date"],
            "status": run["status"],
            "departure_at": run["departure_at"],
            "coach_orientation": run["coach_orientation"],
        },
        "from": _dict(by_idx[from_idx]),
        "to": _dict(by_idx[to_idx]),
        "distance_km": distance,
        "side_scores": side_sums,
        "scenic_side": scenic,
        "sun": sun,
        "coaches": coach_rows,
        "seats": rendered,
    }


def journey_guide(conn, run_id: int, from_idx: int, to_idx: int, seat_id: int) -> dict:
    seat_row = conn.execute(
        """
        SELECT s.*, c.coach_no, tr.train_id, tr.departure_at, tr.coach_orientation, t.name AS train_name
        FROM seat s
        JOIN coach c ON c.coach_id = s.coach_id
        JOIN train_run tr ON tr.run_id = c.run_id
        JOIN train t ON t.train_id = tr.train_id
        WHERE s.seat_id=? AND c.run_id=?
        """,
        (seat_id, run_id),
    ).fetchone()
    if seat_row is None:
        raise KeyError("seat")
    side = travel_side(seat_row["physical_side"], seat_row["coach_orientation"])
    marks = conn.execute(
        """
        SELECT l.*, rs.dep_min, st.name AS after_station, nxt.dep_min AS next_dep
        FROM landmark l
        JOIN route_stop rs ON rs.train_id = l.train_id AND rs.stop_idx = l.segment_idx
        JOIN station st ON st.station_id = rs.station_id
        LEFT JOIN route_stop nxt ON nxt.train_id = l.train_id AND nxt.stop_idx = l.segment_idx + 1
        WHERE l.train_id=? AND l.segment_idx>=? AND l.segment_idx<?
        ORDER BY l.segment_idx, l.time_offset_min
        """,
        (seat_row["train_id"], from_idx, to_idx),
    ).fetchall()
    items = []
    for mark in marks:
        start_min = (mark["dep_min"] or 0) + mark["time_offset_min"]
        when = clock_at(seat_row["departure_at"], start_min)
        end_min = mark["next_dep"] if mark["next_dep"] is not None else start_min + 20
        until = clock_at(seat_row["departure_at"], end_min)
        if until <= when:
            until = when + timedelta(minutes=20)
        items.append({
            "time": when.strftime("%H:%M"),
            "until": until.strftime("%H:%M"),
            "after_station": mark["after_station"],
            "side": mark["side"],
            "on_your_side": mark["side"] == side,
            "score": mark["score"],
            "kind": mark["kind"],
            "description": mark["description"],
        })
    sun = _sun_for_journey(conn, seat_row["train_id"], from_idx, to_idx, seat_row["departure_at"])
    return {
        "train_name": seat_row["train_name"],
        "coach_no": seat_row["coach_no"],
        "seat_no": seat_row["seat_no"],
        "travel_side": side,
        "orientation": seat_row["coach_orientation"],
        "sun": sun,
        "sun_on_this_seat": sun["side"] is not None and sun["side"] == side,
        "items": items,
    }


def _hhmm(value) -> str:
    if not value:
        return "—"
    text = str(value).strip()
    if len(text) >= 5 and text[1] == ":":
        text = "0" + text
    return text[:5]


def _stamp_journey(detail: dict) -> None:
    """Boarding and arrival clocks, including a date that rolls past midnight."""
    origin = detail.get("origin") or {}
    dest = detail.get("dest") or {}
    run_day = datetime.strptime(detail["run_date"], "%Y-%m-%d")
    board = run_day + timedelta(days=int(origin.get("day_offset") or 0))
    dep_min = origin.get("dep_min") or 0
    arr_min = dest.get("arr_min")
    if arr_min is None:
        arr_min = dep_min
    alight_days = int(dest.get("day_offset") or 0)
    if alight_days == 0 and arr_min < dep_min:
        alight_days = 1
    alight = run_day + timedelta(days=alight_days)
    minutes = arr_min - dep_min
    if minutes < 0:
        minutes += 24 * 60
    detail["board_time"] = _hhmm(origin.get("dep"))
    detail["alight_time"] = _hhmm(dest.get("arr"))
    detail["board_date"] = board.strftime("%a %d %b %Y")
    detail["alight_date"] = alight.strftime("%a %d %b %Y")
    detail["duration"] = f"{minutes // 60}h {minutes % 60:02d}m"
    detail["distance_km"] = round(float(dest.get("distance_km") or 0) - float(origin.get("distance_km") or 0), 1)


def booking_detail(conn, booking_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT b.*, u.name AS passenger, u.email,
               s.seat_no, s.berth_type, s.physical_side, s.is_window, s.quota,
               c.coach_no, c.coach_class,
               tr.run_date, tr.status AS run_status, tr.departure_at, tr.coach_orientation, tr.train_id,
               t.train_no, t.name AS train_name
        FROM booking b
        JOIN user u ON u.user_id = b.user_id
        JOIN seat s ON s.seat_id = b.seat_id
        JOIN coach c ON c.coach_id = s.coach_id
        JOIN train_run tr ON tr.run_id = b.run_id
        JOIN train t ON t.train_id = tr.train_id
        WHERE b.booking_id=?
        """,
        (booking_id,),
    ).fetchone()
    if row is None:
        return None
    detail = _dict(row)
    origin = conn.execute(
        """
        SELECT s.code, s.name, rs.dep, rs.dep_min, rs.day_offset, rs.distance_km
        FROM route_stop rs
        JOIN station s ON s.station_id = rs.station_id
        WHERE rs.train_id=? AND rs.stop_idx=?
        """,
        (detail["train_id"], detail["from_idx"]),
    ).fetchone()
    dest = conn.execute(
        """
        SELECT s.code, s.name, rs.arr, rs.arr_min, rs.day_offset, rs.distance_km
        FROM route_stop rs
        JOIN station s ON s.station_id = rs.station_id
        WHERE rs.train_id=? AND rs.stop_idx=?
        """,
        (detail["train_id"], detail["to_idx"]),
    ).fetchone()
    detail["origin"] = _dict(origin)
    detail["dest"] = _dict(dest)
    detail["travel_side"] = travel_side(detail["physical_side"], detail["coach_orientation"])
    _stamp_journey(detail)
    payment = conn.execute(
        "SELECT * FROM payment WHERE booking_id=? ORDER BY payment_id DESC LIMIT 1",
        (booking_id,),
    ).fetchone()
    detail["payment"] = _dict(payment)
    if payment:
        entries = conn.execute(
            "SELECT * FROM ledger_entries WHERE payment_id=? ORDER BY entry_id",
            (payment["payment_id"],),
        ).fetchall()
        detail["ledger"] = [_dict(e) for e in entries]
    else:
        detail["ledger"] = []
    dispute = conn.execute(
        "SELECT * FROM dispute WHERE booking_id=? ORDER BY dispute_id DESC LIMIT 1",
        (booking_id,),
    ).fetchone()
    detail["dispute"] = _dict(dispute)
    if dispute:
        decision = conn.execute(
            "SELECT * FROM dispute_decision WHERE dispute_id=?",
            (dispute["dispute_id"],),
        ).fetchone()
        detail["decision"] = _dict(decision)
    else:
        detail["decision"] = None
    rating = conn.execute(
        "SELECT * FROM seat_rating WHERE booking_id=?",
        (booking_id,),
    ).fetchone()
    detail["rating"] = _dict(rating)
    detail["downloadable"] = ticket_is_downloadable(detail)
    return detail


def user_trips(conn, user_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT b.booking_id, b.status, b.fare, b.from_idx, b.to_idx, b.hold_expires_at,
               s.seat_no, c.coach_no, c.coach_class,
               tr.run_date, tr.status AS run_status, t.train_no, t.name AS train_name,
               os.code AS from_code, ds.code AS to_code,
               (
                 SELECT p.state FROM payment p
                 WHERE p.booking_id = b.booking_id
                 ORDER BY p.payment_id DESC LIMIT 1
               ) AS payment_state
        FROM booking b
        JOIN seat s ON s.seat_id = b.seat_id
        JOIN coach c ON c.coach_id = s.coach_id
        JOIN train_run tr ON tr.run_id = b.run_id
        JOIN train t ON t.train_id = tr.train_id
        JOIN route_stop ors ON ors.train_id = tr.train_id AND ors.stop_idx = b.from_idx
        JOIN station os ON os.station_id = ors.station_id
        JOIN route_stop drs ON drs.train_id = tr.train_id AND drs.stop_idx = b.to_idx
        JOIN station ds ON ds.station_id = drs.station_id
        WHERE b.user_id=?
        ORDER BY b.booking_id DESC
        """,
        (user_id,),
    ).fetchall()
    trips = []
    for row in rows:
        trip = _dict(row)
        trip["downloadable"] = ticket_is_downloadable(trip)
        trips.append(trip)
    return trips


def open_swaps(conn, user_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT sw.swap_id, sw.status, sw.created_at,
               ba.booking_id AS offer_booking, ba.user_id AS offer_user,
               bb.booking_id AS target_booking, bb.user_id AS target_user,
               ua.name AS offer_name, ub.name AS target_name,
               ca.coach_no AS offer_coach, sa.seat_no AS offer_seat, sa.berth_type AS offer_berth,
               cb.coach_no AS target_coach, sb.seat_no AS target_seat, sb.berth_type AS target_berth
        FROM seat_swap sw
        JOIN booking ba ON ba.booking_id = sw.booking_a
        JOIN booking bb ON bb.booking_id = sw.booking_b
        JOIN user ua ON ua.user_id = ba.user_id
        JOIN user ub ON ub.user_id = bb.user_id
        JOIN seat sa ON sa.seat_id = ba.seat_id
        JOIN seat sb ON sb.seat_id = bb.seat_id
        JOIN coach ca ON ca.coach_id = sa.coach_id
        JOIN coach cb ON cb.coach_id = sb.coach_id
        WHERE sw.status='PROPOSED' AND (ba.user_id=? OR bb.user_id=?)
        ORDER BY sw.swap_id DESC
        """,
        (user_id, user_id),
    ).fetchall()
    out = []
    for row in rows:
        item = _dict(row)
        item["can_accept"] = row["target_user"] == user_id
        out.append(item)
    return out


def user_inbox(conn, user_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM inbox WHERE user_id=? ORDER BY message_id DESC LIMIT 50",
        (user_id,),
    ).fetchall()
    return [_dict(r) for r in rows]
