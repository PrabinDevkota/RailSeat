"""View score, sun side, Bayesian rating and allocation penalties."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from app.config import BAYES_MEAN_FALLBACK, BAYES_PRIOR


def travel_side(seat_physical_side: str, coach_orientation: int) -> str:
    """coach_orientation is +1 when the coach faces the direction of travel."""
    if coach_orientation == 1:
        return seat_physical_side
    return "R" if seat_physical_side == "L" else "L"


def view_score(raw_side_sum: float, is_window: int) -> float:
    factor = 1.0 if is_window else 0.3
    return raw_side_sum * factor


def scale_view(raw: float) -> float:
    """Map landmark points onto the same 0–5 range as star ratings. 25 points is a full score."""
    return round(min(5.0, raw / 5.0), 3)


def bayes_rating(sum_ratings: float, n: int, global_mean: float, prior_weight: int = BAYES_PRIOR) -> float:
    mean = global_mean if global_mean else BAYES_MEAN_FALLBACK
    return (prior_weight * mean + sum_ratings) / (prior_weight + n)


def blend_score(view_5: float, bayes: float, n: int, prior_weight: int = BAYES_PRIOR) -> float:
    """Shift weight onto crowd ratings as the number of verified ratings grows."""
    if n <= 0:
        return round(view_5, 3)
    w = n / (n + prior_weight)
    return round((1 - w) * view_5 + w * bayes, 3)


def sun_side(bearing_deg: float, hour: float) -> str | None:
    """Which side of the train the sun is on. None when it is overhead."""
    if 11 <= hour <= 14:
        return None
    right_bearing = (bearing_deg + 90) % 360
    diff = abs((right_bearing - 90 + 180) % 360 - 180)
    right_faces_east = diff < 90
    sun_from_east = hour < 12
    if sun_from_east:
        return "R" if right_faces_east else "L"
    return "L" if right_faces_east else "R"


def circular_mean(degrees: list[float]) -> float:
    if not degrees:
        return 0.0
    s = sum(math.sin(math.radians(d)) for d in degrees)
    c = sum(math.cos(math.radians(d)) for d in degrees)
    if abs(s) < 1e-9 and abs(c) < 1e-9:
        return degrees[0] % 360
    return math.degrees(math.atan2(s, c)) % 360


def parse_dt(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")


def clock_at(departure_at: str, offset_min: int) -> datetime:
    return parse_dt(departure_at) + timedelta(minutes=int(offset_min))


def fragmentation_penalty(occupied: set[int], from_idx: int, to_idx: int, last_segment: int) -> float:
    """Penalise a booking that leaves a one-segment scrap or punches a hole in a long free run."""
    free = {i for i in range(1, last_segment + 1) if i not in occupied}
    left = from_idx
    while left - 1 in free:
        left -= 1
    right = to_idx - 1
    while right + 1 in free:
        right += 1
    penalty = 0.0
    left_rem = from_idx - left
    right_rem = right - (to_idx - 1)
    if 0 < left_rem < 2:
        penalty += 1.0
    if 0 < right_rem < 2:
        penalty += 1.0
    block = right - left + 1
    if (to_idx - from_idx) == 1 and block >= 4:
        penalty += 1.5
    return penalty


def pref_match(seat: dict, prefs: dict) -> tuple[float, list[str]]:
    """Fraction of stated preferences this seat meets, plus short reasons."""
    if not prefs:
        return 0.0, []
    hits = 0
    total = 0
    reasons = []
    if prefs.get("window"):
        total += 1
        if seat.get("is_window"):
            hits += 1
            reasons.append("Window berth, as requested.")
    if prefs.get("lower"):
        total += 1
        if seat.get("berth_type") in ("LB", "SL"):
            hits += 1
            reasons.append("Lower berth, as requested.")
    if prefs.get("scenic"):
        total += 1
        if seat.get("scenic_match"):
            hits += 1
            reasons.append("Sits on the side with the stronger landmarks.")
    if prefs.get("shade"):
        total += 1
        sun = seat.get("sun_side")
        if sun is None or seat.get("travel_side") != sun:
            hits += 1
            reasons.append("Shaded for the sunny part of this journey.")
    toilet = prefs.get("toilet")
    if toilet in ("NEAR", "FAR"):
        total += 1
        near = bool(seat.get("near_toilet"))
        if (toilet == "NEAR" and near) or (toilet == "FAR" and not near):
            hits += 1
            reasons.append("Toilet distance matches the request.")
    if total == 0:
        return 0.0, reasons
    return hits / total, reasons


def passes_hard_rules(seat: dict, user: dict) -> bool:
    if seat.get("taken") or seat.get("broken") or seat.get("maintenance"):
        return False
    if seat.get("quota") == "LADIES" and user.get("gender") != "F":
        return False
    if seat.get("quota") == "SENIOR" and int(user.get("age") or 0) < 60:
        return False
    return True


def global_mean(conn) -> float:
    row = conn.execute("SELECT n, sum_stars FROM rating_global WHERE id=1").fetchone()
    if not row or row["n"] == 0:
        return BAYES_MEAN_FALLBACK
    return row["sum_stars"] / row["n"]
