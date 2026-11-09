"""The timetable is a network: one origin reaches several destinations."""

from datetime import date, timedelta

from app.db import connect, init_db
from app.seed import ensure_services
from app.seed_data import STATIONS, TRAINS
from app.services.readmodel import search_trains


def test_every_stop_is_a_known_station_and_the_route_is_ordered():
    numbers = [train["train_no"] for train in TRAINS]
    assert len(numbers) == len(set(numbers))
    assert len(TRAINS) >= 12
    origins = {}
    for train in TRAINS:
        stops = train["stops"]
        assert len(stops) >= 2
        previous = -1
        for code, _arr, _dep, dist, _arr_min, _dep_min, _bearing in stops:
            assert code in STATIONS
            assert dist >= previous
            previous = dist
        last_segment = len(stops) - 1
        for segment, side, score, _kind, _text, _offset in train["landmarks"]:
            assert 1 <= segment <= last_segment
            assert side in ("L", "R")
            assert 1 <= score <= 10
        origins.setdefault(stops[0][0], set()).add(stops[-1][0])
    assert len(origins["CSMT"]) >= 4
    assert {"CSMT", "SBC"} <= origins["PUNE"]
    assert len(origins["SBC"]) >= 3
    assert {"PUNE", "RN", "MAO", "SBC"} <= origins["CSMT"]


def test_services_load_without_a_wipe_and_search_finds_each_city(tmp_path):
    path = tmp_path / "network.db"
    init_db(path).close()
    first = ensure_services(path, offsets=(1,))
    assert first["trains"] == len(TRAINS)
    assert first["runs"] == len(TRAINS)
    again = ensure_services(path, offsets=(1,))
    assert again == {"stations": 0, "trains": 0, "runs": 0}

    conn = connect(path)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    try:
        mumbai_pune = search_trains(conn, "CSMT", "PUNE", tomorrow)
        numbers = {row["train_no"] for row in mumbai_pune["results"]}
        assert {"11007", "12127", "16209"} <= numbers

        mumbai_blr = search_trains(conn, "Mumbai CSMT", "KSR Bengaluru", tomorrow)
        assert [row["train_no"] for row in mumbai_blr["results"]] == ["16209"]
        assert mumbai_blr["results"][0]["bookable"] is True

        pune_mumbai = {row["train_no"] for row in search_trains(conn, "PUNE", "CSMT", tomorrow)["results"]}
        assert {"11008", "11020"} <= pune_mumbai

        coast = {row["train_no"] for row in search_trains(conn, "MAO", "MAJN", tomorrow)["results"]}
        assert "17317" in coast
        mysuru = search_trains(conn, "SBC", "MYS", tomorrow)["results"]
        assert {row["train_no"] for row in mysuru} >= {"56216", "56641"}

        overnight = conn.execute(
            """
            SELECT rs.day_offset
            FROM route_stop rs
            JOIN train t ON t.train_id = rs.train_id
            JOIN station s ON s.station_id = rs.station_id
            WHERE t.train_no = '16209' AND s.code = 'SBC'
            """
        ).fetchone()
        assert overnight["day_offset"] == 1
        free = next(row for row in mumbai_blr["results"][0]["classes"] if row["code"] == "SL")
        assert free["free"] > 0
    finally:
        conn.close()
