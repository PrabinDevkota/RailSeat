"""Physical coach layout.

A sleeper bay has six compartment berths against one wall and two side
berths against the other. That wall is the seat's physical side. Travel
side flips when the coach is marshalled the other way (see scoring.travel_side).

Seat count is 72 in every coach so the seeded volume matches the brief.
1A and 2A counts are therefore illustrative, not the railway's real diagram.
"""

from __future__ import annotations

# berth, physical side, window?
_BAY = (
    ("LB", "L", 1),
    ("MB", "L", 0),
    ("UB", "L", 1),
    ("LB", "L", 1),
    ("MB", "L", 0),
    ("UB", "L", 1),
    ("SL", "R", 1),
    ("SU", "R", 1),
)

COACH_PLAN = (
    [("S1", "SL"), ("S2", "SL"), ("S3", "SL"), ("S4", "SL"),
     ("S5", "SL"), ("S6", "SL"), ("S7", "SL"), ("S8", "SL"),
     ("S9", "SL"), ("S10", "SL"), ("S11", "SL"), ("S12", "SL"),
     ("B1", "3A"), ("B2", "3A"), ("B3", "3A"), ("B4", "3A"), ("B5", "3A"),
     ("A1", "2A"), ("A2", "2A"),
     ("H1", "1A")]
)


def seat_plan(count: int = 72) -> list[dict]:
    bays = count // 8
    seats = []
    for i in range(count):
        berth, side, window = _BAY[i % 8]
        bay = i // 8
        if bay == 1 and berth == "LB":
            quota = "LADIES"
        elif bay == 2 and berth == "LB":
            quota = "SENIOR"
        else:
            quota = "GENERAL"
        near = 1 if bay == 0 or bay == bays - 1 else 0
        seats.append({
            "seat_no": i + 1,
            "berth_type": berth,
            "physical_side": side,
            "is_window": window,
            "near_toilet": near,
            "quota": quota,
            "broken": 0,
        })
    return seats


def demo_coaches() -> list[tuple[str, str]]:
    """Four coaches is enough to show class, quota and a full bay map."""
    return [("S1", "SL"), ("S2", "SL"), ("B1", "3A"), ("A1", "2A")]
