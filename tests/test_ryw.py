import httpx
from fastapi.testclient import TestClient

from app.config import INTERNAL
from app.db import connect, init_db, latest_lsn
from app.replication.failover import choose_primary, eligible_replicas
from app.replication.puller import pull_once
from app.servers.node import create_app
from tests.live import LiveServer
from tests.world import make_world


def test_stale_replica_is_not_eligible():
    peers = [
        {"url": "http://r1", "healthy": True, "applied_lsn": 10, "role": "replica"},
        {"url": "http://r2", "healthy": True, "applied_lsn": 4, "role": "replica"},
    ]
    assert [p["url"] for p in eligible_replicas(peers, 8)] == ["http://r1"]
    assert eligible_replicas(peers, 11) == []


def test_failover_promotes_the_furthest_healthy_replica():
    peers = [
        {"url": "http://r1", "healthy": True, "applied_lsn": 3},
        {"url": "http://r2", "healthy": True, "applied_lsn": 9},
        {"url": "http://r3", "healthy": False, "applied_lsn": 20},
    ]
    assert choose_primary(peers)["url"] == "http://r2"


def test_read_after_apply_sees_the_write(tmp_path):
    conn = make_world(tmp_path)
    conn.close()
    primary = tmp_path / "rail.db"
    replica_path = tmp_path / "replica.db"
    init_db(replica_path).close()
    app = create_app(primary, background=False)
    headers = {"X-Internal": INTERNAL, "X-User-Id": "1"}
    with LiveServer(app) as server:
        with httpx.Client(base_url=server.url, timeout=10) as client:
            before = client.get("/bookings/999", headers=headers)
            assert before.status_code == 404
            held = client.post(
                "/bookings/hold",
                headers=headers,
                json={"run_id": 1, "seat_id": 4, "from_idx": 1, "to_idx": 3},
            )
            assert held.status_code == 200, held.text
            token = int(held.headers["X-LSN"])
            replica = connect(replica_path)
            assert replica.execute("SELECT COUNT(*) FROM booking").fetchone()[0] == 0
            assert eligible_replicas(
                [{"url": "r", "healthy": True, "applied_lsn": 0, "role": "replica"}], token
            ) == []
            while True:
                count = pull_once(replica_path, server.url)
                if count < 500:
                    break
            applied = connect(replica_path).execute("SELECT applied_lsn FROM replica_state").fetchone()[0]
            assert applied >= token
            assert connect(replica_path).execute("SELECT COUNT(*) FROM booking").fetchone()[0] == 1
            assert latest_lsn(connect(primary)) == applied
