"""Idempotent application of one change-log row."""

from __future__ import annotations

import json

from app.replication.catalog import BY_NAME


def apply_entry(conn, entry: dict) -> None:
    table = BY_NAME.get(entry["tbl"])
    if table is None:
        return
    if entry["op"] == "D":
        values = str(entry["pk"]).split(":")
        where = " AND ".join(f"{col}=?" for col in table.pk)
        conn.execute(f"DELETE FROM {table.name} WHERE {where}", values)
    else:
        payload = json.loads(entry["payload"] or "{}")
        columns = table.columns
        placeholders = ", ".join("?" for _ in columns)
        update = ", ".join(f"{col}=excluded.{col}" for col in columns if col not in table.pk)
        conflict = ", ".join(table.pk)
        sql = (
            f"INSERT INTO {table.name} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict}) DO "
        )
        sql += f"UPDATE SET {update}" if update else "NOTHING"
        conn.execute(sql, [payload.get(col) for col in columns])
    conn.execute(
        """
        INSERT OR IGNORE INTO change_log(lsn, tbl, op, pk, payload, ts)
        VALUES (?,?,?,?,?,?)
        """,
        (entry["lsn"], entry["tbl"], entry["op"], entry["pk"], entry.get("payload"), entry.get("ts")),
    )
