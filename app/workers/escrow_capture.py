"""Capture escrow once the journey and the dispute window are both over."""

from __future__ import annotations

from datetime import datetime

from app.db import connect
from app.services.payments import capture_due


def capture_once(db_path, as_of: datetime | None = None, force_booking_id: int | None = None) -> list[int]:
    conn = connect(db_path)
    try:
        return capture_due(conn, as_of=as_of, force_booking_id=force_booking_id)
    finally:
        conn.close()
