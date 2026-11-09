"""Reads per second with 0, 1, 2 and 3 replicas. About 90 percent of calls are reads."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from app.db import connect, init_db, latest_lsn
from tests.world import make_world

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmarks" / "results"
PY = sys.executable
SECONDS = 2.5
THREADS = 8


def _spawn(cmd: list[str], env: dict) -> subprocess.Popen:
    return subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wait(url: str):
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            response = httpx.get(url + "/health", timeout=0.4)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.05)
    raise RuntimeError(url)


def _prepare(path: Path):
    if path.exists():
        path.unlink()
    conn = make_world(OUT, seats=24)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    (OUT / "rail.db").replace(path)


def _one(replicas: int) -> dict:
    primary = OUT / "scale_primary.db"
    _prepare(primary)
    env = os.environ.copy()
    procs = [
        _spawn(
            [PY, "-m", "app.servers.node", "--role", "primary", "--db", str(primary),
             "--port", "9200", "--node-url", "http://127.0.0.1:9200"],
            env,
        )
    ]
    urls = []
    try:
        for index in range(replicas):
            port = 9201 + index
            path = OUT / f"scale_r{index}.db"
            if path.exists():
                path.unlink()
            init_db(path).close()
            url = f"http://127.0.0.1:{port}"
            urls.append(url)
            procs.append(
                _spawn(
                    [PY, "-m", "app.servers.node", "--role", "replica", "--db", str(path),
                     "--port", str(port), "--node-url", url, "--primary-url", "http://127.0.0.1:9200"],
                    env,
                )
            )
        _wait("http://127.0.0.1:9200")
        for url in urls:
            _wait(url)
        if urls:
            target = latest_lsn(connect(primary))
            deadline = time.time() + 20
            while time.time() < deadline:
                lag = [httpx.get(url + "/health", timeout=1).json()["applied_lsn"] for url in urls]
                if min(lag) >= target:
                    break
                time.sleep(0.1)
        router_env = env.copy()
        router_env["PRIMARY_URL"] = "http://127.0.0.1:9200"
        router_env["REPLICA_URLS"] = ",".join(urls)
        router_env["ROUTER_PORT"] = "9280"
        router_env["RAILSEAT_CLUSTER"] = str(OUT / "scale_cluster.json")
        procs.append(_spawn([PY, "-m", "app.servers.router"], router_env))
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if httpx.get("http://127.0.0.1:9280/admin/metrics", timeout=0.4).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            raise RuntimeError("router")
        reads = 0
        writes = 0
        errors = 0
        lock = threading.Lock()
        stop = [0.0]
        ready = threading.Barrier(THREADS + 1)
        go = threading.Event()

        def worker():
            nonlocal reads, writes, errors
            client = httpx.Client(timeout=5, follow_redirects=True)
            try:
                client.post(
                    "http://127.0.0.1:9280/login",
                    data={"email": "arun@railseat.dev", "password": "railseat"},
                )
            except httpx.HTTPError:
                pass
            ready.wait(timeout=20)
            go.wait(timeout=20)
            n = 0
            while time.time() < stop[0]:
                n += 1
                try:
                    if n % 10 == 0:
                        response = client.post(
                            "http://127.0.0.1:9280/bookings/hold",
                            json={"run_id": 1, "seat_id": (n % 24) + 1, "from_idx": 1, "to_idx": 3},
                            headers={"X-User-Id": "4"},
                        )
                        kind = "w"
                    else:
                        response = client.get("http://127.0.0.1:9280/runs/1?from_idx=1&to_idx=4")
                        kind = "r"
                    ok = response.status_code < 500
                except httpx.HTTPError:
                    ok = False
                    kind = "e"
                with lock:
                    if not ok:
                        errors += 1
                    elif kind == "r":
                        reads += 1
                    else:
                        writes += 1

        threads = [threading.Thread(target=worker) for _ in range(THREADS)]
        for thread in threads:
            thread.start()
        ready.wait(timeout=20)
        stop[0] = time.time() + SECONDS
        go.set()
        for thread in threads:
            thread.join()
        return {
            "replicas": replicas,
            "reads": reads,
            "writes": writes,
            "errors": errors,
            "seconds": SECONDS,
            "reads_per_second": round(reads / SECONDS, 1),
        }
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
        time.sleep(0.3)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = [_one(n) for n in (0, 1, 2, 3)]
    (OUT / "read_scaling.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    ax.bar([str(r["replicas"]) for r in rows], [r["reads_per_second"] for r in rows], color="#0e6b57")
    ax.set_xlabel("Replicas")
    ax.set_ylabel("Successful reads / second")
    ax.set_title("Read-heavy load through the router")
    fig.tight_layout()
    fig.savefig(OUT / "read_scaling.png", dpi=140)
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
