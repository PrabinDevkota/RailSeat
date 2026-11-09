-- RailSeat schema. 3NF except seat_rating_summary, which is a maintained aggregate.
-- Live writes append change_log in the same transaction (triggers installed by db.init_db).
-- Replica apply sets apply_mode.applying = 1 so those triggers do not double-write.

PRAGMA foreign_keys = ON;

CREATE TABLE apply_mode (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  applying INTEGER NOT NULL DEFAULT 0 CHECK (applying IN (0, 1))
);

CREATE TABLE replica_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  applied_lsn INTEGER NOT NULL DEFAULT 0,
  role TEXT NOT NULL DEFAULT 'replica' CHECK (role IN ('primary', 'replica'))
);

CREATE TABLE user (
  user_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('PASSENGER', 'ARBITRATOR', 'ADMIN')),
  age INTEGER NOT NULL CHECK (age BETWEEN 1 AND 120),
  gender TEXT NOT NULL CHECK (gender IN ('F', 'M', 'O')),
  pref_window INTEGER NOT NULL DEFAULT 0 CHECK (pref_window IN (0, 1)),
  pref_scenic INTEGER NOT NULL DEFAULT 0 CHECK (pref_scenic IN (0, 1)),
  pref_shade INTEGER NOT NULL DEFAULT 0 CHECK (pref_shade IN (0, 1)),
  pref_lower INTEGER NOT NULL DEFAULT 0 CHECK (pref_lower IN (0, 1)),
  pref_toilet TEXT CHECK (pref_toilet IN ('NEAR', 'FAR') OR pref_toilet IS NULL),
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE station (
  station_id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  lat REAL,
  lon REAL
);

CREATE TABLE train (
  train_id INTEGER PRIMARY KEY,
  train_no TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  route_name TEXT NOT NULL
);

CREATE TABLE route_stop (
  train_id INTEGER NOT NULL REFERENCES train(train_id),
  stop_idx INTEGER NOT NULL CHECK (stop_idx >= 1),
  station_id INTEGER NOT NULL REFERENCES station(station_id),
  arr TEXT,
  dep TEXT,
  day_offset INTEGER NOT NULL DEFAULT 0,
  distance_km REAL NOT NULL CHECK (distance_km >= 0),
  arr_min INTEGER,
  dep_min INTEGER,
  bearing_out INTEGER,
  PRIMARY KEY (train_id, stop_idx)
);

CREATE TABLE train_run (
  run_id INTEGER PRIMARY KEY,
  train_id INTEGER NOT NULL REFERENCES train(train_id),
  run_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('SCHEDULED', 'RUNNING', 'COMPLETED', 'CANCELLED')),
  coach_orientation INTEGER NOT NULL CHECK (coach_orientation IN (1, -1)),
  departure_at TEXT NOT NULL,
  UNIQUE (train_id, run_date)
);

CREATE TABLE coach (
  coach_id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES train_run(run_id),
  coach_no TEXT NOT NULL,
  coach_class TEXT NOT NULL CHECK (coach_class IN ('SL', '3A', '2A', '1A')),
  position INTEGER NOT NULL,
  UNIQUE (run_id, coach_no)
);

CREATE TABLE seat (
  seat_id INTEGER PRIMARY KEY,
  coach_id INTEGER NOT NULL REFERENCES coach(coach_id),
  seat_no INTEGER NOT NULL CHECK (seat_no BETWEEN 1 AND 72),
  berth_type TEXT NOT NULL CHECK (berth_type IN ('LB', 'MB', 'UB', 'SL', 'SU')),
  physical_side TEXT NOT NULL CHECK (physical_side IN ('L', 'R')),
  is_window INTEGER NOT NULL CHECK (is_window IN (0, 1)),
  near_toilet INTEGER NOT NULL CHECK (near_toilet IN (0, 1)),
  quota TEXT NOT NULL CHECK (quota IN ('GENERAL', 'LADIES', 'SENIOR')),
  broken INTEGER NOT NULL DEFAULT 0 CHECK (broken IN (0, 1)),
  UNIQUE (coach_id, seat_no)
);

CREATE TABLE landmark (
  landmark_id INTEGER PRIMARY KEY,
  train_id INTEGER NOT NULL REFERENCES train(train_id),
  segment_idx INTEGER NOT NULL CHECK (segment_idx >= 1),
  side TEXT NOT NULL CHECK (side IN ('L', 'R')),
  score INTEGER NOT NULL CHECK (score BETWEEN 1 AND 10),
  kind TEXT NOT NULL,
  description TEXT NOT NULL,
  time_offset_min INTEGER NOT NULL DEFAULT 0
);

-- A hold is a booking that already owns its segment rows.
CREATE TABLE booking (
  booking_id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES user(user_id),
  run_id INTEGER NOT NULL REFERENCES train_run(run_id),
  seat_id INTEGER NOT NULL REFERENCES seat(seat_id),
  from_idx INTEGER NOT NULL,
  to_idx INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('HELD', 'CONFIRMED', 'CANCELLED', 'EXPIRED', 'COMPLETED')),
  hold_expires_at TEXT,
  fare REAL NOT NULL DEFAULT 0 CHECK (fare >= 0),
  explain TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
  CHECK (from_idx < to_idx)
);

-- The double-booking guard. One row per sold segment. Conflict rolls the booking back.
CREATE TABLE seat_segment_lock (
  run_id INTEGER NOT NULL,
  seat_id INTEGER NOT NULL,
  segment_idx INTEGER NOT NULL,
  booking_id INTEGER NOT NULL REFERENCES booking(booking_id),
  PRIMARY KEY (run_id, seat_id, segment_idx)
) WITHOUT ROWID;

CREATE TABLE waitlist (
  waitlist_id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES user(user_id),
  run_id INTEGER NOT NULL REFERENCES train_run(run_id),
  from_idx INTEGER NOT NULL,
  to_idx INTEGER NOT NULL,
  coach_class TEXT NOT NULL CHECK (coach_class IN ('SL', '3A', '2A', '1A')),
  preferences TEXT,
  status TEXT NOT NULL CHECK (status IN ('WAITING', 'PROMOTED', 'CANCELLED')),
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
  promoted_booking_id INTEGER REFERENCES booking(booking_id),
  CHECK (from_idx < to_idx)
);

CREATE TABLE payment (
  payment_id INTEGER PRIMARY KEY,
  booking_id INTEGER NOT NULL REFERENCES booking(booking_id),
  user_id INTEGER NOT NULL REFERENCES user(user_id),
  amount REAL NOT NULL CHECK (amount > 0),
  state TEXT NOT NULL CHECK (state IN (
    'INITIATED', 'HELD', 'CAPTURED', 'CANCELLED_BY_USER', 'REFUNDED',
    'TRAIN_CANCELLED', 'DISPUTED', 'UNDER_REVIEW',
    'RESOLVED_REFUND', 'RESOLVED_PARTIAL', 'RESOLVED_REJECTED'
  )),
  gateway_ref TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE payment_transition (
  from_state TEXT NOT NULL,
  to_state TEXT NOT NULL,
  PRIMARY KEY (from_state, to_state)
);

CREATE TABLE refund_rule (
  rule_id INTEGER PRIMARY KEY,
  min_hours_before REAL NOT NULL,
  max_hours_before REAL,
  refund_pct REAL NOT NULL CHECK (refund_pct BETWEEN 0 AND 100)
);

CREATE TABLE ledger_txn (
  txn_id TEXT PRIMARY KEY,
  payment_id INTEGER REFERENCES payment(payment_id),
  closed INTEGER NOT NULL DEFAULT 0 CHECK (closed IN (0, 1)),
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE ledger_entries (
  entry_id INTEGER PRIMARY KEY,
  txn_id TEXT NOT NULL REFERENCES ledger_txn(txn_id),
  account TEXT NOT NULL CHECK (account IN (
    'user_wallet', 'escrow', 'railway_revenue', 'refund_pool', 'gateway'
  )),
  amount REAL NOT NULL CHECK (amount != 0),
  user_id INTEGER REFERENCES user(user_id),
  payment_id INTEGER REFERENCES payment(payment_id),
  note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE dispute (
  dispute_id INTEGER PRIMARY KEY,
  booking_id INTEGER NOT NULL REFERENCES booking(booking_id),
  user_id INTEGER NOT NULL REFERENCES user(user_id),
  payment_id INTEGER NOT NULL REFERENCES payment(payment_id),
  run_id INTEGER NOT NULL REFERENCES train_run(run_id),
  seat_id INTEGER NOT NULL REFERENCES seat(seat_id),
  reason TEXT NOT NULL CHECK (reason IN (
    'TRAIN_CANCELLED', 'COACH_NOT_ATTACHED', 'SEAT_UNUSABLE', 'WRONG_DESCRIPTION'
  )),
  detail TEXT,
  photo_path TEXT,
  status TEXT NOT NULL CHECK (status IN ('OPEN', 'AUTO_RESOLVED', 'UNDER_REVIEW', 'RESOLVED')),
  sla_due_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE dispute_decision (
  decision_id INTEGER PRIMARY KEY,
  dispute_id INTEGER NOT NULL UNIQUE REFERENCES dispute(dispute_id),
  outcome TEXT NOT NULL CHECK (outcome IN ('REFUND_FULL', 'REFUND_PARTIAL', 'REJECTED')),
  refund_pct REAL NOT NULL CHECK (refund_pct BETWEEN 0 AND 100),
  auto INTEGER NOT NULL DEFAULT 0 CHECK (auto IN (0, 1)),
  reason TEXT NOT NULL,
  arbitrator_id INTEGER REFERENCES user(user_id),
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE seat_rating (
  rating_id INTEGER PRIMARY KEY,
  booking_id INTEGER NOT NULL UNIQUE REFERENCES booking(booking_id),
  user_id INTEGER NOT NULL REFERENCES user(user_id),
  run_id INTEGER NOT NULL REFERENCES train_run(run_id),
  seat_id INTEGER NOT NULL REFERENCES seat(seat_id),
  stars INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE seat_rating_tag (
  rating_id INTEGER NOT NULL REFERENCES seat_rating(rating_id),
  tag TEXT NOT NULL CHECK (tag IN (
    'good_view', 'broken_window', 'no_socket', 'near_toilet', 'noisy', 'too_sunny'
  )),
  PRIMARY KEY (rating_id, tag)
);

-- Deliberate denormalisation: the seat map reads this instead of scanning ratings.
-- from_idx = 0 and to_idx = 0 is the seat-wide aggregate. Other keys are one journey.
CREATE TABLE seat_rating_summary (
  train_id INTEGER NOT NULL,
  coach_no TEXT NOT NULL,
  seat_no INTEGER NOT NULL,
  from_idx INTEGER NOT NULL,
  to_idx INTEGER NOT NULL,
  n INTEGER NOT NULL CHECK (n >= 0),
  sum_stars REAL NOT NULL,
  good_view INTEGER NOT NULL DEFAULT 0,
  broken_window INTEGER NOT NULL DEFAULT 0,
  no_socket INTEGER NOT NULL DEFAULT 0,
  near_toilet INTEGER NOT NULL DEFAULT 0,
  noisy INTEGER NOT NULL DEFAULT 0,
  too_sunny INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (train_id, coach_no, seat_no, from_idx, to_idx)
);

CREATE TABLE rating_global (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  n INTEGER NOT NULL,
  sum_stars REAL NOT NULL
);

CREATE TABLE maintenance_flag (
  flag_id INTEGER PRIMARY KEY,
  train_id INTEGER NOT NULL REFERENCES train(train_id),
  coach_no TEXT NOT NULL,
  seat_no INTEGER NOT NULL,
  reason TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE audit_log (
  audit_id INTEGER PRIMARY KEY,
  entity TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT,
  actor_id INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE inbox (
  message_id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES user(user_id),
  body TEXT NOT NULL,
  read_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE seat_swap (
  swap_id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES train_run(run_id),
  booking_a INTEGER NOT NULL REFERENCES booking(booking_id),
  booking_b INTEGER NOT NULL REFERENCES booking(booking_id),
  status TEXT NOT NULL CHECK (status IN ('PROPOSED', 'ACCEPTED', 'REJECTED', 'CANCELLED')),
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
  CHECK (booking_a != booking_b)
);

CREATE TABLE change_log (
  lsn INTEGER PRIMARY KEY AUTOINCREMENT,
  tbl TEXT NOT NULL,
  op TEXT NOT NULL CHECK (op IN ('I', 'U', 'D')),
  pk TEXT NOT NULL,
  payload TEXT,
  ts TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Indexes. idx_lock_run_segment is the one the index benchmark drops.
CREATE INDEX idx_lock_run_segment ON seat_segment_lock(run_id, segment_idx);
CREATE INDEX idx_lock_booking ON seat_segment_lock(booking_id);
CREATE INDEX idx_booking_user ON booking(user_id, status);
CREATE INDEX idx_booking_run ON booking(run_id, status);
CREATE INDEX idx_booking_hold ON booking(hold_expires_at) WHERE status = 'HELD';
CREATE INDEX idx_waitlist_open ON waitlist(run_id, created_at) WHERE status = 'WAITING';
CREATE INDEX idx_route_station ON route_stop(station_id);
CREATE INDEX idx_run_date ON train_run(run_date, status);
CREATE INDEX idx_run_train ON train_run(train_id, run_date);
CREATE INDEX idx_payment_booking ON payment(booking_id);
CREATE INDEX idx_ledger_txn ON ledger_entries(txn_id);
CREATE INDEX idx_ledger_payment ON ledger_entries(payment_id, account);
CREATE INDEX idx_landmark_seg ON landmark(train_id, segment_idx, side);
CREATE INDEX idx_seat_coach ON seat(coach_id, seat_no);
CREATE INDEX idx_coach_run ON coach(run_id, coach_class);
CREATE INDEX idx_dispute_status ON dispute(status, sla_due_at);
CREATE INDEX idx_rating_seat ON seat_rating(seat_id, run_id);
CREATE INDEX idx_inbox_user ON inbox(user_id, created_at);
CREATE INDEX idx_flag_active ON maintenance_flag(train_id, coach_no, seat_no) WHERE active = 1;

CREATE VIEW v_seat_availability AS
SELECT
  c.run_id,
  s.seat_id,
  c.coach_id,
  c.coach_no,
  c.coach_class,
  s.seat_no,
  s.berth_type,
  s.physical_side,
  s.is_window,
  s.near_toilet,
  s.quota,
  s.broken,
  k.segment_idx,
  k.booking_id
FROM seat s
JOIN coach c ON c.coach_id = s.coach_id
LEFT JOIN seat_segment_lock k
  ON k.run_id = c.run_id AND k.seat_id = s.seat_id;

CREATE VIEW v_seat_quality AS
SELECT
  sm.train_id,
  sm.coach_no,
  sm.seat_no,
  sm.from_idx,
  sm.to_idx,
  sm.n,
  sm.sum_stars,
  sm.good_view,
  sm.broken_window,
  sm.no_socket,
  sm.near_toilet,
  sm.noisy,
  sm.too_sunny,
  CASE
    WHEN sm.n = 0 THEN NULL
    WHEN g.n = 0 THEN (10.0 * 3.5 + sm.sum_stars) / (10.0 + sm.n)
    ELSE (10.0 * (g.sum_stars / g.n) + sm.sum_stars) / (10.0 + sm.n)
  END AS bayes
FROM seat_rating_summary sm
JOIN rating_global g ON g.id = 1;

CREATE VIEW v_replica_lag AS
SELECT
  (SELECT COALESCE(MAX(lsn), 0) FROM change_log) AS head_lsn,
  (SELECT applied_lsn FROM replica_state WHERE id = 1) AS applied_lsn,
  (SELECT COALESCE(MAX(lsn), 0) FROM change_log)
    - (SELECT applied_lsn FROM replica_state WHERE id = 1) AS lag_rows;

CREATE VIEW v_unbalanced_txns AS
SELECT txn_id, ROUND(SUM(amount), 2) AS balance
FROM ledger_entries
GROUP BY txn_id
HAVING ROUND(SUM(amount), 2) != 0;

CREATE VIEW v_account_balances AS
SELECT account, ROUND(SUM(amount), 2) AS balance
FROM ledger_entries
GROUP BY account;

-- Business rules. Gated so replica apply replays row images without firing them again.

CREATE TRIGGER trg_booking_state BEFORE UPDATE OF status ON booking
WHEN (SELECT applying FROM apply_mode WHERE id = 1) = 0
 AND NEW.status != OLD.status
BEGIN
  SELECT CASE
    WHEN OLD.status = 'HELD' AND NEW.status NOT IN ('CONFIRMED', 'EXPIRED', 'CANCELLED')
      THEN RAISE(ABORT, 'bad booking transition')
    WHEN OLD.status = 'CONFIRMED' AND NEW.status NOT IN ('CANCELLED', 'COMPLETED')
      THEN RAISE(ABORT, 'bad booking transition')
    WHEN OLD.status IN ('EXPIRED', 'CANCELLED', 'COMPLETED')
      THEN RAISE(ABORT, 'booking is terminal')
  END;
END;

CREATE TRIGGER trg_payment_transition BEFORE UPDATE OF state ON payment
WHEN (SELECT applying FROM apply_mode WHERE id = 1) = 0
 AND NEW.state != OLD.state
BEGIN
  SELECT CASE
    WHEN NOT EXISTS (
      SELECT 1 FROM payment_transition
      WHERE from_state = OLD.state AND to_state = NEW.state
    ) THEN RAISE(ABORT, 'illegal payment transition')
  END;
END;

CREATE TRIGGER trg_payment_audit AFTER UPDATE OF state ON payment
WHEN (SELECT applying FROM apply_mode WHERE id = 1) = 0
 AND NEW.state != OLD.state
BEGIN
  INSERT INTO audit_log(entity, entity_id, action, detail, actor_id)
  VALUES ('payment', NEW.payment_id, NEW.state, 'from ' || OLD.state, NEW.user_id);
END;

CREATE TRIGGER trg_ledger_closed BEFORE UPDATE OF closed ON ledger_txn
WHEN (SELECT applying FROM apply_mode WHERE id = 1) = 0
 AND NEW.closed = 1 AND OLD.closed = 0
BEGIN
  SELECT CASE
    WHEN ABS(COALESCE((SELECT SUM(amount) FROM ledger_entries WHERE txn_id = NEW.txn_id), 0)) >= 0.005
      THEN RAISE(ABORT, 'unbalanced ledger transaction')
  END;
END;

CREATE TRIGGER trg_rating_verified BEFORE INSERT ON seat_rating
WHEN (SELECT applying FROM apply_mode WHERE id = 1) = 0
BEGIN
  SELECT CASE
    WHEN (SELECT status FROM booking WHERE booking_id = NEW.booking_id) != 'COMPLETED'
      THEN RAISE(ABORT, 'rating requires completed booking')
    WHEN (SELECT user_id FROM booking WHERE booking_id = NEW.booking_id) != NEW.user_id
      THEN RAISE(ABORT, 'rating user mismatch')
    WHEN (SELECT seat_id FROM booking WHERE booking_id = NEW.booking_id) != NEW.seat_id
      THEN RAISE(ABORT, 'rating seat mismatch')
    WHEN (SELECT run_id FROM booking WHERE booking_id = NEW.booking_id) != NEW.run_id
      THEN RAISE(ABORT, 'rating run mismatch')
  END;
END;

CREATE TRIGGER trg_rating_summary AFTER INSERT ON seat_rating
WHEN (SELECT applying FROM apply_mode WHERE id = 1) = 0
BEGIN
  UPDATE rating_global
     SET n = n + 1,
         sum_stars = sum_stars + NEW.stars
   WHERE id = 1;

  INSERT INTO seat_rating_summary(
    train_id, coach_no, seat_no, from_idx, to_idx, n, sum_stars,
    good_view, broken_window, no_socket, near_toilet, noisy, too_sunny)
  SELECT tr.train_id, c.coach_no, s.seat_no, 0, 0, 1, NEW.stars, 0, 0, 0, 0, 0, 0
  FROM booking b
  JOIN seat s ON s.seat_id = b.seat_id
  JOIN coach c ON c.coach_id = s.coach_id
  JOIN train_run tr ON tr.run_id = b.run_id
  WHERE b.booking_id = NEW.booking_id
  ON CONFLICT(train_id, coach_no, seat_no, from_idx, to_idx)
  DO UPDATE SET n = n + 1, sum_stars = sum_stars + NEW.stars;

  INSERT INTO seat_rating_summary(
    train_id, coach_no, seat_no, from_idx, to_idx, n, sum_stars,
    good_view, broken_window, no_socket, near_toilet, noisy, too_sunny)
  SELECT tr.train_id, c.coach_no, s.seat_no, b.from_idx, b.to_idx, 1, NEW.stars, 0, 0, 0, 0, 0, 0
  FROM booking b
  JOIN seat s ON s.seat_id = b.seat_id
  JOIN coach c ON c.coach_id = s.coach_id
  JOIN train_run tr ON tr.run_id = b.run_id
  WHERE b.booking_id = NEW.booking_id
  ON CONFLICT(train_id, coach_no, seat_no, from_idx, to_idx)
  DO UPDATE SET n = n + 1, sum_stars = sum_stars + NEW.stars;
END;

CREATE TRIGGER trg_rating_tag AFTER INSERT ON seat_rating_tag
WHEN (SELECT applying FROM apply_mode WHERE id = 1) = 0
BEGIN
  UPDATE seat_rating_summary
  SET
    good_view = good_view + (NEW.tag = 'good_view'),
    broken_window = broken_window + (NEW.tag = 'broken_window'),
    no_socket = no_socket + (NEW.tag = 'no_socket'),
    near_toilet = near_toilet + (NEW.tag = 'near_toilet'),
    noisy = noisy + (NEW.tag = 'noisy'),
    too_sunny = too_sunny + (NEW.tag = 'too_sunny')
  WHERE train_id = (
      SELECT tr.train_id FROM seat_rating r
      JOIN train_run tr ON tr.run_id = r.run_id
      WHERE r.rating_id = NEW.rating_id
    )
    AND coach_no = (
      SELECT c.coach_no FROM seat_rating r
      JOIN seat s ON s.seat_id = r.seat_id
      JOIN coach c ON c.coach_id = s.coach_id
      WHERE r.rating_id = NEW.rating_id
    )
    AND seat_no = (
      SELECT s.seat_no FROM seat_rating r
      JOIN seat s ON s.seat_id = r.seat_id
      WHERE r.rating_id = NEW.rating_id
    );

  INSERT INTO maintenance_flag(train_id, coach_no, seat_no, reason, active)
  SELECT sm.train_id, sm.coach_no, sm.seat_no, 'repeated defect tags', 1
  FROM seat_rating_summary sm
  WHERE sm.from_idx = 0 AND sm.to_idx = 0
    AND sm.broken_window + sm.no_socket >= 3
    AND sm.train_id = (
      SELECT tr.train_id FROM seat_rating r
      JOIN train_run tr ON tr.run_id = r.run_id
      WHERE r.rating_id = NEW.rating_id
    )
    AND sm.coach_no = (
      SELECT c.coach_no FROM seat_rating r
      JOIN seat s ON s.seat_id = r.seat_id
      JOIN coach c ON c.coach_id = s.coach_id
      WHERE r.rating_id = NEW.rating_id
    )
    AND sm.seat_no = (
      SELECT s.seat_no FROM seat_rating r
      JOIN seat s ON s.seat_id = r.seat_id
      WHERE r.rating_id = NEW.rating_id
    )
    AND NOT EXISTS (
      SELECT 1 FROM maintenance_flag m
      WHERE m.train_id = sm.train_id AND m.coach_no = sm.coach_no
        AND m.seat_no = sm.seat_no AND m.active = 1
    );
END;

CREATE TRIGGER trg_dispute_audit AFTER UPDATE OF status ON dispute
WHEN (SELECT applying FROM apply_mode WHERE id = 1) = 0
 AND NEW.status != OLD.status
BEGIN
  INSERT INTO audit_log(entity, entity_id, action, detail, actor_id)
  VALUES ('dispute', NEW.dispute_id, NEW.status, NEW.reason, NEW.user_id);
END;

CREATE TRIGGER trg_audit_no_update BEFORE UPDATE ON audit_log
WHEN (SELECT applying FROM apply_mode WHERE id = 1) = 0
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON audit_log
WHEN (SELECT applying FROM apply_mode WHERE id = 1) = 0
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER trg_changelog_no_update BEFORE UPDATE ON change_log
BEGIN
  SELECT RAISE(ABORT, 'change_log is append-only');
END;

CREATE TRIGGER trg_changelog_no_delete BEFORE DELETE ON change_log
WHEN (SELECT applying FROM apply_mode WHERE id = 1) = 0
BEGIN
  SELECT RAISE(ABORT, 'change_log is append-only');
END;

INSERT INTO apply_mode(id, applying) VALUES (1, 0);
INSERT INTO replica_state(id, applied_lsn, role) VALUES (1, 0, 'replica');
INSERT INTO rating_global(id, n, sum_stars) VALUES (1, 0, 0);

INSERT INTO payment_transition(from_state, to_state) VALUES
  ('INITIATED', 'HELD'),
  ('HELD', 'CAPTURED'),
  ('HELD', 'CANCELLED_BY_USER'),
  ('HELD', 'TRAIN_CANCELLED'),
  ('HELD', 'DISPUTED'),
  ('CAPTURED', 'DISPUTED'),
  ('CAPTURED', 'TRAIN_CANCELLED'),
  ('CANCELLED_BY_USER', 'REFUNDED'),
  ('TRAIN_CANCELLED', 'REFUNDED'),
  ('DISPUTED', 'UNDER_REVIEW'),
  ('DISPUTED', 'RESOLVED_REFUND'),
  ('DISPUTED', 'RESOLVED_PARTIAL'),
  ('DISPUTED', 'RESOLVED_REJECTED'),
  ('UNDER_REVIEW', 'RESOLVED_REFUND'),
  ('UNDER_REVIEW', 'RESOLVED_PARTIAL'),
  ('UNDER_REVIEW', 'RESOLVED_REJECTED'),
  ('UNDER_REVIEW', 'HELD'),
  ('DISPUTED', 'HELD');

-- Illustrative IRCTC-style slabs. Hours are measured before departure.
INSERT INTO refund_rule(rule_id, min_hours_before, max_hours_before, refund_pct) VALUES
  (1, 48, NULL, 100),
  (2, 12, 48, 75),
  (3, 4, 12, 50),
  (4, 0, 4, 25),
  (5, -100000, 0, 0);
