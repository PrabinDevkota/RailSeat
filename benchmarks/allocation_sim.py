"""Preference-match rate for random, first-free, L1 and L2 on the same rake."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from app.db import connect
from app.services.allocation import NoSeatAvailable, allocate
from tests.world import make_world

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmarks" / "results"
STRATEGIES = ("random", "first", "L1", "L2")
PROFILES = [
    {"window": True},
    {"scenic": True},
    {"lower": True},
    {"window": True, "scenic": True},
]


def _matched(conn, seat_id: int, prefs: dict) -> tuple[bool, float]:
    row = conn.execute(
        "SELECT is_window, berth_type, physical_side FROM seat WHERE seat_id=?",
        (seat_id,),
    ).fetchone()
    on_scenic_side = row["physical_side"] == "R"
    checks = []
    if prefs.get("window"):
        checks.append(bool(row["is_window"]))
    if prefs.get("scenic"):
        checks.append(on_scenic_side)
    if prefs.get("lower"):
        checks.append(row["berth_type"] in ("LB", "SL"))
    view = 5.0 if on_scenic_side and row["is_window"] else (2.0 if row["is_window"] else 0.5)
    return all(checks) if checks else True, view


def _run(db_path: Path, strategy: str) -> dict:
    conn = connect(db_path)
    matched = 0
    booked = 0
    view_sum = 0.0
    for n in range(40):
        prefs = PROFILES[n % len(PROFILES)]
        try:
            result = allocate(conn, 4, 1, 1, 4, "SL", prefs, strategy)
        except NoSeatAvailable:
            break
        ok, view = _matched(conn, result["seat_id"], prefs)
        matched += int(ok)
        view_sum += view
        booked += 1
    conn.close()
    return {
        "strategy": strategy,
        "booked": booked,
        "match_rate": round(matched / booked, 3) if booked else 0,
        "mean_view": round(view_sum / booked, 3) if booked else 0,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    base = OUT / "alloc_base.db"
    if base.exists():
        base.unlink()
    conn = make_world(OUT, seats=72)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    (OUT / "rail.db").replace(base)
    rows = []
    for strategy in STRATEGIES:
        path = OUT / f"alloc_{strategy}.db"
        if path.exists():
            path.unlink()
        shutil.copy(base, path)
        rows.append(_run(path, strategy))
    (OUT / "allocation.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    ax.bar([r["strategy"] for r in rows], [r["match_rate"] for r in rows], color="#0e6b57")
    ax.set_ylim(0, 1)
    ax.set_ylabel("Passengers whose preferences were met")
    ax.set_title("Allocation on one 72-berth coach, 40 requests")
    fig.tight_layout()
    fig.savefig(OUT / "allocation.png", dpi=140)
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
