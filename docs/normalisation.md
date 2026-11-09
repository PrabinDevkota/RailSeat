# Normalisation

The operational tables are in third normal form. Two structures are deliberately not: `seat_rating_summary` and the JSON payload in `change_log`. Both are derived, and both exist so a query the demo runs on every seat map stays cheap.

## Functional dependencies

**Identity**

- `station_id → code, name, lat, lon` and `code → station_id`
- `train_id → train_no, name, route_name` and `train_no → train_id`
- `user_id → name, email, password_hash, role, age, gender, preferences` and `email → user_id`

**Route**

- `(train_id, stop_idx) → station_id, distance_km, arr_min, dep_min, bearing_out, arr, dep, day_offset`
- `run_id → train_id, run_date, status, coach_orientation, departure_at`
- `(train_id, run_date) → run_id` for a single rake that day

**Rolling stock**

- `coach_id → run_id, coach_no, coach_class, position`
- `(run_id, coach_no) → coach_id`
- `seat_id → coach_id, seat_no, berth_type, physical_side, is_window, near_toilet, quota, broken`
- `(coach_id, seat_no) → seat_id`

Berth type does not determine the side by itself in a way we store twice: `physical_side`, `is_window` and `berth_type` are attributes of the seat, fixed by the coach layout when the seat row is inserted. They are not recomputed from each other at read time, because a middle berth is not a window and a side-upper is.

**Inventory**

- `booking_id → user_id, run_id, seat_id, from_idx, to_idx, status, fare, explain, hold_expires_at`
- `(run_id, seat_id, segment_idx) → booking_id`

The second dependency is the double-booking rule. `explain` is text produced when the seat was chosen. It is stored so the ticket still shows the reason after the ranking inputs change. It is not used as a key and it does not determine any other booking attribute.

**Money**

- `payment_id → booking_id, user_id, amount, state, gateway_ref`
- `txn_id → payment_id, closed, created_at`
- `entry_id → txn_id, account, amount, user_id, payment_id, note`
- `(from_state, to_state)` is a legal edge, not a fact about a payment

A payment's amount is the fare copied at pay time. Fare can be derived from distance and class, and the booking keeps its own copy so a later fare-table edit does not rewrite history. That is a snapshot, the same idea as an invoice line.

**Reputation**

- `rating_id → booking_id, user_id, run_id, seat_id, stars` and `booking_id → rating_id`
- `(rating_id, tag) → tag` (the tag row has no extra columns)
- `dispute_id → booking_id, user_id, payment_id, run_id, seat_id, reason, detail, status`
- `decision_id → dispute_id, outcome, refund_pct, auto, reason, arbitrator_id` and `dispute_id → decision_id`

The rating trigger rejects an insert unless that user has a `COMPLETED` booking for that run and seat. The user, run and seat on the rating row therefore match the booking. They are repeated so a seat-quality query does not join through a cancelled history to find who travelled. They are checked, not independently updated.

## Why these are in 3NF

Take `booking`. The key is `booking_id`. Every non-key column depends on the whole key. There is no decomposition of a single-column key into a partial dependency. There is no transitive dependency: `user_id` does not determine `fare` or `status`, and `seat_id` does not determine `run_id`. The same argument applies to `payment`, `dispute`, `coach` and `seat`.

`route_stop` and `seat_segment_lock` use composite keys. A stop's distance depends on the train and the stop index together, not on either alone. A lock's booking depends on all three of run, seat and segment. No non-key column depends on a subset of the key, because the only non-key column of a lock is `booking_id`.

`seat_rating_tag` is all key. It is already in BCNF.

## seat_rating_summary is not in 3NF

The summary key is `(train_id, coach_no, seat_no, from_idx, to_idx)`.

Stored beside it: `n`, `sum_stars`, and a count for each tag (`good_view`, `broken_window`, `no_socket`, `near_toilet`, `noisy`, `too_sunny`).

Those values are determined by the set of `seat_rating` and `seat_rating_tag` rows for that seat and segment span. They are transitively dependent on the key through the ratings. A strict 3NF design would drop the summary and aggregate on read:

```sql
SELECT COUNT(*), SUM(stars)
FROM seat_rating r
JOIN seat s ON s.seat_id = r.seat_id
JOIN coach c ON c.coach_id = s.coach_id
WHERE c.coach_no = ? AND s.seat_no = ?;
```

The seat map evaluates that for every visible berth. On the full catalogue that is a correlated aggregate over a large rating history, which is the wrong place to pay. The summary is a materialised view with a trigger:

- an inserted rating increments `n` and `sum_stars` for the overall row `(from_idx, to_idx) = (0, 0)` and for the journey span
- each tag increments its column
- `broken_window + no_socket ≥ 3` raises a `maintenance_flag`

The Bayesian score is not stored. `v_seat_quality` computes `(sum_stars + C·m) / (n + C)` with `C = 10` and `m` the global mean, so changing the prior does not require a rewrite. The summary holds the sufficient statistics only.

If the trigger and the ratings ever diverge, `rebuild_summaries` recomputes the table from the ratings in one set-based pass. The demo seed does that before it snapshots the log.

## change_log payload

`change_log(lsn, tbl, op, pk, payload)` stores the row image as JSON. The payload is determined by the source row at that LSN, so it is redundant the moment it is written. Replicas apply it without reading the primary's tables. Deleting it would force either a logical dump on every catch-up or a statement log that cannot be replayed idempotently. The redundancy is the replication protocol, not a modelling accident. The table is append-only unless a node is applying a snapshot.

## Anomalies the constraints close

| Anomaly | Constraint |
| --- | --- |
| Two bookings, one segment | Primary key of `seat_segment_lock` |
| Rating without a journey | Trigger on `seat_rating` |
| Payment skipping a state | `payment_transition` plus a trigger |
| Ledger transaction that does not balance | Trigger on `ledger_txn.closed` |
| Rewriting history | Append-only triggers on `audit_log` and `change_log` |
| Replica applying the log and logging it again | `apply_mode.applying = 1` disables the change triggers |
