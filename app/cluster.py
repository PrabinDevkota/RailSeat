"""Start the primary, three replicas and the router as child processes."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def _python():
    return sys.executable


def main():
    scale = "demo"
    if "--full" in sys.argv:
        scale = "full"
    data = ROOT / "data"
    data.mkdir(exist_ok=True)
    cluster_file = data / "cluster.json"
    if cluster_file.exists():
        cluster_file.unlink()
    primary_db = data / "primary.db"
    if "--reseed" in sys.argv or not primary_db.exists():
        subprocess.run([_python(), "-m", "app.seed", "--scale", scale, "--db", str(primary_db), "--force"], check=True, cwd=ROOT)
    for name in ("replica1.db", "replica2.db", "replica3.db"):
        path = data / name
        if path.exists() and "--reseed" in sys.argv:
            path.unlink()
        if not path.exists():
            from app.db import init_db
            init_db(path).close()
    commands = [
        ([_python(), "-m", "app.servers.primary", "--db", str(primary_db), "--port", "8000", "--node-url", "http://127.0.0.1:8000"], "primary"),
        ([_python(), "-m", "app.servers.replica", "--db", str(data / "replica1.db"), "--port", "8001", "--node-url", "http://127.0.0.1:8001", "--primary-url", "http://127.0.0.1:8000"], "replica1"),
        ([_python(), "-m", "app.servers.replica", "--db", str(data / "replica2.db"), "--port", "8002", "--node-url", "http://127.0.0.1:8002", "--primary-url", "http://127.0.0.1:8000"], "replica2"),
        ([_python(), "-m", "app.servers.replica", "--db", str(data / "replica3.db"), "--port", "8003", "--node-url", "http://127.0.0.1:8003", "--primary-url", "http://127.0.0.1:8000"], "replica3"),
        ([_python(), "-m", "app.servers.router"], "router"),
    ]
    procs = []
    logs = data / "logs"
    logs.mkdir(exist_ok=True)
    for cmd, name in commands:
        handle = open(logs / f"{name}.log", "w", encoding="utf-8")
        procs.append(subprocess.Popen(cmd, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT, env=os.environ.copy()))
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            response = httpx.get("http://127.0.0.1:8080/admin/metrics", timeout=1)
            if response.status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.4)
    else:
        print("Router did not become ready. See data/logs.")
        return
    print("RailSeat is up at http://127.0.0.1:8080")
    print("Sign in as asha@railseat.dev, ravi@railseat.dev or meera@railseat.dev")
    print("Password: railseat")
    try:
        for proc in procs:
            proc.wait()
    except KeyboardInterrupt:
        for proc in procs:
            proc.terminate()


if __name__ == "__main__":
    main()
