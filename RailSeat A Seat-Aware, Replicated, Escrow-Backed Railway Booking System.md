# RailSeat: A Seat-Aware, Replicated, Escrow-Backed Railway Booking System

*Product Requirements Document for M.Tech DBMS End-Semester Project. Stack: SQLite (WAL), Python, custom replication.*

## 1. Problem Statement

IRCTC allots seats mostly at random. Passengers cannot choose a seat based on what matters: the scenic side of the route, sun exposure, noise, working charging points, or proximity to the toilet. Payments are also all-or-nothing: if a train is cancelled, a coach is missing, or a seat is broken, the refund is slow and opaque. Finally, at peak load (Tatkal), one overloaded database serves both searches and bookings.

RailSeat is a prototype booking platform that fixes these using database-centric design, so the project is about DBMS concepts rather than UI polish.

## 2. Goals and Non-Goals

**Goals**

1. Let users pick specific seats with segment-aware availability (a seat can be sold to A to B and again to B to C).
2. Show route-aware seat quality: view side, landmarks, sun side, and crowd-sourced ratings from verified travellers.
3. Serve reads from mirror (replica) servers while keeping all writes consistent on a primary.
4. Hold payment in escrow with a dispute and arbitration workflow, backed by a double-entry ledger.
5. Prove correctness and performance with measurable experiments.

**Non-Goals**

- No integration with real IRCTC, real payment gateways or real UPI (a mock gateway is used).
- No real-time train tracking. Timetables are seeded synthetic data for 2 to 3 real routes.
- No claim of production scale. SQLite is a deliberate choice; its limits are part of the analysis.

## 3. Personas

| Persona | Need |
| --- | --- |
| Passenger (Asha) | Pick a window seat on the scenic side, know the seat is good, get money back fast if something fails. |
| Frequent traveller (Ravi) | Rate seats after the trip and help others. |
| Arbitrator / Admin (Meera) | Resolve disputes with evidence and rules, see system health. |
| Evaluator (Professor) | See clear DBMS concepts: ACID, concurrency, indexing, replication, triggers, views. |

## 4. Feature Overview (Prioritised)

### P0: Must have

**F1. Segment-based seat inventory.** A train run has stations in order (stop index 1..N). A booking from stop i to stop j occupies segments i..j-1 for a seat. The database itself must prevent double booking.

**F2. User-selected seats.** Interactive coach layout. The user sees free and taken seats for their exact journey and picks one.

**F3. Seat view and quality score.** For each seat and journey, show a score based on landmarks on the left or right of the track, the seat's side, window or aisle, and direction of travel.

**F4. Verified seat ratings.** Only users with a completed booking for that seat can rate it. Ratings are stored per seat per run, with tags (good view, broken window, no socket, near toilet, noisy, too sunny).

**F5. Mirror servers.** One primary (writes) and 2 or more replicas (reads) using log-based asynchronous replication, with read-your-writes consistency.

**F6. Payment escrow.** Funds go HELD, then CAPTURED after the journey and dispute window, or RELEASED/REFUNDED on cancellation or dispute outcome.

### P1: Should have

**F7. Temporary seat hold.** A 5 minute seat lock during payment, with automatic expiry so that seats are not lost when a user is on the payment page.

**F8. Dispute and arbitration.** Reasons: train cancelled, coach not attached, seat unusable, wrong seat description. Rule engine auto-resolves clear cases (train cancelled leads to automatic refund). Manual queue for the rest.

**F9. Waitlist with auto-promotion.** Cancelling a seat triggers promotion of the oldest compatible waitlisted request.

**F10. Replica failover.** Promote the most up-to-date replica if the primary dies.

### P2: Stretch / novelty

**F11. Journey Guide.** Auto-generated list of things to watch for on your route and side, for example "after Lonavala, Ghat section on right side, 14:10 to 14:35".

**F12. Sun-aware seating.** Compute which side gets afternoon sun for the route and departure time, and warn or recommend the shaded side.

**F13. Seat swap marketplace.** Two confirmed passengers on the same run can propose a swap, executed atomically.

**F14. Transparent allocation.** If the user does not choose, auto-allocation explains why (for example "lower berth given for age 60+").

## 5. How the "Good View" Feature Works (Core Novelty)

The trick is to not rate seats only by crowd opinion, which is empty for new routes. Use **two layers**.

**Layer 1: Geographic model (computed).**

- Each route is divided into segments between stations.
- A `landmark` table stores scenic things along a segment: ghat sections, rivers, bridges, sea, lakes, tunnels, with `side` (L or R relative to direction of travel) and `score` (1 to 10), plus approximate time offset from the segment start.
- A `coach_layout` table maps each seat number to its physical `side` (for example, in a Sleeper coach, side lower and side upper berths are on one side of the corridor, and window seats are on the left or right wall) and whether it is a window seat. The physical side is fixed to the coach, but the **left or right of the direction of travel flips if the coach faces the opposite way**, so the model records the `coach_orientation` per run. This is the kind of detail that makes the data model interesting.
- `view_score(seat, journey) = sum over segments in journey of (landmark.score where landmark.side == seat.travel_side) * window_factor`.

**Layer 2: Crowd rating (learned).**

- Verified travellers rate seats (1 to 5 stars + tags) after the journey.
- Aggregate with a Bayesian average so that a seat with one 5 star rating does not beat one with 40 ratings of 4.6: `score = (C*m + sum(ratings)) / (C + n)`, where m is the global mean and C is a prior weight.
- Final displayed score blends Layer 1 and Layer 2, with weight shifting to Layer 2 as `n` grows.

Ratings are kept per seat per route segment group, because seat 13 might be wonderful between two stations and bad elsewhere (for example, near a door with noise at a busy junction).

## 6. Mirror Server Design (The YouTube or AOL Idea, Done Properly)

SQLite allows one writer at a time, so the correct architecture is **single-primary, multiple read replicas**.

```
Client -> Router/Load Balancer
          |-- writes (book, pay, cancel, rate) --> PRIMARY (railseat_primary.db)
          |-- reads (search, seat map, ratings) --> REPLICA 1 / REPLICA 2 / REPLICA 3
PRIMARY --change_log--> replicas pull and apply
```

**Replication mechanism (implement this yourself, it is the heart of the project).**

1. Every write transaction on the primary also appends rows to a `change_log` table (`lsn`, `table`, `op`, `pk`, `payload_json`) via triggers, in the same transaction, so log and data are atomic.
2. Each replica has a `replica_state(applied_lsn)` table and polls the primary for entries with `lsn > applied_lsn`, applying them in order inside one transaction and updating `applied_lsn`.
3. Replicas open their database read-only for the app and apply the log through a separate replication thread.

**Consistency.**

- After a write, the primary returns the new LSN. The client keeps it as a token.
- On reads, the router sends the request to a replica only if `replica.applied_lsn >= token`; otherwise it waits briefly or falls back to the primary. This gives **read-your-writes**.
- Availability numbers shown on search are allowed to be slightly stale, but **the final seat confirmation always happens on the primary**, so staleness can never cause a double booking.

**Failover.** A health-check loop marks the primary dead after k missed heartbeats, picks the replica with the highest `applied_lsn`, promotes it, and repoints the other replicas. Document the data-loss window (asynchronous replication) and how it could be reduced (semi-sync: wait for one replica ack before commit).

**Optional tools to compare against.** Litestream and LiteFS do WAL-based SQLite replication. Mention them in the report and explain why your application-level log is simpler to reason about and demonstrate.

## 7. Payment Escrow and Arbitration Design

**Payment state machine.**

```
INITIATED -> HELD -> (journey complete + dispute window passes) -> CAPTURED
                \-> CANCELLED_BY_USER -> REFUNDED (per refund-rule table)
                \-> TRAIN_CANCELLED -> REFUNDED (auto)
                \-> DISPUTED -> UNDER_REVIEW -> RESOLVED_REFUND | RESOLVED_PARTIAL | RESOLVED_REJECTED
```

**Double-entry ledger.** Every money movement is two or more rows in `ledger_entries` whose amounts sum to zero per `txn_id` (enforced by a trigger or a verification query). Accounts: `user_wallet`, `escrow`, `railway_revenue`, `refund_pool`, `gateway`. This gives audit trail, easy reconciliation, and a strong DBMS talking point.

**Dispute workflow.**

1. Passenger raises a dispute with reason, optional text, optional photo path.
2. Rule engine: if `train_run.status = 'CANCELLED'` then auto-refund 100%. If the seat's recent tags contain `broken_window` reported by 2+ other verified users, fast-track partial refund.
3. Otherwise it enters an arbitrator queue with SLA timestamps.
4. Arbitrator decision writes to `dispute_decision`, and a trigger moves the money in the ledger.
5. Every state change is recorded in an append-only `audit_log`.

The seat ratings and the dispute system feed each other: repeated seat defect tags create an operational "maintenance flag" for that seat.

## 8. Data Model (Core Tables)

Key entities (aim for 3NF, justify any deliberate denormalisation):

`user`, `station`, `train`, `route_stop(train_id, stop_idx, station_id, arr, dep, distance)`, `train_run(run_id, train_id, date, status, coach_orientation)`, `coach(run_id, coach_no, class)`, `seat(coach_id, seat_no, berth_type, physical_side, is_window)`, `landmark(route_segment, side, score, description)`, `booking(booking_id, user_id, run_id, from_idx, to_idx, status)`, `seat_segment_lock(run_id, seat_id, segment_idx, booking_id)`, `seat_hold(hold_id, ..., expires_at)`, `waitlist`, `payment`, `ledger_entries`, `dispute`, `dispute_decision`, `seat_rating`, `seat_rating_summary`, `change_log`, `replica_state`, `audit_log`.

**The double booking guard (the single most important constraint):**

```sql
CREATE TABLE seat_segment_lock (
  run_id INTEGER NOT NULL,
  seat_id INTEGER NOT NULL,
  segment_idx INTEGER NOT NULL,
  booking_id INTEGER NOT NULL REFERENCES booking(booking_id),
  PRIMARY KEY (run_id, seat_id, segment_idx)
) WITHOUT ROWID;
```

Booking seat S from stop 3 to 6 inserts three rows (segments 3, 4, 5) inside one `BEGIN IMMEDIATE` transaction. If any row conflicts, the whole transaction rolls back. No application-level check can bypass this.

**Other DBMS features to showcase.**

- Indexes: composite on `(run_id, segment_idx)`; justify with `EXPLAIN QUERY PLAN` before and after.
- Views: `v_seat_availability`, `v_seat_quality`.
- Triggers: change log, ledger balance check, waitlist promotion, rating summary refresh, audit log.
- Transactions: ACID demonstration with deliberate crash injection (kill the process mid-booking and show no partial state).
- WAL mode, `busy_timeout`, and retry with backoff for writer contention.
- Window functions or CTEs for ranking best seats and computing segment availability.
- Foreign keys, CHECK constraints, partial indexes (for example, active holds only).

## 9. Functional Requirements

| ID | Requirement | Priority |
| --- | --- | --- |
| FR1 | Search trains by from, to and date, served from a replica. | P0 |
| FR2 | Show seat map with per-seat availability for the chosen journey segments. | P0 |
| FR3 | Show per-seat view score, side, window flag and rating tags. | P0 |
| FR4 | Reserve a selected seat atomically on the primary; no double booking under concurrency. | P0 |
| FR5 | Hold a seat for 5 minutes during payment, auto-release on expiry. | P1 |
| FR6 | Create escrow payment through a mock gateway; record in ledger. | P0 |
| FR7 | Capture funds after journey completion plus a 24 hour dispute window. | P0 |
| FR8 | Cancel booking with refund computed from a rule table (time before departure). | P0 |
| FR9 | Raise and resolve disputes; auto-resolve train-cancelled cases. | P1 |
| FR10 | Verified users can rate seats; aggregates update through triggers. | P0 |
| FR11 | Waitlist and auto-promotion on cancellation. | P1 |
| FR12 | Read-your-writes via LSN token routing. | P0 |
| FR13 | Replica lag and health dashboard; failover demo. | P1 |
| FR14 | Journey Guide and sun-aware recommendation. | P2 |

## 10. Non-Functional Requirements

- **Correctness:** zero double bookings in the concurrency test; ledger balances to zero always.
- **Performance targets (prototype):** seat map load under 200 ms from a replica on seeded data of about 50 runs by 20 coaches by 72 seats; booking under 300 ms median on the primary.
- **Replication lag:** under 1 second at normal load; measured and plotted.
- **Durability:** WAL with `synchronous=FULL` on the primary.
- **Auditability:** every payment and dispute transition logged.

## 11. Architecture and Tech Stack

- **Backend:** Python with FastAPI. Use raw SQL through `sqlite3` (avoid heavy ORMs so the SQL is visible to the examiner).
- **Router:** a small FastAPI or Nginx layer for read/write splitting and LSN-aware routing.
- **Databases:** 1 primary and 2 or 3 replicas, each its own SQLite file and process.
- **Frontend:** simple React or HTMX with a coach layout grid and colour-coded view scores.
- **Background workers:** hold expiry, escrow capture, replication pullers, health checks.
- **Packaging:** Docker Compose so the demo runs with one command.
- **Test and load:** pytest for correctness, Locust or a custom thread pool for load.

## 12. Evaluation Plan (What You Show the Examiner)

1. **Concurrency correctness.** 500 simultaneous attempts on the same seat and overlapping segments; show exactly one wins. Also run the same test against a naive check-then-insert version to show it fails, which proves why constraints matter.
2. **Replication.** Throughput with 0, 1, 2 and 3 replicas for a read-heavy workload (90 percent reads); chart the scaling and where writes become the bottleneck.
3. **Staleness.** Measure how often a stale replica read occurs and show the LSN token fixes read-your-writes.
4. **Failover.** Kill the primary during load, measure time to recovery and number of lost transactions.
5. **Index impact.** Query plan and latency before and after indexes.
6. **Ledger integrity.** After 10,000 random operations (book, cancel, dispute, refund), the ledger still sums to zero.
7. **Rating quality.** Show the Bayesian average ranking compared with a naive average.

## 13. Milestones (About 8 to 10 Weeks)

| Week | Deliverable |
| --- | --- |
| 1 | Finalise scope with the teacher, ER diagram, seed data plan for 2 to 3 routes. |
| 2 | Schema (3NF), constraints, seed scripts, landmark and coach layout data. |
| 3 | Booking engine with segment locks, transactions, concurrency tests. |
| 4 | Seat map, view score computation, search APIs. |
| 5 | Ratings, verified-traveller rule, Bayesian summary, triggers. |
| 6 | Replication (change log, pullers, routing, LSN tokens). |
| 7 | Escrow, ledger, cancellation and refund rules. |
| 8 | Disputes, arbitration, waitlist, hold expiry. |
| 9 | Failover, dashboard, load tests, plots. |
| 10 | Report, slides, demo rehearsal. P2 features only if time remains. |

## 14. Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| SQLite single-writer bottleneck | Keep write transactions short, use `BEGIN IMMEDIATE`, retry with backoff, and present it as a measured trade-off in the report. |
| Replication bugs (gaps, duplicates) | Strictly increasing LSN, idempotent apply, checksum comparison between primary and replicas. |
| Landmark data is hard to collect | Hand-curate only 2 or 3 routes (for example a Western Ghats route and a Konkan route) from public maps. State clearly that the data is illustrative. |
| Scope creep | Freeze P0 by week 6; P2 is optional. |
| Subjective ratings misused | Verified travellers only, rate limits, Bayesian smoothing. |

## 15. Success Criteria

- Demo shows a user choosing a scenic seat, paying into escrow, a train cancellation triggering an automatic refund, and a replica being killed without the site going down for reads.
- All P0 requirements pass automated tests.
- Report includes the ER diagram, normalisation proof, concurrency and replication experiments with graphs.

## 16. Future Work and Wider Impact

The same ideas apply beyond railways: bus seats, cinema halls, flights, and event ticketing. Seat quality scoring, escrow-backed bookings and replicated read paths are reusable patterns for any inventory-with-positions platform. A production system would replace SQLite with PostgreSQL (streaming replication, row-level locking) and the mock gateway with a real one.

## Appendix A. Positioning: Why This Is Independent of IRCTC

IRCTC's real system cannot be modified, so RailSeat is an **independent reference implementation**: its own database, synthetic or public timetable data, and a mock payment gateway. The project is graded on database design and engineering, not on adoption by the railways.

- **Pitch:** "IRCTC allots seats randomly today. I built a prototype showing how a booking database should handle seat choice, replicas and escrow payments, and I benchmarked it against random allotment."
- **Baseline experiment:** implement IRCTC-style random allocation as one strategy inside RailSeat. Simulate 1,000 passengers with preferences (window, scenic side, shade) and compare the percentage who get a seat matching their preference under random allotment versus choose-your-seat.
- **Integration boundary:** an `InventoryProvider` interface. In the demo it reads SQLite. In production it would call the railway reservation API (future work).
- **Data:** real station codes and timetables from open datasets, plus hand-curated landmarks for 2 or 3 routes. Clearly label ratings, passengers and payments as simulated.

## Appendix B. Feature Catalogue: Tech Side

| Area | Feature | What it demonstrates |
| --- | --- | --- |
| Seat selection | Interactive coach map with live availability for the chosen journey | Segment-aware queries, frontend and API design |
| Seat insight | View score, sun side, window flag, rating tags, Journey Guide | Geographic modelling, scoring algorithms |
| Routing | Read/write splitting router with LSN-aware replica choice | Distributed systems basics |
| Resilience | Health checks, automatic failover, replica lag dashboard | Fault tolerance |
| Seat holds | 5 minute hold with countdown and auto-release | Background workers, timeouts |
| Fair allocation | Auto-allocation with explanation, priority for senior citizens and ladies quota, and a random baseline strategy | Policy rules, experiment design |
| Waitlist | Auto-promotion on cancellation, notification via in-app inbox | Event-driven flows |
| Payments | Mock gateway with escrow, refund timeline, dispute form with photo path | Workflow design |
| Arbitration console | Admin queue with SLA timers, evidence view, decision buttons | Role-based access |
| Rate limiting and anti-bot | Per-user and per-IP limits on booking endpoints, simple queue for a rush (Tatkal style) | Load handling |
| Seat swap | Two passengers propose and accept a swap | Atomic multi-row updates |
| Observability | Metrics page: QPS, replica lag, lock wait time, failed bookings | Monitoring |
| Deployment | Docker Compose, one command demo | Reproducibility |

## Appendix C. Feature Catalogue: DBMS Side

| Concept | How it appears in RailSeat |
| --- | --- |
| ER design and normalisation | 3NF schema with justified denormalisation (`seat_rating_summary`) |
| Primary and foreign keys, CHECK, UNIQUE | Composite key on `seat_segment_lock` prevents double booking |
| Transactions and ACID | `BEGIN IMMEDIATE` booking, crash injection test showing atomic rollback |
| Isolation and concurrency | Naive check-then-insert fails under 500 threads, constraint-based version passes |
| Locking and retries | WAL mode, `busy_timeout`, exponential backoff on `SQLITE_BUSY` |
| Indexing | Composite and partial indexes, `EXPLAIN QUERY PLAN` before and after |
| Views | `v_seat_availability`, `v_seat_quality`, `v_replica_lag` |
| Triggers | Change log capture, ledger zero-sum check, rating summary refresh, waitlist promotion, audit log |
| Stored-logic style queries | CTEs and window functions for ranking best seats and computing free segments |
| Replication | Log-based async replication, idempotent apply, checksum verification |
| Consistency models | Read-your-writes via LSN token, eventual consistency for search |
| Failover and recovery | Promote highest LSN replica, measure data loss window, WAL checkpoint and backup |
| Double-entry ledger | Every money move balances to zero, reconciliation query |
| Audit and append-only tables | Immutable `audit_log` for payments and disputes |
| Time-based data | Hold expiry, dispute window, refund rule table by time before departure |
| Statistics | Bayesian average for ratings, replica lag percentiles |
| Benchmarking | Throughput versus replica count, latency with and without indexes |
| Data volume | Seeded dataset of about 50 runs by 20 coaches by 72 seats plus 100k simulated bookings |

### Suggested Priority

If time is tight, build in this order: segment locks and booking transactions, seat map and view score, ratings with triggers, replication with LSN routing, escrow ledger, then disputes. Everything else is bonus.

# Part II. Final Implementation Specification (Python)

This part finalises the PRD above. Where Part II and Part I differ, Part II wins. It defines seat allocation, the Python architecture, module-level specs with key code, the API, tests and deliverables.

## 17. Seat Allocation (Final Decision)

Allocation is **not random**. It has three levels, and the system uses the first level that applies.

| Level | Trigger | What happens |
| --- | --- | --- |
| L1 Manual | User clicks a seat on the map | Reserve that seat if free for all segments |
| L2 Preference | User gives preferences (window, scenic side, avoid sun, lower berth, near or far from toilet) | Filter, score, rank, try in order |
| L3 Fallback | User gives nothing | Rank by general seat quality; random only among exact ties |
| Baseline (experiment only) | Benchmark | IRCTC-style random allotment |

**Hard filters (applied first):** seat free for every segment of the journey; not flagged broken; quota rules satisfied (senior citizen lower berth, ladies quota).

**Score for L2 and L3:**

`score = w1*view_score + w2*bayes_rating + w3*pref_match - w4*defect_penalty - w5*fragmentation_penalty`

- `view_score`: computed from landmarks and seat side (section 5).
- `bayes_rating`: Bayesian average of verified ratings.
- `pref_match`: fraction of the user's stated preferences the seat satisfies.
- `defect_penalty`: grows with recent defect tags (broken window, no socket).
- `fragmentation_penalty`: penalises choices that leave small unsellable gaps (a seat given for stops 3 to 4 while 2 to 5 is still wanted), like a bin packing heuristic. This raises the total seats sold.

**Concurrency rule:** ranking may use slightly stale replica data, but the final reservation is always the insert into `seat_segment_lock` on the primary. If the insert fails, try the next ranked seat.

```python
def allocate(conn, req, weights):
    candidates = free_seats(conn, req.run_id, req.from_idx, req.to_idx, req.coach_class)
    candidates = [s for s in candidates if passes_hard_rules(s, req)]
    ranked = sorted(candidates, key=lambda s: -score(s, req, weights))
    for seat in ranked[:10]:
        try:
            return book_seat(conn, req.user_id, req.run_id, seat.id, req.from_idx, req.to_idx)
        except SeatTaken:
            continue
    raise NoSeatAvailable()
```

**Experiment:** simulate 1,000 passengers with random preference profiles on one run. Report preference-match rate, defect-seat rate and seats sold for Random, First-click, L2 and L1. This is the headline result of the project.

## 18. Python Stack and Project Structure

| Purpose | Choice |
| --- | --- |
| Language | Python 3.11+ |
| Web framework | FastAPI with Uvicorn |
| Database | `sqlite3` standard library, raw SQL only (no ORM) |
| HTTP between servers | `httpx` |
| Validation | Pydantic |
| Background jobs | `asyncio` tasks or `threading` workers (APScheduler optional) |
| Frontend | Jinja2 templates with HTMX and a small JS seat grid |
| Tests | `pytest`, `concurrent.futures` for concurrency tests |
| Load and plots | Locust or custom thread pool, `matplotlib`, `pandas` |
| Packaging | Docker Compose |

```
railseat/
  app/
    db.py                 # connections, pragmas, transaction helper
    schema.sql            # tables, indexes, views, triggers
    seed.py               # stations, trains, coaches, landmarks, users
    services/
      booking.py          # book_seat, cancel, hold, expire_holds
      allocation.py       # L1, L2, L3, baseline strategies
      scoring.py          # view_score, bayes_rating, penalties
      ratings.py          # verified rating rules
      payments.py         # escrow state machine, ledger
      disputes.py         # rules engine, arbitration
      waitlist.py         # promotion
    replication/
      puller.py           # replica pulls change_log
      checksum.py         # primary vs replica verification
      failover.py         # health check, promotion
    servers/
      primary.py          # FastAPI: all writes + consistent reads
      replica.py          # FastAPI: reads only
      router.py           # read/write split, LSN-aware routing
    workers/
      hold_expiry.py
      escrow_capture.py
    web/templates/ static/
  tests/
  benchmarks/
  docker-compose.yml
  README.md
```

Run 1 primary (port 8000), 3 replicas (8001 to 8003) and the router (port 8080). Each DB server has its own SQLite file.

## 19. Module Specifications with Key Code

### 19.1 db.py: connections and transactions

```python
import sqlite3, time, random

def connect(path, readonly=False):
    uri = f'file:{path}?mode={"ro" if readonly else "rwc"}'
    conn = sqlite3.connect(uri, uri=True, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=5000')
    if not readonly:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=FULL')
    return conn

def write_tx(conn, fn, retries=5):
    # run fn(conn) inside BEGIN IMMEDIATE with retry on lock contention
    for attempt in range(retries):
        try:
            conn.execute('BEGIN IMMEDIATE')
            result = fn(conn)
            conn.execute('COMMIT')
            return result
        except sqlite3.OperationalError as e:
            conn.execute('ROLLBACK')
            if 'locked' not in str(e) and 'busy' not in str(e):
                raise
            time.sleep((2 ** attempt) * 0.01 + random.random() * 0.01)
        except Exception:
            conn.execute('ROLLBACK')
            raise
    raise RuntimeError('write transaction failed after retries')
```

Note: `isolation_level=None` gives manual transaction control, which is what lets you demonstrate `BEGIN IMMEDIATE`.

### 19.2 booking.py: the double booking guard

Schema essentials:

```sql
CREATE TABLE booking (
  booking_id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES user(user_id),
  run_id INTEGER NOT NULL REFERENCES train_run(run_id),
  seat_id INTEGER NOT NULL REFERENCES seat(seat_id),
  from_idx INTEGER NOT NULL,
  to_idx INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('HELD','CONFIRMED','CANCELLED','EXPIRED','COMPLETED')),
  hold_expires_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK (from_idx < to_idx)
);

CREATE TABLE seat_segment_lock (
  run_id INTEGER NOT NULL,
  seat_id INTEGER NOT NULL,
  segment_idx INTEGER NOT NULL,
  booking_id INTEGER NOT NULL REFERENCES booking(booking_id),
  PRIMARY KEY (run_id, seat_id, segment_idx)
) WITHOUT ROWID;
```

```python
class SeatTaken(Exception): pass

def hold_seat(conn, user_id, run_id, seat_id, from_idx, to_idx, ttl_minutes=5):
    def tx(c):
        cur = c.execute(
            "INSERT INTO booking(user_id, run_id, seat_id, from_idx, to_idx, status, hold_expires_at) "
            "VALUES (?,?,?,?,?,'HELD', datetime('now', ?))",
            (user_id, run_id, seat_id, from_idx, to_idx, f'+{ttl_minutes} minutes'))
        bid = cur.lastrowid
        try:
            c.executemany('INSERT INTO seat_segment_lock VALUES (?,?,?,?)',
                          [(run_id, seat_id, s, bid) for s in range(from_idx, to_idx)])
        except sqlite3.IntegrityError:
            raise SeatTaken()
        return bid
    return write_tx(conn, tx)

def expire_holds(conn):
    def tx(c):
        ids = [r[0] for r in c.execute("SELECT booking_id FROM booking WHERE status='HELD' AND hold_expires_at < datetime('now')")]
        for bid in ids:
            c.execute('DELETE FROM seat_segment_lock WHERE booking_id=?', (bid,))
            c.execute("UPDATE booking SET status='EXPIRED' WHERE booking_id=?", (bid,))
        return ids
    return write_tx(conn, tx)
```

A hold is a booking with status HELD that already owns the segment rows, so a held seat cannot be sold twice. Cancellation and expiry delete the lock rows and then trigger waitlist promotion.

### 19.3 scoring.py: view score and Bayesian rating

```python
def travel_side(seat_physical_side, coach_orientation):
    # coach_orientation = +1 if coach faces direction of travel, -1 if reversed
    if coach_orientation == 1:
        return seat_physical_side
    return 'R' if seat_physical_side == 'L' else 'L'

def view_score(conn, run_id, seat, from_idx, to_idx):
    side = travel_side(seat['physical_side'], seat['coach_orientation'])
    row = conn.execute(
        'SELECT COALESCE(SUM(score),0) FROM landmark WHERE route_id=? AND segment_idx>=? AND segment_idx<? AND side=?',
        (seat['route_id'], from_idx, to_idx, side)).fetchone()
    factor = 1.0 if seat['is_window'] else 0.3
    return row[0] * factor

def bayes_rating(sum_ratings, n, global_mean, prior_weight=10):
    return (prior_weight * global_mean + sum_ratings) / (prior_weight + n)
```

The rating summary table is refreshed by a trigger on `seat_rating`, so reads never aggregate raw ratings.

### 19.4 Replication: change log, puller, LSN

Primary side (triggers in the same transaction as the write, so log and data are atomic):

```sql
CREATE TABLE change_log (
  lsn INTEGER PRIMARY KEY AUTOINCREMENT,
  tbl TEXT NOT NULL,
  op TEXT NOT NULL CHECK (op IN ('I','U','D')),
  pk TEXT NOT NULL,
  payload TEXT,
  ts TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TRIGGER trg_lock_ai AFTER INSERT ON seat_segment_lock
BEGIN
  INSERT INTO change_log(tbl, op, pk, payload)
  VALUES ('seat_segment_lock','I', NEW.run_id||':'||NEW.seat_id||':'||NEW.segment_idx,
          json_object('run_id',NEW.run_id,'seat_id',NEW.seat_id,'segment_idx',NEW.segment_idx,'booking_id',NEW.booking_id));
END;
```

Generate similar triggers (insert, update, delete) for every replicated table with a small script rather than by hand.

Replica side:

```python
async def pull_loop(replica_db, primary_url, interval=0.2):
    while True:
        applied = replica_db.execute('SELECT applied_lsn FROM replica_state').fetchone()[0]
        r = await client.get(f'{primary_url}/replication/log', params={'after': applied, 'limit': 500})
        entries = r.json()
        if entries:
            replica_db.execute('BEGIN IMMEDIATE')
            for e in entries:
                apply_entry(replica_db, e)   # idempotent: INSERT OR REPLACE / DELETE by pk
            replica_db.execute('UPDATE replica_state SET applied_lsn=?', (entries[-1]['lsn'],))
            replica_db.execute('COMMIT')
        await asyncio.sleep(interval)
```

Rules: apply entries in LSN order; applying must be idempotent; one transaction per batch; `checksum.py` compares row counts and a hash per table between primary and replicas.

### 19.5 router.py: read/write split with read-your-writes

- Writes (POST book, pay, cancel, rate, dispute) go to the primary. The response includes header `X-LSN` with the commit LSN.
- The client (or the router, through a cookie) stores the last LSN. For reads it sends `X-Min-LSN`.
- The router picks a healthy replica with `applied_lsn >= X-Min-LSN` (replicas report their LSN via `/health`). If none qualifies, it waits up to 300 ms, then falls back to the primary.
- Seat confirmation and payment status reads always go to the primary.
- Replica choice: round robin among eligible replicas.

### 19.6 failover.py

Poll `/health` on the primary every second. After 3 misses, choose the replica with the highest `applied_lsn`, set it to writable mode (a flag in its config plus restart of its app as primary), tell the router and remaining replicas the new primary URL, and log the event with the measured recovery time and the last applied LSN (data loss window = missing LSNs).

### 19.7 payments.py: escrow and double-entry ledger

```python
def post_entries(c, txn_id, entries):
    # entries: list of (account, amount); credits positive, debits negative
    if round(sum(a for _, a in entries), 2) != 0:
        raise ValueError('unbalanced ledger transaction')
    c.executemany('INSERT INTO ledger_entries(txn_id, account, amount) VALUES (?,?,?)',
                  [(txn_id, acc, amt) for acc, amt in entries])

# example flows
# hold:     post_entries(c, t, [('gateway', -fare), ('escrow', +fare)])
# capture:  post_entries(c, t, [('escrow', -fare), ('railway_revenue', +fare)])
# refund:   post_entries(c, t, [('escrow', -refund), ('user_wallet', +refund), ('escrow', -fee), ('railway_revenue', +fee)])
```

Also enforce balance in the database with a verification view: `SELECT txn_id FROM ledger_entries GROUP BY txn_id HAVING ROUND(SUM(amount),2) != 0` must always return zero rows (asserted in tests).

Payment state transitions live in one function that checks the allowed transitions table (`payment_transition(from_state, to_state)`), so illegal jumps are rejected.

### 19.8 disputes.py: rules then arbitration

```python
def evaluate_dispute(c, d):
    run = get_run(c, d.run_id)
    if run.status == 'CANCELLED':
        return Decision('REFUND_FULL', auto=True, reason='train cancelled')
    if d.reason == 'SEAT_DEFECT' and defect_reports(c, d.seat_id, d.run_id, exclude_user=d.user_id) >= 2:
        return Decision('REFUND_PARTIAL', pct=50, auto=True, reason='defect corroborated')
    return None   # goes to arbitrator queue with SLA deadline
```

Every state change writes to `audit_log`. Arbitrator decisions go to `dispute_decision`, and a worker applies the ledger movement.

### 19.9 ratings.py

A user may rate a seat only if a booking exists with `status='COMPLETED'` for that user, run and seat. Enforce with a trigger that rejects other inserts and a UNIQUE constraint on `(booking_id)`. Ratings include 1 to 5 stars plus tags (`good_view`, `broken_window`, `no_socket`, `near_toilet`, `noisy`, `too_sunny`) stored in a `seat_rating_tag` table.

### 19.10 waitlist.py

On cancellation or expiry, find the oldest waitlisted request whose journey fits the freed segments and whose seat class matches, then run the allocation for it inside the same transaction. Notify through an `inbox` table.

## 20. API Specification

| Method and path | Server | Purpose |
| --- | --- | --- |
| GET `/trains?from=&to=&date=` | Replica | Search trains |
| GET `/runs/{run_id}/seatmap?from_idx=&to_idx=` | Replica | Seat map with availability, view score, rating, tags |
| GET `/runs/{run_id}/journey-guide?from_idx=&to_idx=&seat_id=` | Replica | Landmarks and sun side for that seat |
| POST `/bookings/hold` | Primary | L1 manual hold (returns booking id and LSN) |
| POST `/bookings/allocate` | Primary | L2 or L3 auto-allocation with preferences |
| POST `/bookings/{id}/pay` | Primary | Mock payment, escrow HELD |
| POST `/bookings/{id}/cancel` | Primary | Cancel with refund rule |
| GET `/bookings/{id}` | Primary | Authoritative status |
| POST `/waitlist` | Primary | Join waitlist |
| POST `/ratings` | Primary | Rate a completed seat |
| POST `/disputes` | Primary | Raise dispute |
| GET `/admin/disputes` and POST `/admin/disputes/{id}/decide` | Primary | Arbitration |
| POST `/swaps` and `/swaps/{id}/accept` | Primary | Seat swap (stretch) |
| GET `/replication/log?after=&limit=` | Primary | Change log for replicas |
| GET `/health` | All | Status, role, applied LSN |
| GET `/admin/metrics` | Router | Replica lag, QPS, lock waits, failed bookings |

All write responses include `X-LSN`. All reads accept `X-Min-LSN`.

## 21. Testing and Benchmark Plan (Python Scripts)

| Test or benchmark | File | Pass criterion |
| --- | --- | --- |
| Concurrent double booking | `tests/test_concurrency.py` | 500 threads on one seat and overlapping segments: exactly 1 success |
| Naive version for contrast | `tests/test_naive_race.py` | Check-then-insert version shows duplicates (proves the point) |
| Segment logic | `tests/test_segments.py` | A to B and B to C sell on the same seat; A to C overlaps and fails |
| Crash atomicity | `tests/test_crash.py` | Kill the process mid-booking: no booking without locks, no locks without booking |
| Hold expiry | `tests/test_holds.py` | Expired hold frees the segments and promotes the waitlist |
| Ledger integrity | `tests/test_ledger.py` | After 10,000 random operations the unbalanced-transactions query returns 0 rows |
| Payment transitions | `tests/test_payment_states.py` | Illegal transitions rejected |
| Rating rules | `tests/test_ratings.py` | Only completed-booking users can rate; Bayesian ordering correct |
| Replication correctness | `tests/test_replication.py` | After load, checksums match on all replicas |
| Read-your-writes | `tests/test_ryw.py` | Read after write with token never returns stale data |
| Failover | `benchmarks/failover.py` | Record recovery time and lost LSNs |
| Replica scaling | `benchmarks/read_scaling.py` | Plot reads per second with 0, 1, 2, 3 replicas at 90 percent reads |
| Index impact | `benchmarks/index_impact.py` | Query plan and latency before and after |
| Allocation strategies | `benchmarks/allocation_sim.py` | Preference-match rate for Random, First-click, L2, L1 |

Generate all graphs with `matplotlib` into `benchmarks/results/` so the report and slides reuse them.

## 22. Seed Data Plan

- **Stations and trains:** 2 or 3 real routes (for example a Western Ghats route and a Konkan route) using public timetable data.
- **Runs:** about 50 runs, each with 20 coaches and 72 seats.
- **Landmarks:** hand-curated 20 to 40 landmarks with side and score, from public maps and photos.
- **Users:** 1,000 synthetic users with preference profiles.
- **History:** 100,000 simulated bookings and ratings generated by `seed.py` with a fixed random seed for repeatability.
- Label ratings, passengers and payments as simulated in the report and in the UI footer.

## 23. Deliverables Checklist

1. Source code in the structure above, with a README and one-command Docker Compose.
2. ER diagram, relational schema and normalisation proof (functional dependencies and justification for `seat_rating_summary`).
3. SQL files: `schema.sql`, triggers, views, indexes, seed.
4. Test suite with passing results.
5. Benchmark graphs and the allocation experiment table.
6. Project report: problem, related systems (IRCTC, Litestream, LiteFS, PostgreSQL streaming replication), design, implementation, experiments, limitations, future work.
7. 10 to 12 slide deck and a 5 minute demo script.

**Demo script (5 minutes):** search a Ghats route, open the seat map and show view scores, pick a scenic seat, pay into escrow, show the ledger entries, kill a replica and show reads continue, kill the primary and show failover, raise a dispute on a cancelled train and show the automatic refund, then show the concurrency test and the allocation comparison graph.

## 24. Likely Viva Questions and Short Answers

- **Why SQLite for a booking system?** It keeps the project focused on DBMS fundamentals. Its single writer is a known limit, which the report measures. Production would use PostgreSQL with row-level locking and streaming replication.
- **How do you prevent double booking?** A composite primary key on `(run_id, seat_id, segment_idx)` inside an immediate transaction. The database enforces it, not application checks.
- **What if a replica is stale?** Search may be slightly stale, but confirmation always happens on the primary. Read-your-writes uses the LSN token.
- **What does async replication risk?** Losing the last unreplicated transactions on failover. Semi-sync (wait for one replica ack) reduces that at the cost of write latency.
- **Why a double-entry ledger?** Every movement balances to zero, giving a built-in audit and reconciliation check.
- **Is allocation random?** No. Random is only the IRCTC baseline in the experiment.
- **Why Bayesian rating?** It prevents a seat with one 5-star rating from outranking a seat with many strong ratings.
- **Can this work with real IRCTC?** Not directly. The `InventoryProvider` interface marks where a real reservation API would plug in.

## 25. Final Scope and Timeline

**P0 (must finish):** schema, segment locks, booking transactions, seat map, view score, ratings, replication with LSN routing, escrow with ledger, tests and benchmarks.

**P1 (should finish):** holds, disputes and rules, waitlist, failover.

**P2 (only if time remains):** Journey Guide polish, sun-aware recommendation, seat swap.

| Week | Deliverable |
| --- | --- |
| 1 | Scope sign-off, ER diagram, seed plan |
| 2 | Schema, triggers, seed scripts |
| 3 | Booking engine and concurrency tests |
| 4 | Seat map, scoring, allocation L1 to L3 |
| 5 | Ratings and triggers, allocation experiment |
| 6 | Replication, router, LSN tokens |
| 7 | Escrow, ledger, refund rules |
| 8 | Disputes, waitlist, hold expiry |
| 9 | Failover, metrics page, benchmarks |
| 10 | Report, slides, demo rehearsal |
