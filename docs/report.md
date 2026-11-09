# RailSeat

A seat-aware, replicated, escrow-backed booking system. Passengers, ratings, payments and the three timetables are simulated. The database behaviour is not.

## 1. Problem

On a reserved railway ticket the berth number is the product, and the passenger finds out which side of the train they are on after they have paid. Allocation in the familiar IRCTC flow is effectively a lottery among the berths that are still free in that class. A second, separate failure is operational: a popular search is a read, a booking is a write, and a single SQLite writer cannot be the only process serving both if the point of the exercise is to show replication rather than to hide it.

RailSeat separates those concerns.

- Inventory is a set of segments of one run. The database, not the application, refuses a second booking of the same seat on an overlapping segment.
- The seat map scores the side of travel from hand-placed landmarks, then blends that score with a Bayesian rating from verified travellers.
- The fare is held in escrow and posted as a balanced ledger transaction. A cancelled train refunds that escrow by rule.
- One primary accepts writes. Three replicas apply a change log and serve searches once they have caught the caller's log position.

## 2. Related systems

**IRCTC.** The production reservation system allocates a berth inside a quota and a class. The passenger does not rank the side of the coach, and the operator does not publish a per-berth quality score. RailSeat keeps that random allocation as the baseline in the experiment and does not call a live reservation API. `InventoryProvider` is the seam where a real inventory service would sit. Nothing in this repository talks to IRCTC.

**Litestream.** Litestream tails a SQLite WAL and copies frames to object storage so a database can be restored after a disk dies. It is a backup stream, not a queryable replica and not a failover controller. RailSeat needs both: replicas that answer `SELECT`, and a router that can promote one of them. The change log is logical (row images) rather than a shipping of WAL frames, which makes the apply path visible in SQL and easy to checksum.

**LiteFS.** LiteFS is closer to this design. It sits in front of SQLite, replicates the database, and arranges a single primary. RailSeat does the same job in userspace with an explicit `change_log`, an HTTP puller and a small router, because the replication protocol is part of what the project has to explain. The cost is that we do not get LiteFS's file-level consistency machinery. The gain is that every replicated row is an ordinary table the examiner can query.

**PostgreSQL streaming replication.** A production version of this system would use row-level locks and streaming WAL, with the option of synchronous commit to one replica. That removes the SQLite single-writer queue and gives a well-understood failover story. It would also hide the log behind the server. This prototype keeps the log in a table so the lag, the checksum and the lost-LSN window are queries rather than server internals. The failover section measures what async replication gives up.

## 3. Design

### 3.1 Segments

Stops are numbered from the origin. A ticket from stop `i` to stop `j` inserts one `seat_segment_lock` row for each segment in `[i, j)`, in the same `BEGIN IMMEDIATE` transaction as the booking. The primary key is `(run_id, seat_id, segment_idx)`. A conflicting insert aborts the transaction, so a crash cannot leave a booking without its locks or locks without a booking. The crash test kills the process with `os._exit` on both sides of the commit and checks those two states.

A hold is a booking in status `HELD` whose locks are already taken, with a five-minute expiry. There is no second hold table. Expiry deletes the locks, marks the booking `EXPIRED`, and promotes the oldest matching waitlist row in that same transaction.

### 3.2 What "a good view" means

Each landmark has a segment, a side (`L` or `R`) and a score. Summing scores between the passenger's stops gives a side total. The scenic side is the larger total.

A coach has a physical left and right, painted on the berth. `coach_orientation` is `1` in the normal direction and `-1` when the rake is reversed. Travel side is the physical side flipped by that sign. A window on the scenic travel side scores higher than a window on the quiet side, which scores higher than an aisle or a middle berth. The raw score is scaled to 0–5 for the ranker.

The timetable is illustrative, drawn from public geography rather than a live feed. Fifteen services cover Mumbai, Pune, the Konkan coast and the Bengaluru side, including the return trains. The three that carry the scripted story are:

- 11007 Deccan Express, CSMT to Pune, Bhor Ghat on the right after Karjat.
- 10111 Konkan Kanya, CSMT to Madgaon, sea on the right going south.
- 56641 Malnad Express, KSR Bengaluru to Mangaluru, the Sakleshpur descent.

Every class in this prototype has the same 72-berth sleeper layout. That is a simplification, stated here so it is not mistaken for a real coach diagram. Side berths are the windows on physical right. Lower berths in the ladies and senior bays carry those quotas.

### 3.3 Ratings

Only a user with a `COMPLETED` booking for that seat may rate it. The trigger rejects anything else, and `booking_id` is unique on `seat_rating`. Tags live in `seat_rating_tag`. A summary row caches the count and the sum so the seat map does not aggregate the history per berth. The displayed rating is Bayesian with prior 10 toward the global mean, falling back to 3.5 when nobody has rated anything. One 5-star rating therefore ranks below a long run of 4.6s. The seat map blends view and rating, and the blend shifts toward the rating as the number of votes grows.

Three or more `broken_window` or `no_socket` tags on a seat raise a maintenance flag. The seat stays visible and cannot be held.

### 3.4 Allocation

The final write is always the lock insert on the primary. The strategies differ only in which free seat they try first.

| Strategy | Order |
| --- | --- |
| Random | Shuffle, the IRCTC-style baseline |
| First | Lowest coach and seat number |
| L1 | View score, then preference match |
| L2 | `1.2·view + 1.0·Bayes + 2.5·preference − 1.5·defects − 0.8·fragmentation` |
| L3 | The same score with preferences cleared, used when the passenger has no opinion |

Hard rules run before the score: a broken seat, an active maintenance flag, a ladies quota berth for a male passenger, a senior quota for a younger passenger, and a cancelled run are never offered.

### 3.5 Replication

Tables that are part of the product are listed in `app/replication/catalog.py` in foreign-key order. Insert, update and delete triggers append `change_log(lsn, tbl, op, pk, payload)` while `apply_mode.applying = 0`. A replica fetches `GET /replication/log?after=&limit=`, applies each row with `INSERT ON CONFLICT DO UPDATE` (or `DO NOTHING` when the row is all key), writes the original LSN into its own log, and advances `replica_state.applied_lsn` in that transaction. Applying sets `applying = 1`, so the replica does not log the log.

The seed loads with triggers paused and then writes one snapshot into the log. Live bookings after that are ordinary trigger output. Checksums hash each replicated table. On the demo cluster, after catch-up, the primary and replica 1 checksums match.

The router sends a write to the primary and remembers the `X-LSN` cookie. A later read waits up to 300ms for a replica whose applied LSN is at least that cookie and within 500 LSNs of the primary. Otherwise the primary answers. An empty replica satisfies cookie 0 and would show no trains; the lag bound is what keeps search on the primary during catch-up.

Health is polled once a second. Three missed replies and the router promotes the healthy replica with the highest applied LSN, repoints the others, and writes `data/cluster.json`. A process that restarts reads that file, so the old primary comes back as a replica instead of a second writer.

### 3.6 Escrow

Accounts are `gateway`, `escrow`, `railway_revenue`, `user_wallet` and `refund_pool`. Credits are positive, debits negative, and a transaction cannot close unless the entries sum to zero.

| Event | Movement |
| --- | --- |
| Pay | gateway −fare, escrow +fare, payment `HELD` |
| Capture, after arrival and the dispute window | escrow −fare, railway_revenue +fare |
| Cancel by the passenger | refund by the hour slab, the rest to revenue |
| Train cancelled | 100 percent back, via `TRAIN_CANCELLED` then `REFUNDED` |
| Dispute upheld | escrow to the wallet for the decided percent |
| Dispute rejected on a ticket that has not departed | payment returns to `HELD`, money stays in escrow |
| Dispute rejected after the journey | leftover escrow moves to revenue |

Slabs, measured against departure: at least 48 hours, 100 percent; 12 to 48, 75 percent; 4 to 12, 50 percent; under 4, 25 percent; after departure, nothing.

A dispute whose run is `CANCELLED` refunds in full without an arbitrator. `SEAT_UNUSABLE` with `broken_window` from two other verified passengers refunds half. Anything else waits in Meera's queue. Rejecting a claim on a future train does not capture the fare early: the ticket is still valid, so escrow remains.

## 4. Implementation notes

Python 3.12, FastAPI, uvicorn, httpx, Pydantic, Jinja, pytest, matplotlib. The web UI is server-rendered HTML and a small script, with no CDN. The seat grid is the coach: nine bays, six main berths and two side berths, coloured by the blended score.

The demo clock uses local time on both sides of the connection (`datetime('now','localtime')` and `datetime.now()`), so an expiry computed in Python matches an expiry computed in SQL.

Workers on the primary expire holds and capture escrow after the dispute window. They do not touch a paid ticket on a cancelled run; that refund is the dispute, which is the demo.

## 5. Experiments

All figures are from the scripts in `benchmarks/`, written to `benchmarks/results/`. The machine is the development workstation. Numbers will move on another box; the shape of the result is the point.

### 5.1 Concurrency

Five hundred threads call `hold_seat` on one seat and an overlapping segment. The test requires exactly one success and 499 `SeatTaken`, and one distinct booking id. The same race against a check-then-insert, which sleeps between the read and the write and does not take the unique key, produces more than one success. The constraint is the difference, not the application lock.

### 5.2 Ledger

Ten thousand mixed holds, payments, cancellations and dispute decisions leave `v_unbalanced_txns` empty. A hand-built transaction that closes with a single entry is aborted by the trigger and rolls back cleanly.

### 5.3 Index

`seat_segment_lock` is `WITHOUT ROWID`, so its primary key already seeks on `run_id`. To show the index rather than the primary key, the benchmark copies 200,000 lock rows onto a heap table and looks up `(run_id, segment_idx)`.

| Plan | Mean lookup |
| --- | --- |
| `SCAN lock_probe` | 9.64 ms |
| `SEARCH` using `idx_probe` | 0.44 ms |

That is about a 22× drop on this box. The graph is `benchmarks/results/index_impact.png`.

### 5.4 Allocation

Forty passengers, one 72-berth coach, preferences rotating through window, scenic side, lower berth, and window-plus-scenic. Each strategy starts from the same empty rake. A request matches when every preference it stated is true of the berth it received.

| Strategy | Match rate | Mean view (0–5, scenic window = 5) |
| --- | --- | --- |
| Random | 0.350 | 2.23 |
| First free | 0.350 | 2.34 |
| L1, view then preference | 0.725 | 3.35 |
| L2, full score | 0.725 | 3.35 |

L1 and L2 tie on a cold coach. There are no ratings yet, so the Bayesian term is the same for every seat and the preference weight dominates both orders. Both more than double the baseline match rate. The graph is `benchmarks/results/allocation.png`. On a coach with a real rating history, L2 can prefer a well-rated quiet berth over an unrated scenic one; L1 will not, because it sorts by view first.

### 5.5 Failover

A primary and two replicas on ports 9100–9102. Twelve bookings are replicated. Eight more are committed. The primary is then killed through its shutdown endpoint, the replica with the higher applied LSN is promoted, and a new hold is posted to it.

| | |
| --- | --- |
| Time from kill to a successful hold on the new primary | 5.16 s |
| Primary LSN at the kill | 149 |
| Promoted replica's LSN at the kill | 149 |
| LSNs present only on the dead primary | 0 |
| HTTP status of the booking after promotion | 200 |

Zero lost LSNs means the pullers had applied the burst before the process exited. That is the favourable case of async replication, not a guarantee. A replica that is paused, or a primary that dies before the next pull, loses the tail of the log. The router records that gap when it promotes. Synchronous wait-for-one-replica would shrink the window and add a round trip to every booking.

### 5.6 Read scaling

Eight client threads request seat-map pages through the router for 2.5 seconds. Every tenth call is a hold. With so few requests per thread the mix is almost entirely reads, which is the case the replica tier is for.

| Replicas | Successful reads / second |
| --- | --- |
| 0 (primary only) | 9.6 |
| 1 | 28.8 |
| 2 | 53.2 |
| 3 | 52.8 |

Two replicas multiply the seat-map rate by about five against the primary serving every page itself. The third replica does not move the number. The ceiling here is the router and the eight clients, not another database file. Writes, when the test does produce them, still land on one SQLite writer. That is the bottleneck a move to PostgreSQL would remove. The graph is `benchmarks/results/read_scaling.png`.

## 6. What the demo database contains

A fresh `python -m app.cluster` loads the demo scale: 80 users, 29 stations, 15 trains, 157 runs, 628 coaches and 45,216 seats, with about 4,500 bookings and about 1,900 ratings, fixed by `random.Random(7)`. The bookings sit on the original three trains. The other twelve services are empty for the next ten days. Asha has a completed Deccan trip in S1 berth 7 that she has not rated, and a paid ticket on a cancelled Konkan run. Ravi has three completed trips. The hero Deccan run (the one the home page searches for tomorrow) leaves the lower seat numbers free so the map is usable. The full scale is a separate command and is what the 100,000-booking figure in the brief refers to. The demo scale is the one Docker starts, so a cold `compose up` finishes. The database already on disk is not wiped by a normal start.

## 7. Limitations

- One writer. Bookings queue on the primary. The concurrency test shows correctness under that queue, not horizontal write scaling.
- Replication is asynchronous and logical. A checksum detects divergence; it does not repair an arbitrary split brain. The cluster file is the guard against the old primary waking up as a writer.
- Landmark scores are authored, not measured. Reversing a rake flips the travel side, and a wrong orientation flips the recommendation.
- Every class uses the sleeper bay diagram.
- The payment gateway is a row in the ledger. There is no bank.
- Rate limits are in-process. They reset when the primary restarts, and they do not coordinate across a future multi-writer design because there is only one writer.
- The 100,000-booking catalogue is not what the live demo serves, by choice. The schema and the seed path are the same.

## 8. What a next version would change

PostgreSQL, streaming replication, and synchronous commit to one replica. A real coach diagram per class. Landmark scores reviewed against photographs. The `InventoryProvider` boundary filled by an operator API, with RailSeat keeping the seat map, the escrow and the rating summary on our side of that boundary.
