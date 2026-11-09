"""One FastAPI process. Role decides whether it accepts writes or only applies the log."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import DATA, INTERNAL
from app.db import connect, latest_lsn
from app.replication.checksum import table_checksums
from app.replication.puller import pull_loop
from app.auth import verify_password
from app.services.accounts import AuthError, change_password, register_user, update_preferences
from app.services.allocation import NoSeatAvailable, allocate
from app.services.booking import BookingError, SeatTaken, cancel_booking, hold_seat
from app.services.disputes import DisputeError, cancel_run, decide_dispute, raise_dispute
from app.services.payments import PaymentError, capture_due, ledger_is_balanced, pay_booking, wallet_balance
from app.services.ratelimit import allow_auth, check as rate_ok
from app.services.ratings import RatingError, add_rating
from app.services.readmodel import (
    booking_detail,
    highlights,
    journey_guide,
    open_swaps,
    search_trains,
    seatmap,
    stations,
    user_inbox,
    user_trips,
)
from app.services.swaps import SwapError, accept_swap, propose_swap
from app.services.waitlist import join_waitlist

log = logging.getLogger("railseat")


class Node:
    def __init__(self, db_path, role: str, node_url: str, primary_url: str):
        self.db_path = str(db_path)
        self.role = role
        self.node_url = node_url
        self.primary_url = primary_url
        self.lock = threading.Lock()
        self.worker_stop = threading.Event()
        self.metrics = {"book_ok": 0, "book_fail": 0}


class HoldIn(BaseModel):
    run_id: int
    seat_id: int
    from_idx: int
    to_idx: int
    explain: str | None = None


class AllocIn(BaseModel):
    run_id: int
    from_idx: int
    to_idx: int
    coach_class: str | None = None
    strategy: str = "L2"
    preferences: dict = Field(default_factory=dict)


class RatingIn(BaseModel):
    booking_id: int
    stars: int
    tags: list[str] = Field(default_factory=list)


class DisputeIn(BaseModel):
    booking_id: int
    reason: str
    detail: str | None = None
    photo_path: str | None = None


class DecideIn(BaseModel):
    outcome: str
    pct: float | None = None
    reason: str = ""


class WaitIn(BaseModel):
    run_id: int
    from_idx: int
    to_idx: int
    coach_class: str
    preferences: dict = Field(default_factory=dict)


class SwapIn(BaseModel):
    booking_id: int
    other_booking_id: int


class LoginIn(BaseModel):
    email: str
    password: str


class RegisterIn(BaseModel):
    name: str
    email: str
    password: str
    age: int
    gender: str


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


class PrefsIn(BaseModel):
    pref_window: int = 0
    pref_scenic: int = 0
    pref_shade: int = 0
    pref_lower: int = 0
    pref_toilet: str | None = None


class PayIn(BaseModel):
    fail: bool = False


class CancelRunIn(BaseModel):
    run_id: int


def _worker(node: Node) -> None:
    while not node.worker_stop.is_set():
        if node.role == "primary":
            try:
                with node.lock:
                    conn = connect(node.db_path)
                    try:
                        from app.services.booking import expire_holds
                        expire_holds(conn)
                        capture_due(conn)
                    finally:
                        conn.close()
            except Exception:
                log.exception("worker")
        node.worker_stop.wait(2)


def create_app(db_path, role: str = "primary", node_url: str = "http://127.0.0.1:8000",
               primary_url: str = "http://127.0.0.1:8000", background: bool = False) -> FastAPI:
    node = Node(db_path, role, node_url, primary_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.worker = None
        app.state.pull_task = None
        app.state.pull_stop = None
        if background and node.role == "primary":
            app.state.worker = threading.Thread(target=_worker, args=(node,), daemon=True)
            app.state.worker.start()
        if background and node.role != "primary":
            app.state.pull_stop = asyncio.Event()
            app.state.pull_task = asyncio.create_task(
                pull_loop(
                    node.db_path,
                    lambda: node.primary_url,
                    app.state.pull_stop,
                    node.lock,
                    lambda: node.role != "primary",
                )
            )
        yield
        node.worker_stop.set()
        if app.state.pull_stop is not None:
            app.state.pull_stop.set()
        if app.state.pull_task is not None:
            app.state.pull_task.cancel()

    app = FastAPI(title="RailSeat", version="1.0", lifespan=lifespan)
    app.state.node = node
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-LSN"],
    )

    @app.middleware("http")
    async def internal_only(request: Request, call_next):
        path = request.url.path
        if path in ("/health", "/openapi.json", "/docs", "/redoc") or path.startswith("/docs"):
            return await call_next(request)
        if request.headers.get("x-internal") != INTERNAL:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return await call_next(request)

    def fail(status: int, code: str, message: str):
        return JSONResponse({"error": code, "message": message}, status_code=status)

    @app.exception_handler(SeatTaken)
    async def seat_taken(_request, exc: SeatTaken):
        node.metrics["book_fail"] += 1
        return fail(409, "seat_taken", "That berth is already held on this journey")

    @app.exception_handler(NoSeatAvailable)
    async def none_left(_request, exc: NoSeatAvailable):
        node.metrics["book_fail"] += 1
        return fail(409, "no_seat", "No berth matches that request")

    @app.exception_handler(BookingError)
    async def booking_error(_request, exc: BookingError):
        return fail(400, "booking", str(exc))

    @app.exception_handler(PaymentError)
    async def payment_error(_request, exc: PaymentError):
        return fail(400, "payment", str(exc))

    @app.exception_handler(DisputeError)
    async def dispute_error(_request, exc: DisputeError):
        return fail(400, "dispute", str(exc))

    @app.exception_handler(RatingError)
    async def rating_error(_request, exc: RatingError):
        return fail(400, "rating", str(exc))

    @app.exception_handler(SwapError)
    async def swap_error(_request, exc: SwapError):
        return fail(400, "swap", str(exc))

    @app.exception_handler(AuthError)
    async def auth_error(_request, exc: AuthError):
        return fail(exc.status, "auth", exc.message)

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity(_request, exc: sqlite3.IntegrityError):
        text = str(exc)
        return fail(400, "rejected", text)

    @app.exception_handler(sqlite3.OperationalError)
    async def operational(_request, exc: sqlite3.OperationalError):
        return fail(400, "rejected", str(exc))

    def actor(request: Request):
        raw = request.headers.get("x-user-id") or ""
        if not raw.isdigit():
            raise HTTPException(401, "Sign in first")
        conn = read_conn()
        try:
            row = conn.execute(
                "SELECT user_id, name, email, role FROM user WHERE user_id=?",
                (int(raw),),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise HTTPException(401, "Sign in first")
        return row

    def user_id(request: Request) -> int:
        return int(actor(request)["user_id"])

    def require_role(request: Request, *roles: str):
        who = actor(request)
        if who["role"] not in roles:
            raise HTTPException(403, "Not allowed")
        return who

    def client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "local"

    def read_conn():
        return connect(node.db_path, readonly=node.role != "primary")

    def wrote(conn, body: dict, status: int = 200):
        body = dict(body)
        body["lsn"] = latest_lsn(conn)
        return JSONResponse(body, status_code=status, headers={"X-LSN": str(body["lsn"])})

    @app.get("/health")
    def health():
        conn = connect(node.db_path, readonly=node.role != "primary")
        try:
            head = latest_lsn(conn)
            applied = conn.execute("SELECT applied_lsn FROM replica_state WHERE id=1").fetchone()[0]
        finally:
            conn.close()
        if node.role == "primary":
            applied = head
        return {
            "ok": True,
            "role": node.role,
            "url": node.node_url,
            "lsn": head,
            "applied_lsn": applied,
        }

    @app.get("/replication/log")
    def replication_log(after: int = 0, limit: int = 500):
        if node.role != "primary":
            raise HTTPException(409, "not the primary")
        limit = max(1, min(limit, 2000))
        conn = connect(node.db_path)
        try:
            rows = conn.execute(
                """
                SELECT lsn, tbl, op, pk, payload, ts
                FROM change_log WHERE lsn>? ORDER BY lsn LIMIT ?
                """,
                (after, limit),
            ).fetchall()
            return {"entries": [dict(row) for row in rows]}
        finally:
            conn.close()

    @app.get("/trains")
    def trains_search(request: Request):
        origin = request.query_params.get("from", "")
        dest = request.query_params.get("to", "")
        when = request.query_params.get("date", "")
        conn = read_conn()
        try:
            return search_trains(conn, origin, dest, when)
        finally:
            conn.close()

    @app.get("/stations")
    def station_list():
        conn = read_conn()
        try:
            return {"stations": stations(conn)}
        finally:
            conn.close()

    @app.get("/highlights")
    def highlight_list():
        conn = read_conn()
        try:
            return {"runs": highlights(conn, 48)}
        finally:
            conn.close()

    @app.get("/runs/{run_id}/seatmap")
    def run_seatmap(run_id: int, from_idx: int | None = None, to_idx: int | None = None, coach: str | None = None):
        conn = read_conn()
        try:
            return seatmap(conn, run_id, from_idx, to_idx, coach)
        except KeyError:
            raise HTTPException(404, "Unknown run or stops")
        finally:
            conn.close()

    @app.get("/runs/{run_id}/journey-guide")
    def guide(run_id: int, from_idx: int, to_idx: int, seat_id: int):
        conn = read_conn()
        try:
            return journey_guide(conn, run_id, from_idx, to_idx, seat_id)
        except KeyError:
            raise HTTPException(404, "Unknown seat")
        finally:
            conn.close()

    @app.post("/auth/login")
    def login(body: LoginIn, request: Request):
        if not allow_auth(client_ip(request)):
            return fail(429, "rate_limit", "Too many attempts. Wait a minute.")
        email = body.email.strip().lower()
        conn = read_conn()
        try:
            row = conn.execute(
                "SELECT user_id, name, email, role, age, gender, password_hash FROM user WHERE email=?",
                (email,),
            ).fetchone()
        finally:
            conn.close()
        stored = row["password_hash"] if row else "not-a-real-hash"
        if row is None or not verify_password(body.password, stored):
            return fail(401, "login", "Unknown email or password")
        return {
            "user_id": row["user_id"],
            "name": row["name"],
            "email": row["email"],
            "role": row["role"],
            "age": row["age"],
            "gender": row["gender"],
        }

    @app.post("/auth/register")
    def register(body: RegisterIn, request: Request):
        if not allow_auth(client_ip(request)):
            return fail(429, "rate_limit", "Too many attempts. Wait a minute.")
        if node.role != "primary":
            raise HTTPException(409, "not the primary")
        conn = connect(node.db_path)
        try:
            created = register_user(conn, body.name, body.email, body.password, body.age, body.gender)
            return wrote(conn, created, status=201)
        except AuthError as exc:
            return fail(exc.status, "auth", exc.message)
        finally:
            conn.close()

    @app.post("/me/password")
    def password(body: PasswordIn, request: Request):
        uid = user_id(request)
        if node.role != "primary":
            raise HTTPException(409, "not the primary")
        with node.lock:
            conn = connect(node.db_path)
            try:
                change_password(conn, uid, body.current_password, body.new_password)
                return wrote(conn, {"ok": True})
            except AuthError as exc:
                return fail(exc.status, "auth", exc.message)
            finally:
                conn.close()

    @app.post("/me/preferences")
    def preferences(body: PrefsIn, request: Request):
        uid = user_id(request)
        if node.role != "primary":
            raise HTTPException(409, "not the primary")
        with node.lock:
            conn = connect(node.db_path)
            try:
                saved = update_preferences(conn, uid, body.model_dump())
                return wrote(conn, saved)
            except AuthError as exc:
                return fail(exc.status, "auth", exc.message)
            finally:
                conn.close()

    @app.get("/me")
    def me(request: Request):
        uid = user_id(request)
        conn = read_conn()
        try:
            row = conn.execute(
                "SELECT user_id, name, email, role, age, gender, pref_window, pref_scenic, pref_shade, pref_lower, pref_toilet FROM user WHERE user_id=?",
                (uid,),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "Unknown user")
            body = dict(row)
            body["wallet"] = wallet_balance(conn, uid)
            return body
        finally:
            conn.close()

    @app.get("/me/trips")
    def trips(request: Request):
        conn = read_conn()
        try:
            return {"trips": user_trips(conn, user_id(request))}
        finally:
            conn.close()

    @app.get("/me/swaps")
    def my_swaps(request: Request):
        conn = read_conn()
        try:
            return {"swaps": open_swaps(conn, user_id(request))}
        finally:
            conn.close()

    @app.get("/me/inbox")
    def inbox(request: Request):
        conn = read_conn()
        try:
            return {"messages": user_inbox(conn, user_id(request))}
        finally:
            conn.close()

    @app.post("/bookings/hold")
    def hold(body: HoldIn, request: Request):
        uid = user_id(request)
        if not rate_ok(uid, client_ip(request)):
            return fail(429, "rate_limit", "Too many hold attempts. Wait a minute and try again.")
        with node.lock:
            if node.role != "primary":
                raise HTTPException(409, "not the primary")
            conn = connect(node.db_path)
            try:
                booking_id = hold_seat(
                    conn, uid, body.run_id, body.seat_id, body.from_idx, body.to_idx,
                    body.explain or "You chose this berth on the coach diagram.",
                )
                node.metrics["book_ok"] += 1
                detail = booking_detail(conn, booking_id)
                return wrote(conn, {"booking_id": booking_id, "booking": detail})
            finally:
                conn.close()

    @app.post("/bookings/allocate")
    def alloc(body: AllocIn, request: Request):
        uid = user_id(request)
        if not rate_ok(uid, client_ip(request)):
            return fail(429, "rate_limit", "Too many allocation attempts. Wait a minute and try again.")
        if body.strategy not in ("L1", "L2", "L3", "random", "first"):
            return fail(400, "strategy", "Unknown strategy")
        with node.lock:
            if node.role != "primary":
                raise HTTPException(409, "not the primary")
            conn = connect(node.db_path)
            try:
                result = allocate(
                    conn, uid, body.run_id, body.from_idx, body.to_idx,
                    body.coach_class, body.preferences, body.strategy,
                )
                node.metrics["book_ok"] += 1
                return wrote(conn, result)
            finally:
                conn.close()

    @app.post("/bookings/{booking_id}/pay")
    def pay(booking_id: int, request: Request, fail: bool = False):
        uid = user_id(request)
        with node.lock:
            if node.role != "primary":
                raise HTTPException(409, "not the primary")
            conn = connect(node.db_path)
            try:
                result = pay_booking(conn, booking_id, uid, fail=fail)
                return wrote(conn, result)
            finally:
                conn.close()

    @app.post("/bookings/{booking_id}/cancel")
    def cancel(booking_id: int, request: Request):
        uid = user_id(request)
        with node.lock:
            if node.role != "primary":
                raise HTTPException(409, "not the primary")
            conn = connect(node.db_path)
            try:
                return wrote(conn, cancel_booking(conn, booking_id, uid))
            finally:
                conn.close()

    @app.get("/bookings/{booking_id}")
    def one_booking(booking_id: int, request: Request):
        who = actor(request)
        conn = connect(node.db_path, readonly=node.role != "primary")
        try:
            detail = booking_detail(conn, booking_id)
        finally:
            conn.close()
        if detail is None:
            raise HTTPException(404, "Unknown booking")
        if who["role"] not in ("ADMIN", "ARBITRATOR") and detail["user_id"] != who["user_id"]:
            raise HTTPException(403, "Not your booking")
        return detail

    @app.post("/waitlist")
    def wait(body: WaitIn, request: Request):
        uid = user_id(request)
        with node.lock:
            if node.role != "primary":
                raise HTTPException(409, "not the primary")
            conn = connect(node.db_path)
            try:
                wait_id = join_waitlist(conn, uid, body.run_id, body.from_idx, body.to_idx, body.coach_class, body.preferences)
                return wrote(conn, {"waitlist_id": wait_id})
            finally:
                conn.close()

    @app.post("/ratings")
    def rate(body: RatingIn, request: Request):
        uid = user_id(request)
        with node.lock:
            if node.role != "primary":
                raise HTTPException(409, "not the primary")
            conn = connect(node.db_path)
            try:
                rating_id = add_rating(conn, uid, body.booking_id, body.stars, body.tags)
                return wrote(conn, {"rating_id": rating_id})
            finally:
                conn.close()

    @app.post("/disputes")
    def dispute(body: DisputeIn, request: Request):
        uid = user_id(request)
        with node.lock:
            if node.role != "primary":
                raise HTTPException(409, "not the primary")
            conn = connect(node.db_path)
            try:
                return wrote(conn, raise_dispute(conn, uid, body.booking_id, body.reason, body.detail, body.photo_path))
            finally:
                conn.close()

    @app.get("/admin/disputes")
    def list_disputes(request: Request):
        require_role(request, "ADMIN", "ARBITRATOR")
        conn = connect(node.db_path, readonly=node.role != "primary")
        try:
            rows = conn.execute(
                """
                SELECT d.*, u.name AS passenger, p.amount, p.state AS payment_state
                FROM dispute d
                JOIN user u ON u.user_id = d.user_id
                JOIN payment p ON p.payment_id = d.payment_id
                ORDER BY d.dispute_id DESC
                LIMIT 100
                """
            ).fetchall()
            return {"disputes": [dict(row) for row in rows]}
        finally:
            conn.close()

    @app.post("/admin/disputes/{dispute_id}/decide")
    def decide(dispute_id: int, body: DecideIn, request: Request):
        uid = int(require_role(request, "ADMIN", "ARBITRATOR")["user_id"])
        with node.lock:
            if node.role != "primary":
                raise HTTPException(409, "not the primary")
            conn = connect(node.db_path)
            try:
                return wrote(conn, decide_dispute(conn, dispute_id, uid, body.outcome, body.pct, body.reason))
            finally:
                conn.close()

    @app.post("/admin/runs/{run_id}/cancel")
    def cancel_train(run_id: int, request: Request):
        uid = int(require_role(request, "ADMIN")["user_id"])
        with node.lock:
            if node.role != "primary":
                raise HTTPException(409, "not the primary")
            conn = connect(node.db_path)
            try:
                return wrote(conn, cancel_run(conn, run_id, uid))
            finally:
                conn.close()

    @app.post("/admin/bookings/{booking_id}/capture")
    def force_capture(booking_id: int, request: Request):
        require_role(request, "ADMIN")
        with node.lock:
            if node.role != "primary":
                raise HTTPException(409, "not the primary")
            conn = connect(node.db_path)
            try:
                done = capture_due(conn, force_booking_id=booking_id)
                return wrote(conn, {"captured": done})
            finally:
                conn.close()

    @app.get("/admin/ledger")
    def ledger(request: Request):
        require_role(request, "ADMIN", "ARBITRATOR")
        conn = connect(node.db_path, readonly=node.role != "primary")
        try:
            balances = [dict(row) for row in conn.execute("SELECT * FROM v_account_balances").fetchall()]
            bad = conn.execute("SELECT COUNT(*) AS n FROM v_unbalanced_txns").fetchone()["n"]
            return {"balanced": bad == 0 and ledger_is_balanced(conn), "accounts": balances, "unbalanced": bad}
        finally:
            conn.close()

    @app.get("/admin/checksum")
    def checksum(request: Request):
        require_role(request, "ADMIN")
        conn = connect(node.db_path, readonly=node.role != "primary")
        try:
            return {"tables": table_checksums(conn)}
        finally:
            conn.close()

    @app.get("/admin/node-metrics")
    def node_metrics(request: Request):
        require_role(request, "ADMIN")
        conn = connect(node.db_path, readonly=node.role != "primary")
        try:
            lag = dict(conn.execute("SELECT * FROM v_replica_lag").fetchone())
        finally:
            conn.close()
        from app.db import lock_waits
        return {**node.metrics, "lock_waits": lock_waits, "lag": lag, "role": node.role}

    @app.post("/swaps")
    def swap(body: SwapIn, request: Request):
        uid = user_id(request)
        with node.lock:
            if node.role != "primary":
                raise HTTPException(409, "not the primary")
            conn = connect(node.db_path)
            try:
                swap_id = propose_swap(conn, uid, body.booking_id, body.other_booking_id)
                return wrote(conn, {"swap_id": swap_id})
            finally:
                conn.close()

    @app.post("/swaps/{swap_id}/accept")
    def swap_accept(swap_id: int, request: Request):
        uid = user_id(request)
        with node.lock:
            if node.role != "primary":
                raise HTTPException(409, "not the primary")
            conn = connect(node.db_path)
            try:
                return wrote(conn, accept_swap(conn, uid, swap_id))
            finally:
                conn.close()

    @app.post("/uploads")
    async def upload(file: UploadFile, request: Request):
        user_id(request)
        raw = await file.read()
        if len(raw) > 2_000_000:
            return fail(400, "upload", "Photo must be under 2 MB")
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            return fail(400, "upload", "Use a JPG, PNG, WEBP, or GIF")
        folder = DATA / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{uuid.uuid4().hex[:10]}{suffix}"
        dest.write_bytes(raw)
        return {"path": str(dest)}

    @app.post("/control/promote")
    async def promote():
        node.role = "primary"
        if app.state.pull_stop is not None:
            app.state.pull_stop.set()
        if app.state.pull_task is not None:
            app.state.pull_task.cancel()
            app.state.pull_task = None
        conn = connect(node.db_path)
        try:
            conn.execute("UPDATE replica_state SET role='primary' WHERE id=1")
        finally:
            conn.close()
        node.worker_stop.clear()
        if app.state.worker is None or not app.state.worker.is_alive():
            app.state.worker = threading.Thread(target=_worker, args=(node,), daemon=True)
            app.state.worker.start()
        return {"role": "primary", "url": node.node_url}

    @app.post("/control/repoint")
    async def repoint(request: Request):
        body = await request.json()
        node.primary_url = body["primary_url"]
        return {"primary_url": node.primary_url}

    @app.post("/control/demote")
    async def demote(request: Request):
        body = await request.json()
        node.role = "replica"
        node.primary_url = body["primary_url"]
        node.worker_stop.set()
        conn = connect(node.db_path)
        try:
            conn.execute("UPDATE replica_state SET role='replica' WHERE id=1")
        finally:
            conn.close()
        app.state.pull_stop = asyncio.Event()
        app.state.pull_task = asyncio.create_task(
            pull_loop(
                node.db_path,
                lambda: node.primary_url,
                app.state.pull_stop,
                node.lock,
                lambda: node.role != "primary",
            )
        )
        return {"role": "replica", "primary_url": node.primary_url}

    @app.post("/control/shutdown")
    async def shutdown():
        threading.Timer(0.2, lambda: os._exit(0)).start()
        return {"shutting_down": True}

    return app


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--role", default=os.environ.get("RAILSEAT_ROLE", "primary"))
    parser.add_argument("--db", default=os.environ.get("RAILSEAT_DB", "data/primary.db"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--node-url", default=os.environ.get("NODE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--primary-url", default=os.environ.get("PRIMARY_URL", "http://127.0.0.1:8000"))
    args = parser.parse_args()
    from app.config import CLUSTER_FILE
    if CLUSTER_FILE.exists():
        saved = json.loads(CLUSTER_FILE.read_text(encoding="utf-8"))
        saved_primary = saved.get("primary_url")
        if saved_primary and saved_primary != args.node_url:
            args.role = "replica"
            args.primary_url = saved_primary
        elif saved_primary == args.node_url:
            args.role = "primary"
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    if not Path(args.db).exists():
        if args.role == "primary":
            from app.seed import seed
            seed(args.db, os.environ.get("SEED_SCALE", "demo"), force=True)
        else:
            from app.db import init_db
            init_db(args.db).close()
    uvicorn.run(
        create_app(args.db, args.role, args.node_url, args.primary_url, background=True),
        host="0.0.0.0",
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
