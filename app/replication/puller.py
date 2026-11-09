"""Replica pull loop. Steady state sleeps 0.2s; catch-up does not sleep on a full batch."""

from __future__ import annotations

import asyncio

import httpx

from app.config import INTERNAL
from app.db import connect, set_applying
from app.replication.apply import apply_entry


def pull_once(db_path, primary_url: str, limit: int = 500, client: httpx.Client | None = None,
              lock=None, still_replica=None) -> int:
    own = client is None
    client = client or httpx.Client(timeout=15)
    try:
        applied_conn = connect(db_path)
        try:
            applied = applied_conn.execute("SELECT applied_lsn FROM replica_state WHERE id=1").fetchone()[0]
        finally:
            applied_conn.close()
        response = client.get(
            f"{primary_url}/replication/log",
            params={"after": applied, "limit": limit},
            headers={"X-Internal": INTERNAL},
        )
        response.raise_for_status()
        entries = response.json()["entries"]
        if not entries:
            return 0

        def apply() -> int:
            if still_replica is not None and not still_replica():
                return 0
            conn = connect(db_path)
            set_applying(conn, True)
            try:
                conn.execute("BEGIN IMMEDIATE")
                for entry in entries:
                    apply_entry(conn, entry)
                conn.execute(
                    "UPDATE replica_state SET applied_lsn=? WHERE id=1",
                    (entries[-1]["lsn"],),
                )
                conn.execute("COMMIT")
                return len(entries)
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                set_applying(conn, False)
                conn.close()

        if lock is None:
            return apply()
        with lock:
            return apply()
    finally:
        if own:
            client.close()


async def pull_loop(db_path, primary_url_getter, stop: asyncio.Event, lock=None,
                    still_replica=None, interval: float = 0.2, limit: int = 500):
    while not stop.is_set():
        try:
            count = await asyncio.to_thread(
                pull_once, db_path, primary_url_getter(), limit, None, lock, still_replica,
            )
        except Exception:
            count = 0
        if stop.is_set():
            return
        if count < limit:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
