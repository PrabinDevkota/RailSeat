from fastapi.testclient import TestClient

from app.config import INTERNAL
from app.db import connect, init_db, latest_lsn
from app.replication.checksum import compare, table_checksums
from app.replication.puller import pull_once
from app.servers.node import create_app
from app.services.booking import hold_seat
from app.services.payments import capture_due, pay_booking
from app.services.ratings import add_rating
from tests.live import LiveServer
from tests.world import make_world


def test_replica_matches_primary_after_the_log_is_applied(tmp_path):
    primary = tmp_path / "primary.db"
    conn = make_world(tmp_path)
    conn.close()
    (tmp_path / "rail.db").replace(primary)
    hold = connect(primary)
    booking_id = hold_seat(hold, 1, 1, 8, 1, 3)
    pay_booking(hold, booking_id, 1)
    hold.execute("UPDATE train_run SET departure_at='2020-01-01 08:00:00', run_date='2020-01-01'")
    capture_due(hold, as_of=__import__("datetime").datetime(2020, 1, 5))
    add_rating(hold, 1, booking_id, 5, ["good_view", "near_toilet"])
    hold.close()

    replica = tmp_path / "replica.db"
    init_db(replica).close()
    app = create_app(primary, role="primary", background=False)
    with LiveServer(app) as server:
        pulled = 0
        while True:
            count = pull_once(replica, server.url, limit=500)
            pulled += count
            if count < 500:
                break
    assert pulled > 0
    left = table_checksums(connect(primary))
    right = table_checksums(connect(replica))
    assert compare(left, right) == []
    assert latest_lsn(connect(primary)) == connect(replica).execute(
        "SELECT applied_lsn FROM replica_state"
    ).fetchone()[0]


def test_log_endpoint_requires_the_internal_token(tmp_path):
    conn = make_world(tmp_path)
    conn.close()
    app = create_app(tmp_path / "rail.db", background=False)
    client = TestClient(app)
    assert client.get("/replication/log").status_code == 403
    assert client.get("/replication/log", headers={"X-Internal": INTERNAL}).status_code == 200
    assert client.get("/health").status_code == 200
