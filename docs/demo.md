# Five-minute demo

Open http://127.0.0.1:8080 after `python -m app.cluster`. Password for every account is `railseat`. Tomorrow's date is already filled in. Speak to the schema when you click, not to the CSS.

**0:00 Search.** Leave CSMT → PUNE and the date, and submit. This is the Deccan Express. Point at the free-berth counts and the fare. Mention that the search is a replica read once the replicas are within 500 LSNs of the primary; the footer of the header shows which.

**0:40 Seat map.** Open the Deccan. The scenic side for this stretch is the right. Click a green side berth (berth 7 in S1 is the window on that side) and read the view score, the travel side and the fare. If the rake were reversed, the same physical wall would be the other side of travel. The colour is the blend of view and Bayesian rating.

**1:20 Hold and escrow.** Sign in as Asha if you are not already. Hold the berth. On the ticket, pay. The ledger rows are gateway negative and escrow positive, and they sum to zero. The hold lasts five minutes if you do not pay; a worker then frees the segments and can promote the waitlist.

**2:00 Kill a replica.** From another shell, stop the process on port 8001. Refresh the search. Reads continue on the other replicas or on the primary. The admin page, signed in as Meera, lists who is up and the applied LSN.

**2:30 Kill the primary.** On the admin page, press "Stop the primary". Wait until the health line moves. The router promotes the replica with the highest applied LSN after three missed heartbeats and writes `data/cluster.json`. Show a new search still answering. Say plainly that any LSN the dead primary had not shipped is gone; the last measured burst lost none because the pullers were caught up, and a paused replica would lose the tail.

**3:20 Cancelled train.** Sign in as Asha. Open Trips and the Konkan ticket whose run is cancelled. The payment is still in escrow. Raise the dispute with reason "Train cancelled". The rule engine refunds 100 percent, the booking is cancelled, and the decision is automatic. No arbitrator clicks.

**4:10 Evidence, not a live 500-thread run.** Switch to the allocation graph `benchmarks/results/allocation.png`: random and first-free matched 35 percent of preferences, L1 and L2 matched 72.5 percent on the same 40 requests. If there is time, the index graph: 9.64 ms down to 0.44 ms on 200,000 heap rows once `(run_id, segment_idx)` is indexed. Mention the pytest that runs 500 threads and keeps a single winner.

**4:40 Stop.** The constraints are the product: the segment key, the balanced ledger, the LSN cookie. The map is how a passenger sees them.
