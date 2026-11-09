# RailSeat

RailSeat is a seat-aware railway booking system built as an M.Tech DBMS prototype. A passenger searches a train, sees which berths face the ghats or the sea, reserves one, and pays into escrow. The fare stays there until the journey and a dispute window have both passed.

Timetables, passengers, ratings and payments are simulated. This is not an IRCTC client and it does not call a live reservation API.

## Start the application

Python 3.11 or newer. Commands below are for PowerShell, run from the project folder `RailSeat`.

1. Install the libraries once:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

2. Start the whole cluster. The first run creates `data\primary.db` and the three replica files. Later runs keep the database that is already there.

```powershell
python -m app.cluster
```

3. Wait until the terminal prints `RailSeat is up at http://127.0.0.1:8080`, then open that address. Leave the terminal running. Ctrl+C stops the primary, the three replicas and the router together. Process logs are in `data\logs\`.

| What | Where |
| --- | --- |
| Site, the only page you open | http://127.0.0.1:8080 |
| Primary, all writes | http://127.0.0.1:8000 |
| Replicas, reads | http://127.0.0.1:8001 , :8002 , :8003 |

The router is the site. Booking, payment and ticket pages go to the primary. Search and the coach diagram may be served by a replica that has caught up.

### Start again, or start from an empty database

| Command | What it does |
| --- | --- |
| `python -m app.cluster` | Starts the five processes. Seeds only if `data\primary.db` is missing. |
| `python -m app.cluster --reseed` | Deletes the demo database and loads it again, then starts. Bookings you made are gone. |
| `python -m app.cluster --full` | Same, with the large catalogue (about 50 runs, 20 coaches, 1,000 users). |
| `python -m app.seed --extend --db data\primary.db` | Adds upcoming runs for the original three trains. Does not delete bookings. |
| `python -m app.seed --services --db data\primary.db` | Adds any missing station, train and the next ten days of runs. Does not delete bookings. |
| `python -m app.seed --scale full --db data\primary.db` | Rebuilds `primary.db` at full scale and wipes it first. Restart the cluster afterwards. |

`--reseed` and a full seed both wipe `data\primary.db`. Replica files are rebuilt empty and catch up from the primary log.

### Docker

Docker Compose builds the same five processes and publishes only the site:

```powershell
docker compose up --build
```

Open http://127.0.0.1:8080. The database lives in the `railseat` volume, not in `data\`. `docker compose down` stops it.

### Run one process at a time

Use this only when you want a log in that window. Start the primary first, then the replicas, then the router. Ports 8000–8003 and 8080 must be free.

```powershell
python -m app.servers.primary --db data/primary.db --port 8000 --node-url http://127.0.0.1:8000
python -m app.servers.replica --db data/replica1.db --port 8001 --node-url http://127.0.0.1:8001 --primary-url http://127.0.0.1:8000
python -m app.servers.replica --db data/replica2.db --port 8002 --node-url http://127.0.0.1:8002 --primary-url http://127.0.0.1:8000
python -m app.servers.replica --db data/replica3.db --port 8003 --node-url http://127.0.0.1:8003 --primary-url http://127.0.0.1:8000
python -m app.servers.router
```

If `data\primary.db` does not exist yet, create it before the primary:

```powershell
python -m app.seed --scale demo --db data/primary.db
```

### Where the data is

Each node is one SQLite file. There is no separate database server and no ORM.

| File | Contents |
| --- | --- |
| `data\primary.db` | Users, timetables, bookings, payments, ratings, the change log |
| `data\replica1.db`, `replica2.db`, `replica3.db` | Copies, updated by pulling the log |
| `data\*.db-wal`, `*.db-shm` | SQLite write-ahead files. They belong to the database next to them. |
| `data\cluster.json` | Written after failover so a restarted node does not become a second primary |
| `data\logs\` | stdout from `python -m app.cluster` |

`RAILSEAT_DATA`, `RAILSEAT_DB`, `PRIMARY_URL`, `REPLICA_URLS` and `ROUTER_PORT` override those paths and ports. The defaults above are what the demo uses.

### Sign in

| Who | Email | Password | What they can do |
| --- | --- | --- | --- |
| Asha | asha@railseat.dev | railseat | Book, pay, rate a finished trip, dispute the cancelled Konkan ticket |
| Ravi | ravi@railseat.dev | railseat | A frequent traveller with completed trips |
| Meera | meera@railseat.dev | railseat | Admin. Opens Queue for disputes, the ledger, and failover |

Create account on the home page registers a new passenger. The form checks name, email, a password of 8 to 72 characters, age and gender. That account cannot become an admin. Account, after sign-in, changes the password and the window, scenic, shade, lower-berth and toilet preferences used by Choose for me.

Asha’s account already holds a completed Deccan trip in coach S1, berth 7, which she has not rated, and a paid ticket on a cancelled Konkan run. Raising a dispute on that ticket refunds the full fare without an arbitrator. A ticket page and its PDF open only for the passenger who owns the booking, or for an admin.

## What you do on the site

1. **Plan.** From and To are dropdowns of every station, shown as the name and the code. Train schedule lists the next departures and fills the stations and the date. Swap exchanges the two stations. If that date has no scheduled train, the next running date is shown.
2. **Timetable.** Each train shows departure, arrival, duration, distance, and a cell per class: free berths and fare. A cancelled run stays on the board and cannot be booked.
3. **Coach.** Nine bays. Main berths are lower, middle, upper. Side berths are the windows on the other wall. Colour is the blend of view score and Bayesian rating. `L` is a ladies quota, `S` is a senior quota. A reversed rake swaps left and right.
4. **Reserve.** The berth is held for five minutes. Paying moves the fare from the gateway account into escrow. Cancelling refunds by the hours left before departure.
5. **Bookings and messages.** Every ticket has a booking number `RS` plus six digits. Download PDF on the ticket, or from Bookings, gives a one-page slip with boarding time, arrival time and date. Waitlist promotions and dispute decisions land in Messages.
6. **Choose for me.** Window, scenic side, shade and lower berth are ranked, then the winning berth is held on the primary. Joining the waitlist promotes the oldest match when a berth is cancelled or a hold expires.
7. **Journey guide.** From a berth, the guide lists landmarks on your side and the other side, and which side the sun is on.
8. **Queue.** Meera sees replica lag, ledger balances, and open disputes. Stopping the primary promotes the replica with the highest applied log position after three missed heartbeats.

## Routes in the demo

These are teaching timetables, not a working timetable.

| Train | Run | What the seat map is for |
| --- | --- | --- |
| 11007 Deccan Express | CSMT to Pune, departs 07:10 | Bhor Ghat is on the right after Karjat |
| 12127 Intercity Express | CSMT to Pune, afternoon | Same ghats, fewer stops |
| 11008 Deccan Express | Pune to CSMT, departs 15:00 | The ghat is on the left going north |
| 11020 Sahyadri Express | Pune to CSMT, morning | A second Mumbai service from Pune |
| 16590 Rani Chennamma | Pune to KSR Bengaluru via Miraj, Belagavi and Hubballi | The morning departure toward the south |
| 10111 Konkan Kanya | CSMT to Madgaon, daytime | Going south, the sea stays on the right |
| 10104 Mandovi Express | CSMT to Ratnagiri | A shorter Konkan run |
| 10112 Konkan Kanya | Madgaon to CSMT | The sea is on the left going north |
| 16209 Udyan Express | CSMT to KSR Bengaluru, overnight via Pune, Miraj, Belagavi and Hubballi | Arrives the next morning |
| 16589 Rani Chennamma | KSR Bengaluru to Pune | The reverse of the Udyan line |
| 56216 Chamundi Express | KSR Bengaluru to Mysuru | A short morning run |
| 56641 Malnad Express | KSR Bengaluru to Mangaluru Central | The descent through Sakleshpur |
| 56642 Malnad Express | Mangaluru Central to KSR Bengaluru | The climb back up the ghats |
| 17317 Matsyagandha | Madgaon to Mangaluru via Karwar and Udupi | Coast on the right |
| 17318 Matsyagandha | Mangaluru to Madgaon | Coast on the left |

The demo seed is 80 users and 29 stations. Mumbai, Pune, Madgaon, Mangaluru and Bengaluru each have trains to more than one city, including the return service. The original three trains keep their past runs, the cancelled Konkan run, and the scripted tickets. The newer services are scheduled for the next ten days with empty coaches, so those berths can be booked. Each run has four coaches (S1, S2, B1, A1) and 72 berths a coach. `random.Random(7)` fixes the draw. The full catalogue adds a denser history on the original three trains and about 100,000 bookings. Loading it wipes `data\primary.db`:

```powershell
python -m app.seed --scale full --db data/primary.db
```

Every class uses the same 72-berth sleeper diagram. That is a simplification, not a real coach plan. 1A is in the class list for allocation. The demo coaches are SL, 3A and 2A.

## How a booking is protected

A ticket from stop `i` to stop `j` locks each segment in `[i, j)` inside `BEGIN IMMEDIATE`. The primary key of `seat_segment_lock` is `(run_id, seat_id, segment_idx)`. Adjacent trips can share a berth. Overlapping trips cannot. A hold is a booking in status `HELD` that already owns those rows for five minutes. There is no second hold table.

Quotas are enforced in the same check: a ladies berth requires a female passenger, a senior berth requires age 60 or more, and a broken berth or an active maintenance flag cannot be held.

Ratings are allowed only after the booking is `COMPLETED`, one rating per booking, enforced by a trigger. The displayed score is Bayesian with a prior of 10 toward the global mean, so a single 5 does not outrank a long run of 4.6s. `seat_rating_summary` stores the counts so the coach does not aggregate history for every berth. That table is the one deliberate break from third normal form. The proof is in [docs/normalisation.md](docs/normalisation.md).

## Money

Accounts are `gateway`, `escrow`, `railway_revenue`, `user_wallet` and `refund_pool`. Credits are positive, debits negative, and a ledger transaction cannot close unless its entries sum to zero.

| Event | What moves |
| --- | --- |
| Pay | Gateway debited, escrow credited. Payment is `HELD`. |
| Capture | After arrival plus 24 hours, escrow moves to railway revenue. |
| Cancel | Refund by the slab below. The rest, if any, goes to revenue. |
| Train cancelled | A dispute refunds 100 percent. |
| Dispute rejected on a future ticket | Payment returns to `HELD`. The fare stays in escrow. |

Slabs, measured before departure: 48 hours or more, 100 percent; 12 to 48, 75 percent; 4 to 12, 50 percent; under 4, 25 percent; after departure, nothing.

Ten thousand mixed holds, payments, cancellations and dispute decisions leave the unbalanced-transaction view empty.

## Replication

One primary takes writes. Three replicas pull `change_log` and apply each row idempotently, keeping the original log sequence number. Triggers that write the log are switched off while a replica is applying, so the log is not logged again.

The site sends writes to the primary and stores the returned log position in a cookie. A later read uses a replica only when that replica has reached the cookie and is within 500 log rows of the primary. Otherwise the primary answers. Booking status is always read from the primary.

Search may be a few rows behind. A confirmation never is.

Measured on this workstation, graphs in `benchmarks/results/`:

| Experiment | Result |
| --- | --- |
| 500 threads, one berth, overlapping segments | Exactly one booking |
| The same race without the primary key | More than one success |
| Segment lookup, 200,000 heap rows | 9.64 ms scanning, 0.44 ms with an index on `(run_id, segment_idx)` |
| 40 passengers, same empty coach | Random and first-free met 35 percent of preferences. L1 and L2 met 72.5 percent. |
| Kill the primary during a write burst | 5.16 seconds until the new primary accepted a booking. Lost log rows in that run: 0, because the replicas had already caught up. |
| Seat-map reads, 8 threads, 2.5 seconds | 9.6, 28.8, 53.2 and 52.8 reads per second with 0, 1, 2 and 3 replicas |

The third replica did not raise throughput further. Bookings still queue on one SQLite writer. That limit is measured on purpose. A production version would use PostgreSQL row locks and streaming replication.

## Layout

```
app/schema.sql            tables, indexes, views, triggers
app/db.py                 connections, WAL, immediate transactions
app/layout.py             72-berth coach plan
app/seed.py               demo and full catalogues
app/seed_data.py          the network and the landmarks
app/services/accounts.py  registration, password, preferences
app/services/booking.py   holds, cancel, segment locks
app/services/allocation.py  random, first-free, L1, L2, L3
app/services/scoring.py   view score, sun side, Bayesian rating
app/services/payments.py  escrow and the ledger
app/services/disputes.py  rules, then the arbitrator
app/services/ratings.py   verified ratings and the summary
app/services/waitlist.py  promotion inside the cancel transaction
app/services/swaps.py     atomic seat exchange
app/services/ticket_pdf.py one-page ticket
app/replication/          change log, puller, checksum, failover choice
app/servers/primary.py    write node
app/servers/replica.py    read node and puller
app/servers/router.py     the site, read/write split, LSN cookie
app/web/                  templates, the coach diagram, signup and account
app/cluster.py            starts primary, three replicas and the router
tests/                    correctness
benchmarks/               the four experiments
docs/                     ER diagram, normalisation, report, slides, demo script
```

SQL is raw `sqlite3`. There is no ORM. The page is server-rendered HTML and one script, with no CDN.

## Tests and experiments

```powershell
python -m pytest
python -m benchmarks.index_impact
python -m benchmarks.allocation_sim
python -m benchmarks.failover
python -m benchmarks.read_scaling
python -m benchmarks.summarise
```

The last command writes `benchmarks/results/summary.csv` with pandas. The suite covers concurrency, the naive race, segments, crash atomicity, hold expiry and waitlist promotion, payment transitions, the 10,000-row ledger, ratings, replication checksums, read-your-writes, allocation and the index plan.

## Reading

- [ER diagram](docs/er.md)
- [Normalisation](docs/normalisation.md)
- [Project report](docs/report.md), including IRCTC, Litestream, LiteFS and PostgreSQL streaming replication
- [Slides](docs/slides.md)
- [Five-minute demo](docs/demo.md)

## Limits

Landmark scores are authored, not measured. A wrong coach orientation flips the recommendation. Rate limits live in the primary process and reset when it restarts. Async replication can lose the tail of the log if a replica is behind at the moment the primary dies. The cluster file stops the old primary from waking up as a second writer.
