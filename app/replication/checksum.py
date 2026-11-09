"""Row-count and content hash per replicated table."""

from __future__ import annotations

import hashlib

from app.replication.catalog import REPLICATED


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def table_checksums(conn) -> dict[str, dict]:
    out = {}
    for table in REPLICATED:
        order = ", ".join(table.pk)
        rows = conn.execute(f"SELECT * FROM {table.name} ORDER BY {order}").fetchall()
        digest = hashlib.sha256()
        for row in rows:
            digest.update("|".join(_cell(row[col]) for col in table.columns).encode())
            digest.update(b"\n")
        out[table.name] = {"rows": len(rows), "sha256": digest.hexdigest()}
    return out


def compare(left: dict, right: dict) -> list[str]:
    mismatches = []
    names = sorted(set(left) | set(right))
    for name in names:
        a, b = left.get(name), right.get(name)
        if a != b:
            mismatches.append(name)
    return mismatches
