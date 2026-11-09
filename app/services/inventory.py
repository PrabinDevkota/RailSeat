"""The boundary a real reservation API would replace. The demo reads SQLite."""

from __future__ import annotations

from typing import Protocol

from app.services.allocation import allocate
from app.services.booking import hold_seat
from app.services.readmodel import search_trains, seatmap


class InventoryProvider(Protocol):
    def search(self, origin: str, dest: str, date: str) -> dict: ...
    def seat_map(self, run_id: int, from_idx: int, to_idx: int) -> dict: ...
    def hold(self, user_id: int, run_id: int, seat_id: int, from_idx: int, to_idx: int) -> int: ...


class SqliteInventory:
    def __init__(self, conn):
        self.conn = conn

    def search(self, origin: str, dest: str, date: str) -> dict:
        return search_trains(self.conn, origin, dest, date)

    def seat_map(self, run_id: int, from_idx: int, to_idx: int) -> dict:
        return seatmap(self.conn, run_id, from_idx, to_idx)

    def hold(self, user_id: int, run_id: int, seat_id: int, from_idx: int, to_idx: int) -> int:
        return hold_seat(self.conn, user_id, run_id, seat_id, from_idx, to_idx)

    def allocate(self, user_id: int, run_id: int, from_idx: int, to_idx: int, coach_class: str | None, prefs: dict):
        return allocate(self.conn, user_id, run_id, from_idx, to_idx, coach_class, prefs, "L2")
