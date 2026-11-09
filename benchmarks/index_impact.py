"""Query plan and latency of a segment lookup before and after an index.

The live lock table is WITHOUT ROWID, so its primary key already starts with
run_id. This benchmark copies locks onto a heap, which is the case the extra
index is for, and the case the examiner can see in EXPLAIN.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from app.db import connect, init_db

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmarks" / "results"
ROWS = 200_000
REPEATS = 40


def _plan(conn) -> str:
    rows = conn.execute(
        "EXPLAIN QUERY PLAN SELECT booking_id FROM lock_probe WHERE run_id=? AND segment_idx=?",
        (40, 2),
    ).fetchall()
    return " | ".join(" ".join(str(cell) for cell in row) for row in rows)


def _time(conn) -> float:
    started = time.perf_counter()
    for run_id in range(REPEATS):
        conn.execute(
            "SELECT booking_id FROM lock_probe WHERE run_id=? AND segment_idx=?",
            (run_id % 80, 2),
        ).fetchall()
    return (time.perf_counter() - started) / REPEATS * 1000


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "index.db"
    for suffix in ("", "-wal", "-shm"):
        extra = Path(str(path) + suffix)
        if extra.exists():
            extra.unlink()
    conn = init_db(path)
    conn.execute("CREATE TABLE lock_probe (run_id INTEGER, seat_id INTEGER, segment_idx INTEGER, booking_id INTEGER)")
    conn.execute("BEGIN")
    batch = (
        (run, seat, segment, run * 1000 + seat)
        for run in range(80)
        for seat in range(500)
        for segment in range(5)
    )
    conn.executemany("INSERT INTO lock_probe VALUES (?,?,?,?)", batch)
    conn.execute("COMMIT")
    before_plan = _plan(conn)
    before_ms = _time(conn)
    conn.execute("CREATE INDEX idx_probe ON lock_probe(run_id, segment_idx)")
    after_plan = _plan(conn)
    after_ms = _time(conn)
    conn.close()
    result = {
        "rows": ROWS,
        "before_ms": round(before_ms, 4),
        "after_ms": round(after_ms, 4),
        "before_plan": before_plan,
        "after_plan": after_plan,
    }
    (OUT / "index_impact.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    ax.bar(["Heap scan", "Index on (run, segment)"], [before_ms, after_ms], color=["#8f2d2d", "#0e6b57"])
    ax.set_ylabel("Mean lookup (ms)")
    ax.set_title(f"Segment lookup, {ROWS:,} lock rows")
    fig.tight_layout()
    fig.savefig(OUT / "index_impact.png", dpi=140)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
