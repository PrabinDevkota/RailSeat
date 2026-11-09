from app.db import init_db
from app.replication.catalog import REPLICATED


def test_catalog_matches_schema(tmp_path):
    conn = init_db(tmp_path / "c.db")
    for table in REPLICATED:
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table.name})")]
        assert cols == list(table.columns), table.name
