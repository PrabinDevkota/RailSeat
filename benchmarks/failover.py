"""Kill the primary during writes and record recovery time and lost LSNs."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from app.config import INTERNAL
from app.db import connect, init_db, latest_lsn
from app.services.booking import BookingError, hold_seat
from tests.world import make_world

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmarks" / "results"
PY = sys.executable


def _wait(url: str, timeout: float = 15) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(url + "/health", timeout=0.4)
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError:
            time.sleep(0.05)
    raise RuntimeError(f"{url} did not start")


def _spawn(db: Path, port: int, role: str, primary: str) -> subprocess.Popen:
    url = f"http://127.0.0.1:{port}"
    cmd = [
        PY, "-m", "app.servers.node",
        "--role", role,
        "--db", str(db),
        "--port", str(port),
        "--node-url", url,
        "--primary-url", primary,
    ]
    return subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    primary_db = OUT / "fail_primary.db"
    if primary_db.exists():
        primary_db.unlink()
    world = make_world(OUT, seats=72)
    world.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    world.close()
    (OUT / "rail.db").replace(primary_db)
    replicas = []
    for index in (1, 2):
        path = OUT / f"fail_replica{index}.db"
        if path.exists():
            path.unlink()
        init_db(path).close()
        replicas.append(path)
    primary_url = "http://127.0.0.1:9100"
    procs = [_spawn(primary_db, 9100, "primary", primary_url)]
    procs.append(_spawn(replicas[0], 9101, "replica", primary_url))
    procs.append(_spawn(replicas[1], 9102, "replica", primary_url))
    try:
        _wait(primary_url)
        _wait("http://127.0.0.1:9101")
        _wait("http://127.0.0.1:9102")
        writer = connect(primary_db)

        def book_some(start: int, count: int) -> None:
            seat = start
            done = 0
            while done < count:
                try:
                    hold_seat(writer, 4, 1, seat, 1, 3)
                    done += 1
                except BookingError:
                    pass
                seat += 1

        book_some(1, 12)
        writer.close()
        deadline = time.time() + 20
        while time.time() < deadline:
            lag = [
                httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).json()["applied_lsn"]
                for port in (9101, 9102)
            ]
            if min(lag) >= latest_lsn(connect(primary_db)):
                break
            time.sleep(0.1)
        writer = connect(primary_db)
        book_some(40, 8)
        primary_lsn = latest_lsn(writer)
        writer.close()
        before = {
            9101: httpx.get("http://127.0.0.1:9101/health", timeout=1).json()["applied_lsn"],
            9102: httpx.get("http://127.0.0.1:9102/health", timeout=1).json()["applied_lsn"],
        }
        started = time.perf_counter()
        try:
            httpx.post(primary_url + "/control/shutdown", headers={"X-Internal": INTERNAL}, timeout=1)
        except httpx.HTTPError:
            pass
        procs[0].wait(timeout=5)
        best_port = max(before, key=before.get)
        httpx.post(
            f"http://127.0.0.1:{best_port}/control/promote",
            headers={"X-Internal": INTERNAL},
            timeout=5,
        )
        promoted = _wait(f"http://127.0.0.1:{best_port}")
        held = httpx.post(
            f"http://127.0.0.1:{best_port}/bookings/hold",
            headers={"X-Internal": INTERNAL, "X-User-Id": "4"},
            json={"run_id": 1, "seat_id": 30, "from_idx": 1, "to_idx": 3},
            timeout=5,
        )
        elapsed = time.perf_counter() - started
        lost = max(0, primary_lsn - before[best_port])
        result = {
            "recovery_seconds": round(elapsed, 3),
            "primary_lsn": primary_lsn,
            "promoted_port": best_port,
            "promoted_lsn_at_kill": before[best_port],
            "lost_lsns": lost,
            "write_after_promote": held.status_code,
            "role_after": promoted.get("role"),
        }
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
    (OUT / "failover.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    fig, ax = plt.subplots(figsize=(6.2, 3.4))
    ax.bar(["Recovery (s)", "Lost LSNs"], [result["recovery_seconds"], result["lost_lsns"]], color=["#0e6b57", "#8d5a12"])
    ax.set_title("Primary killed during a write burst")
    fig.tight_layout()
    fig.savefig(OUT / "failover.png", dpi=140)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
