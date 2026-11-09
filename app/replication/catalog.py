"""Replicated table catalogue.

Column lists here are the single source for change-log triggers, snapshot
logging and idempotent apply. tests/test_catalog.py checks them against
PRAGMA table_info so they cannot drift from schema.sql.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Table:
    name: str
    pk: tuple[str, ...]
    columns: tuple[str, ...]


# Parents before children so a snapshot can be applied into an empty database.
REPLICATED: tuple[Table, ...] = (
    Table(
        "user",
        ("user_id",),
        (
            "user_id", "name", "email", "password_hash", "role", "age", "gender",
            "pref_window", "pref_scenic", "pref_shade", "pref_lower", "pref_toilet",
            "created_at",
        ),
    ),
    Table(
        "station",
        ("station_id",),
        ("station_id", "code", "name", "lat", "lon"),
    ),
    Table(
        "train",
        ("train_id",),
        ("train_id", "train_no", "name", "route_name"),
    ),
    Table(
        "route_stop",
        ("train_id", "stop_idx"),
        (
            "train_id", "stop_idx", "station_id", "arr", "dep", "day_offset",
            "distance_km", "arr_min", "dep_min", "bearing_out",
        ),
    ),
    Table(
        "train_run",
        ("run_id",),
        (
            "run_id", "train_id", "run_date", "status", "coach_orientation",
            "departure_at",
        ),
    ),
    Table(
        "coach",
        ("coach_id",),
        ("coach_id", "run_id", "coach_no", "coach_class", "position"),
    ),
    Table(
        "seat",
        ("seat_id",),
        (
            "seat_id", "coach_id", "seat_no", "berth_type", "physical_side",
            "is_window", "near_toilet", "quota", "broken",
        ),
    ),
    Table(
        "landmark",
        ("landmark_id",),
        (
            "landmark_id", "train_id", "segment_idx", "side", "score", "kind",
            "description", "time_offset_min",
        ),
    ),
    Table(
        "booking",
        ("booking_id",),
        (
            "booking_id", "user_id", "run_id", "seat_id", "from_idx", "to_idx",
            "status", "hold_expires_at", "fare", "explain", "created_at",
        ),
    ),
    Table(
        "seat_segment_lock",
        ("run_id", "seat_id", "segment_idx"),
        ("run_id", "seat_id", "segment_idx", "booking_id"),
    ),
    Table(
        "waitlist",
        ("waitlist_id",),
        (
            "waitlist_id", "user_id", "run_id", "from_idx", "to_idx", "coach_class",
            "preferences", "status", "created_at", "promoted_booking_id",
        ),
    ),
    Table(
        "payment",
        ("payment_id",),
        (
            "payment_id", "booking_id", "user_id", "amount", "state",
            "gateway_ref", "created_at", "updated_at",
        ),
    ),
    Table(
        "ledger_txn",
        ("txn_id",),
        ("txn_id", "payment_id", "closed", "created_at"),
    ),
    Table(
        "ledger_entries",
        ("entry_id",),
        (
            "entry_id", "txn_id", "account", "amount", "user_id", "payment_id",
            "note", "created_at",
        ),
    ),
    Table(
        "dispute",
        ("dispute_id",),
        (
            "dispute_id", "booking_id", "user_id", "payment_id", "run_id", "seat_id",
            "reason", "detail", "photo_path", "status", "sla_due_at", "created_at",
        ),
    ),
    Table(
        "dispute_decision",
        ("decision_id",),
        (
            "decision_id", "dispute_id", "outcome", "refund_pct", "auto", "reason",
            "arbitrator_id", "created_at",
        ),
    ),
    Table(
        "seat_rating",
        ("rating_id",),
        (
            "rating_id", "booking_id", "user_id", "run_id", "seat_id", "stars",
            "created_at",
        ),
    ),
    Table(
        "seat_rating_tag",
        ("rating_id", "tag"),
        ("rating_id", "tag"),
    ),
    Table(
        "seat_rating_summary",
        ("train_id", "coach_no", "seat_no", "from_idx", "to_idx"),
        (
            "train_id", "coach_no", "seat_no", "from_idx", "to_idx", "n", "sum_stars",
            "good_view", "broken_window", "no_socket", "near_toilet", "noisy", "too_sunny",
        ),
    ),
    Table(
        "maintenance_flag",
        ("flag_id",),
        ("flag_id", "train_id", "coach_no", "seat_no", "reason", "active", "created_at"),
    ),
    Table(
        "audit_log",
        ("audit_id",),
        ("audit_id", "entity", "entity_id", "action", "detail", "actor_id", "created_at"),
    ),
    Table(
        "inbox",
        ("message_id",),
        ("message_id", "user_id", "body", "read_at", "created_at"),
    ),
    Table(
        "seat_swap",
        ("swap_id",),
        ("swap_id", "run_id", "booking_a", "booking_b", "status", "created_at"),
    ),
    Table(
        "rating_global",
        ("id",),
        ("id", "n", "sum_stars"),
    ),
)

BY_NAME = {t.name: t for t in REPLICATED}
