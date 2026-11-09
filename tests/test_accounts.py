"""Registration, password checks, and ownership."""

from fastapi.testclient import TestClient

from app.config import INTERNAL
from app.servers.node import create_app
from tests.world import make_world


def _client(tmp_path):
    conn = make_world(tmp_path)
    conn.close()
    return TestClient(create_app(tmp_path / "rail.db", background=False))


def test_register_rejects_bad_input_and_duplicate_email(tmp_path):
    with _client(tmp_path) as client:
        bad = client.post(
            "/auth/register",
            headers={"X-Internal": INTERNAL},
            json={"name": "A", "email": "not-an-email", "password": "short", "age": 0, "gender": "X"},
        )
        assert bad.status_code == 400
        ok = client.post(
            "/auth/register",
            headers={"X-Internal": INTERNAL},
            json={"name": "Nila", "email": "Nila@Example.com", "password": "correct-horse", "age": 29, "gender": "f"},
        )
        assert ok.status_code == 201, ok.text
        body = ok.json()
        assert body["role"] == "PASSENGER"
        assert body["email"] == "nila@example.com"
        assert body["gender"] == "F"
        dup = client.post(
            "/auth/register",
            headers={"X-Internal": INTERNAL},
            json={"name": "Nila", "email": "nila@example.com", "password": "correct-horse", "age": 29, "gender": "F"},
        )
        assert dup.status_code == 409


def test_new_account_can_sign_in_and_legacy_password_still_works(tmp_path):
    with _client(tmp_path) as client:
        client.post(
            "/auth/register",
            headers={"X-Internal": INTERNAL},
            json={"name": "Nila", "email": "nila@example.com", "password": "correct-horse", "age": 29, "gender": "F"},
        )
        fresh = client.post(
            "/auth/login",
            headers={"X-Internal": INTERNAL},
            json={"email": "nila@example.com", "password": "correct-horse"},
        )
        assert fresh.status_code == 200
        assert fresh.json()["role"] == "PASSENGER"
        wrong = client.post(
            "/auth/login",
            headers={"X-Internal": INTERNAL},
            json={"email": "nila@example.com", "password": "correct-horse-no"},
        )
        assert wrong.status_code == 401
        legacy = client.post(
            "/auth/login",
            headers={"X-Internal": INTERNAL},
            json={"email": "asha@railseat.dev", "password": "railseat"},
        )
        assert legacy.status_code == 200
        assert legacy.json()["name"] == "Asha"


def test_booking_is_private_and_admin_routes_check_role(tmp_path):
    with _client(tmp_path) as client:
        created = client.post(
            "/auth/register",
            headers={"X-Internal": INTERNAL},
            json={"name": "Nila", "email": "nila@example.com", "password": "correct-horse", "age": 29, "gender": "F"},
        )
        nila = str(created.json()["user_id"])
        held = client.post(
            "/bookings/hold",
            headers={"X-Internal": INTERNAL, "X-User-Id": "1"},
            json={"run_id": 1, "seat_id": 4, "from_idx": 1, "to_idx": 3},
        )
        assert held.status_code == 200, held.text
        booking_id = held.json()["booking_id"]
        stranger = client.get(
            f"/bookings/{booking_id}",
            headers={"X-Internal": INTERNAL, "X-User-Id": nila},
        )
        assert stranger.status_code == 403
        owner = client.get(
            f"/bookings/{booking_id}",
            headers={"X-Internal": INTERNAL, "X-User-Id": "1"},
        )
        assert owner.status_code == 200
        missing = client.get("/bookings/999", headers={"X-Internal": INTERNAL, "X-User-Id": "1"})
        assert missing.status_code == 404
        blocked = client.get("/admin/ledger", headers={"X-Internal": INTERNAL, "X-User-Id": "1"})
        assert blocked.status_code == 403
        allowed = client.get("/admin/ledger", headers={"X-Internal": INTERNAL, "X-User-Id": "3"})
        assert allowed.status_code == 200


def test_password_and_preferences_round_trip(tmp_path):
    with _client(tmp_path) as client:
        headers = {"X-Internal": INTERNAL, "X-User-Id": "1"}
        changed = client.post(
            "/me/password",
            headers=headers,
            json={"current_password": "railseat", "new_password": "new-secret"},
        )
        assert changed.status_code == 200, changed.text
        old = client.post(
            "/auth/login",
            headers={"X-Internal": INTERNAL},
            json={"email": "asha@railseat.dev", "password": "railseat"},
        )
        assert old.status_code == 401
        new = client.post(
            "/auth/login",
            headers={"X-Internal": INTERNAL},
            json={"email": "asha@railseat.dev", "password": "new-secret"},
        )
        assert new.status_code == 200
        prefs = client.post(
            "/me/preferences",
            headers=headers,
            json={"pref_window": 1, "pref_scenic": 0, "pref_shade": 1, "pref_lower": 0, "pref_toilet": "FAR"},
        )
        assert prefs.status_code == 200, prefs.text
        me = client.get("/me", headers=headers)
        assert me.status_code == 200
        body = me.json()
        assert body["pref_window"] == 1
        assert body["pref_shade"] == 1
        assert body["pref_toilet"] == "FAR"
        bad = client.post(
            "/me/preferences",
            headers=headers,
            json={"pref_toilet": "MIDDLE"},
        )
        assert bad.status_code == 400
