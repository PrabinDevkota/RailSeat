"""L1 is a manual hold. This module is L2, L3, first-click and the random baseline."""

from __future__ import annotations

import random

from app.config import ALLOC_WEIGHTS
from app.services.booking import SeatTaken, book_seat_tx, compute_fare, hold_seat
from app.services.readmodel import seatmap
from app.services.scoring import fragmentation_penalty, passes_hard_rules, pref_match

class NoSeatAvailable(Exception):
    pass


def _last_segment(conn, run_id: int) -> int:
    row = conn.execute(
        """
        SELECT MAX(stop_idx) AS n FROM route_stop
        WHERE train_id = (SELECT train_id FROM train_run WHERE run_id=?)
        """,
        (run_id,),
    ).fetchone()
    return int(row["n"]) - 1


def _occupied(conn, run_id: int) -> dict[int, set[int]]:
    rows = conn.execute(
        "SELECT seat_id, segment_idx FROM seat_segment_lock WHERE run_id=?",
        (run_id,),
    ).fetchall()
    out: dict[int, set[int]] = {}
    for row in rows:
        out.setdefault(row["seat_id"], set()).add(row["segment_idx"])
    return out


def rank_seats(conn, user: dict, run_id: int, from_idx: int, to_idx: int,
               coach_class: str | None, prefs: dict, weights: dict | None = None) -> list[dict]:
    weights = weights or ALLOC_WEIGHTS
    data = seatmap(conn, run_id, from_idx, to_idx)
    occupied = _occupied(conn, run_id)
    last = _last_segment(conn, run_id)
    ranked = []
    for seat in data["seats"]:
        if coach_class and seat["coach_class"] != coach_class:
            continue
        if not passes_hard_rules(seat, user):
            continue
        match, reasons = pref_match(seat, prefs)
        defect = (seat["tag_counts"].get("broken_window", 0) + seat["tag_counts"].get("no_socket", 0)) * 0.35
        frag = fragmentation_penalty(occupied.get(seat["seat_id"], set()), from_idx, to_idx, last)
        bayes = seat["bayes"] if seat["bayes"] is not None else 3.5
        total = (
            weights["w1"] * seat["view_5"]
            + weights["w2"] * bayes
            + weights["w3"] * match
            - weights["w4"] * defect
            - weights["w5"] * frag
        )
        bits = list(reasons)
        if seat["view_5"] >= 3:
            bits.append(f"View score {seat['view_score']:.0f} on the {seat['travel_side'].lower()} side.")
        if user.get("age", 0) >= 60 and seat["berth_type"] in ("LB", "SL"):
            bits.append("Lower berth ranked up because the passenger is 60 or older.")
        if seat["quota"] == "LADIES":
            bits.append("Ladies quota berth.")
        if not bits:
            bits.append("Best remaining berth by view and traveller rating.")
        seat = dict(seat)
        seat["score"] = round(total, 4)
        seat["pref_match"] = round(match, 3)
        seat["defect_penalty"] = round(defect, 3)
        seat["fragmentation_penalty"] = round(frag, 3)
        seat["reasons"] = bits
        ranked.append(seat)
    ranked.sort(key=lambda s: (-s["score"], s["seat_no"], s["coach_no"]))
    return ranked


def _explain(seat: dict, strategy: str) -> str:
    if strategy == "random":
        return "Random allotment, the IRCTC-style baseline. No preference was considered."
    if strategy == "first":
        return "First free berth in seat-number order."
    return " ".join(seat["reasons"])


def allocate_tx(conn, user: dict, run_id: int, from_idx: int, to_idx: int,
                coach_class: str | None, prefs: dict, strategy: str = "L2",
                weights: dict | None = None, attempts: int = 10) -> dict | None:
    """Try seats inside the caller's transaction. Returns None when nothing is free."""
    ranked = rank_seats(conn, user, run_id, from_idx, to_idx, coach_class, prefs, weights)
    if strategy == "random":
        random.shuffle(ranked)
    elif strategy == "first":
        ranked.sort(key=lambda s: (s["coach_no"], s["seat_no"]))
    elif strategy == "L1":
        ranked.sort(key=lambda s: (-s["view_5"], -s["pref_match"], s["seat_no"]))
    chosen = ranked if strategy == "random" else ranked[:attempts]
    if strategy == "random":
        chosen = ranked[: max(attempts, 30)]
    for seat in chosen:
        fare = compute_fare(
            conn.execute(
                """
                SELECT rs2.distance_km - rs1.distance_km AS km
                FROM train_run tr
                JOIN route_stop rs1 ON rs1.train_id = tr.train_id AND rs1.stop_idx=?
                JOIN route_stop rs2 ON rs2.train_id = tr.train_id AND rs2.stop_idx=?
                WHERE tr.run_id=?
                """,
                (from_idx, to_idx, run_id),
            ).fetchone()["km"],
            seat["coach_class"],
        )
        explain = _explain(seat, strategy)
        try:
            booking_id = book_seat_tx(
                conn, user["user_id"], run_id, seat["seat_id"], from_idx, to_idx, fare, explain,
            )
        except SeatTaken:
            continue
        return {
            "booking_id": booking_id,
            "seat_id": seat["seat_id"],
            "seat_no": seat["seat_no"],
            "coach_no": seat["coach_no"],
            "score": seat["score"],
            "explain": explain,
            "strategy": strategy,
            "fare": fare,
        }
    return None


def allocate(conn, user_id: int, run_id: int, from_idx: int, to_idx: int,
             coach_class: str | None, prefs: dict | None, strategy: str = "L2") -> dict:
    """Public path. Each attempt is its own immediate transaction, as in the brief."""
    user = conn.execute("SELECT * FROM user WHERE user_id=?", (user_id,)).fetchone()
    if user is None:
        raise NoSeatAvailable()
    user = dict(user)
    prefs = prefs or {}
    if strategy == "L3":
        prefs = {}
    ranked = rank_seats(conn, user, run_id, from_idx, to_idx, coach_class, prefs)
    if strategy == "random":
        random.shuffle(ranked)
        pool = ranked[:30]
    elif strategy == "first":
        pool = sorted(ranked, key=lambda s: (s["coach_no"], s["seat_no"]))[:10]
    elif strategy == "L1":
        pool = sorted(ranked, key=lambda s: (-s["view_5"], -s["pref_match"], s["seat_no"]))[:10]
    else:
        pool = ranked[:10]
    for seat in pool:
        fare_row = conn.execute(
            """
            SELECT rs2.distance_km - rs1.distance_km AS km
            FROM train_run tr
            JOIN route_stop rs1 ON rs1.train_id = tr.train_id AND rs1.stop_idx=?
            JOIN route_stop rs2 ON rs2.train_id = tr.train_id AND rs2.stop_idx=?
            WHERE tr.run_id=?
            """,
            (from_idx, to_idx, run_id),
        ).fetchone()
        fare = compute_fare(fare_row["km"], seat["coach_class"])
        explain = _explain(seat, strategy)
        try:
            booking_id = hold_seat(
                conn, user_id, run_id, seat["seat_id"], from_idx, to_idx, explain=explain,
            )
        except SeatTaken:
            continue
        return {
            "booking_id": booking_id,
            "seat_id": seat["seat_id"],
            "seat_no": seat["seat_no"],
            "coach_no": seat["coach_no"],
            "coach_class": seat["coach_class"],
            "score": seat["score"],
            "explain": explain,
            "strategy": strategy,
            "fare": fare,
        }
    raise NoSeatAvailable()
