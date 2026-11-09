from datetime import date, timedelta

from app.services.readmodel import resolve_station, search_trains
from tests.world import make_world


def test_station_name_and_code_both_resolve(tmp_path):
    conn = make_world(tmp_path)
    assert resolve_station(conn, "a") == "A"
    assert resolve_station(conn, "Alpha") == "A"
    assert resolve_station(conn, "nowhere") is None


def test_search_accepts_a_station_name_and_keeps_a_cancelled_run_visible(tmp_path):
    conn = make_world(tmp_path)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    found = search_trains(conn, "Alpha", "Delta", tomorrow)
    assert found["query"]["from"] == "A"
    assert found["query"]["to"] == "D"
    assert found["unknown"] == []
    assert len(found["results"]) == 1
    train = found["results"][0]
    assert train["bookable"] is True
    assert train["classes"][0]["code"] == "SL"
    assert train["classes"][0]["free"] == 24
    assert "h" in train["duration"]

    conn.execute("UPDATE train_run SET status='CANCELLED' WHERE run_id=1")
    cancelled = search_trains(conn, "A", "D", tomorrow)
    assert cancelled["results"][0]["bookable"] is False
    assert cancelled["results"][0]["classes"][0]["free"] == 24

    same = search_trains(conn, "Alpha", "A", tomorrow)
    assert same["same"] is True
    assert same["results"] == []
    missing = search_trains(conn, "nowhere", "Delta", tomorrow)
    assert missing["unknown"] == ["nowhere"]
