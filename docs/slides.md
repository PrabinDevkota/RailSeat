# RailSeat — slide outline

Twelve slides. One idea each. The graphs are the PNG files in `benchmarks/results/`.

## 1. Title

RailSeat. A seat-aware, replicated, escrow-backed railway booking system. Simulated passengers and payments. Real constraints.

## 2. The gap

The berth number is the product, and the side of the train is a surprise. Search is a read. Booking is a write. One SQLite file serving both is a bottleneck we should measure, not hide.

## 3. What the passenger does

Search CSMT to Pune. See the scenic side. Hold a berth. Pay into escrow. Rate the seat only after travelling. If the train is cancelled, the refund is a ledger entry.

## 4. Segment inventory

Ticket from stop i to stop j locks segments i, i+1, …, j−1. Primary key `(run_id, seat_id, segment_idx)` inside `BEGIN IMMEDIATE`. Adjacent trips share a seat. Overlapping trips cannot. A hold is a booking that already owns those rows.

## 5. View score

Landmarks have a side and a score. The coach has a left and a right. `coach_orientation = −1` swaps them. Window on the scenic side ranks above window on the quiet side, which ranks above an aisle. Three illustrative routes: Deccan, Konkan, Malnad.

## 6. Bayesian rating

Verified travellers only, one rating per completed booking, enforced by a trigger. Prior of 10 toward the global mean. One 5 does not beat forty 4.6s. The summary table is a deliberate denormalisation so the seat map does not aggregate history per berth.

## 7. Allocation experiment

Same 40 requests, same empty coach.

- Random 35 percent of preferences met, mean view 2.23
- First free 35 percent, mean view 2.34
- L1 and L2 72.5 percent, mean view 3.35

L1 and L2 tie while ratings are empty. Both beat the lottery. Show `allocation.png`.

## 8. Escrow

Double entry. Gateway to escrow at pay. Escrow to revenue at capture. Refund slabs by hours before departure. A cancelled run refunds 100 percent when the passenger raises the dispute. `v_unbalanced_txns` is empty after 10,000 mixed operations.

## 9. Replication

Triggers write `change_log` unless the node is applying. Replicas pull, upsert, and store the original LSN. Checksums match after catch-up on the demo cluster. The router holds the caller's LSN in a cookie and will not read a replica that is behind it, or more than 500 LSNs behind the primary.

## 10. Failover, measured

Primary killed during a write burst. Promotion plus a new booking: 5.16 seconds. Lost LSNs in that run: 0, because the replicas had already applied the burst. Async replication can lose the tail; this run shows the window, and also that the window can be empty when the puller is keeping up. Show `failover.png`.

## 11. The index, and the single writer

Heap lookup of a segment across 200,000 rows: 9.64 ms. The same lookup with an index on `(run_id, segment_idx)`: 0.44 ms. Show `index_impact.png`.

Read scaling, eight threads, seat-map pages, 2.5 seconds (`read_scaling.png`):

- 0 replicas: 9.6 reads/s
- 1 replica: 28.8
- 2 replicas: 53.2
- 3 replicas: 52.8

The third replica does not help once the router is busy. Bookings still queue on one writer. That is the limit we would leave behind by moving to PostgreSQL.

## 12. What this is not

Not an IRCTC integration. Not a production payment gateway. Not synchronous replication. The next system is PostgreSQL streaming replication, a real coach diagram per class, and an inventory provider at the boundary. The constraints we would keep: the segment key, the balanced ledger, the verified rating, the LSN cookie.
