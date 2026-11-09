"""Health bookkeeping and the choice of who becomes primary."""

from __future__ import annotations


def choose_primary(peers: list[dict]) -> dict | None:
    """peers have url, healthy, applied_lsn. Highest applied LSN wins."""
    healthy = [p for p in peers if p.get("healthy")]
    if not healthy:
        return None
    return max(healthy, key=lambda p: (p.get("applied_lsn") or 0, p["url"]))


def eligible_replicas(peers: list[dict], min_lsn: int) -> list[dict]:
    return [
        p for p in peers
        if p.get("healthy") and p.get("role") != "primary" and (p.get("applied_lsn") or 0) >= min_lsn
    ]
