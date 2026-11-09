"""Draw the saved experiment numbers. Does not re-run the benchmarks."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parent / "results"


def main() -> None:
    allocation = json.loads((OUT / "allocation.json").read_text(encoding="utf-8"))
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    ax.bar([row["strategy"] for row in allocation], [row["match_rate"] for row in allocation], color="#0e6b57")
    ax.set_ylim(0, 1)
    ax.set_ylabel("Passengers whose preferences were met")
    ax.set_title("Allocation on one 72-berth coach, 40 requests")
    fig.tight_layout()
    fig.savefig(OUT / "allocation.png", dpi=140)
    plt.close(fig)

    index = json.loads((OUT / "index_impact.json").read_text(encoding="utf-8"))
    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    ax.bar(
        ["Heap scan", "Index on (run, segment)"],
        [index["before_ms"], index["after_ms"]],
        color=["#8f2d2d", "#0e6b57"],
    )
    ax.set_ylabel("Mean lookup (ms)")
    ax.set_title(f"Segment lookup, {index['rows']:,} lock rows")
    fig.tight_layout()
    fig.savefig(OUT / "index_impact.png", dpi=140)
    plt.close(fig)

    failover = json.loads((OUT / "failover.json").read_text(encoding="utf-8"))
    fig, ax = plt.subplots(figsize=(6.2, 3.4))
    ax.bar(
        ["Recovery (s)", "Lost LSNs"],
        [failover["recovery_seconds"], failover["lost_lsns"]],
        color=["#0e6b57", "#8d5a12"],
    )
    ax.set_title("Primary killed during a write burst")
    fig.tight_layout()
    fig.savefig(OUT / "failover.png", dpi=140)
    plt.close(fig)

    scaling = json.loads((OUT / "read_scaling.json").read_text(encoding="utf-8"))
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    ax.bar([str(row["replicas"]) for row in scaling], [row["reads_per_second"] for row in scaling], color="#0e6b57")
    ax.set_xlabel("Replicas")
    ax.set_ylabel("Successful reads / second")
    ax.set_title("Read-heavy load through the router")
    fig.tight_layout()
    fig.savefig(OUT / "read_scaling.png", dpi=140)
    plt.close(fig)
    print("wrote allocation, index, failover and read-scaling graphs")


if __name__ == "__main__":
    main()
