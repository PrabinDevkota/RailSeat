"""Runtime configuration. Environment variables override the demo defaults."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("RAILSEAT_DATA", ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)

SECRET = os.environ.get("RAILSEAT_SECRET", "railseat-demo-secret")
INTERNAL = os.environ.get("RAILSEAT_INTERNAL", "dev-internal")
DB_PATH = Path(os.environ.get("RAILSEAT_DB", DATA / "primary.db"))
ROLE = os.environ.get("RAILSEAT_ROLE", "primary")
NODE_URL = os.environ.get("NODE_URL", "http://127.0.0.1:8000")
PRIMARY_URL = os.environ.get("PRIMARY_URL", "http://127.0.0.1:8000")
REPLICA_URLS = [
    u.strip()
    for u in os.environ.get(
        "REPLICA_URLS",
        "http://127.0.0.1:8001,http://127.0.0.1:8002,http://127.0.0.1:8003",
    ).split(",")
    if u.strip()
]
CLUSTER_FILE = Path(os.environ.get("RAILSEAT_CLUSTER", DATA / "cluster.json"))
PORT = int(os.environ.get("PORT", "8000"))
SEED_SCALE = os.environ.get("SEED_SCALE", "demo")
HOLD_MINUTES = 5
DISPUTE_WINDOW_HOURS = 24
BAYES_PRIOR = 10
BAYES_MEAN_FALLBACK = 3.5
ALLOC_WEIGHTS = {
    "w1": 1.2,  # scaled view score
    "w2": 1.0,  # Bayesian rating
    "w3": 2.5,  # preference match
    "w4": 1.5,  # defect penalty
    "w5": 0.8,  # fragmentation penalty
}
FARE_PER_KM = {"SL": 0.55, "3A": 1.25, "2A": 1.85, "1A": 3.10}
MIN_FARE = {"SL": 90.0, "3A": 220.0, "2A": 360.0, "1A": 640.0}
RATE_LIMIT_USER = 10
RATE_LIMIT_IP = 30
RATE_WINDOW_SEC = 60
