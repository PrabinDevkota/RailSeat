"""Generate insert/update/delete change-log triggers for every replicated table."""

from __future__ import annotations

from app.replication.catalog import REPLICATED


def _json_object(prefix: str, columns: tuple[str, ...]) -> str:
    parts = ", ".join(f"'{c}', {prefix}.{c}" for c in columns)
    return f"json_object({parts})"


def _pk_expr(prefix: str, pk: tuple[str, ...]) -> str:
    return " || ':' || ".join(f"{prefix}.{c}" for c in pk)


def change_trigger_sql() -> str:
    gate = "(SELECT applying FROM apply_mode WHERE id = 1) = 0"
    blocks = [
        "-- Generated change-log triggers. Same transaction as the write, so log and data commit together.",
    ]
    for table in REPLICATED:
        ident = table.name
        pk_new = _pk_expr("NEW", table.pk)
        pk_old = _pk_expr("OLD", table.pk)
        payload_new = _json_object("NEW", table.columns)
        blocks.append(
            f"""
CREATE TRIGGER trg_cl_{ident}_i AFTER INSERT ON {ident}
WHEN {gate}
BEGIN
  INSERT INTO change_log(tbl, op, pk, payload)
  VALUES ('{ident}', 'I', {pk_new}, {payload_new});
END;

CREATE TRIGGER trg_cl_{ident}_u AFTER UPDATE ON {ident}
WHEN {gate}
BEGIN
  INSERT INTO change_log(tbl, op, pk, payload)
  VALUES ('{ident}', 'U', {pk_new}, {payload_new});
END;

CREATE TRIGGER trg_cl_{ident}_d AFTER DELETE ON {ident}
WHEN {gate}
BEGIN
  INSERT INTO change_log(tbl, op, pk, payload)
  VALUES ('{ident}', 'D', {pk_old}, NULL);
END;
"""
        )
    return "\n".join(blocks)


def snapshot_statements() -> list[str]:
    """SQL that records the current row image of every replicated table."""
    stmts = []
    for table in REPLICATED:
        cols = ", ".join(f"'{c}', {c}" for c in table.columns)
        pk = " || ':' || ".join(table.pk)
        stmts.append(
            f"INSERT INTO change_log(tbl, op, pk, payload) "
            f"SELECT '{table.name}', 'I', {pk}, json_object({cols}) FROM {table.name}"
        )
    return stmts
