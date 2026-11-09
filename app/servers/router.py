"""Read/write split. Writes go to the primary. Reads wait briefly for a replica that has the caller's LSN."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from urllib.parse import quote
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth import read_session, sign_session
from app.config import CLUSTER_FILE, DB_PATH, INTERNAL, PORT, PRIMARY_URL, REPLICA_URLS
from app.db import connect
from app.replication.failover import choose_primary, eligible_replicas
from app.services.ticket_pdf import render_ticket_pdf

WEB = Path(__file__).resolve().parents[1] / "web"
templates = Jinja2Templates(directory=str(WEB / "templates"))


def _attach_decisions(disputes: list[dict]) -> None:
    """The running primary may predate the decision columns. Fill them from the log."""
    missing = [row for row in disputes if not row.get("decision_outcome")]
    if not missing:
        return
    conn = connect(DB_PATH, readonly=True)
    try:
        found = conn.execute(
            """
            SELECT dispute_id, outcome, reason, refund_pct
            FROM dispute_decision
            WHERE dispute_id IN ({})
            ORDER BY decision_id
            """.format(",".join("?" * len(missing))),
            [row["dispute_id"] for row in missing],
        ).fetchall()
    finally:
        conn.close()
    by_id = {row["dispute_id"]: row for row in found}
    for dispute in missing:
        decision = by_id.get(dispute["dispute_id"])
        if decision is None:
            continue
        dispute["decision_outcome"] = decision["outcome"]
        dispute["decision_reason"] = decision["reason"]
        dispute["decision_pct"] = decision["refund_pct"]


@dataclass
class Peer:
    url: str
    role: str = "replica"
    healthy: bool = False
    applied_lsn: int = 0
    misses: int = 0


@dataclass
class Cluster:
    primary_url: str
    replicas: dict[str, Peer]
    primary: Peer = field(init=False)
    rr: int = 0
    qps: int = 0
    fails: int = 0
    event: dict | None = None
    _failover_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self):
        self.primary = Peer(self.primary_url, role="primary")

    def snapshot(self) -> list[dict]:
        peers = [self.primary, *self.replicas.values()]
        return [
            {"url": p.url, "role": p.role, "healthy": p.healthy, "applied_lsn": p.applied_lsn}
            for p in peers
        ]

    async def refresh(self, client: httpx.AsyncClient, peer: Peer) -> bool:
        try:
            response = await client.get(peer.url + "/health", timeout=1.0)
            response.raise_for_status()
            body = response.json()
            peer.healthy = True
            peer.misses = 0
            peer.applied_lsn = int(body.get("applied_lsn") or 0)
            peer.role = body.get("role") or peer.role
            return True
        except httpx.HTTPError:
            peer.misses += 1
            peer.healthy = False
            return False

    async def choose_read(self, min_lsn: int) -> str:
        """A replica must hold the caller's LSN and sit close to the primary.

        An empty replica satisfies token 0, but it has not applied the seed yet.
        Until it is within 500 LSNs of the primary, reads stay on the primary.
        """
        deadline = time.monotonic() + 0.3
        while True:
            ready = eligible_replicas(
                [
                    {"url": p.url, "healthy": p.healthy, "applied_lsn": p.applied_lsn, "role": "replica"}
                    for p in self.replicas.values()
                ],
                min_lsn,
            )
            ceiling = self.primary.applied_lsn if self.primary.healthy else None
            fresh = [p for p in ready if ceiling is None or ceiling - p["applied_lsn"] <= 500]
            if fresh:
                self.rr = (self.rr + 1) % len(fresh)
                return fresh[self.rr]["url"]
            if time.monotonic() >= deadline:
                return self.primary_url
            await asyncio.sleep(0.05)

    async def watch(self, client: httpx.AsyncClient):
        while True:
            await self.refresh(client, self.primary)
            for peer in list(self.replicas.values()):
                await self.refresh(client, peer)
            if self.primary.misses >= 3:
                await self.failover(client)
            await asyncio.sleep(1)

    async def failover(self, client: httpx.AsyncClient):
        async with self._failover_lock:
            if self.primary.misses < 3:
                return
            candidates = [
                {"url": p.url, "healthy": p.healthy, "applied_lsn": p.applied_lsn}
                for p in self.replicas.values()
            ]
            best = choose_primary(candidates)
            if best is None:
                return
            started = time.perf_counter()
            old = self.primary_url
            old_lsn = self.primary.applied_lsn
            try:
                await client.post(best["url"] + "/control/promote", headers={"X-Internal": INTERNAL}, timeout=5)
                for peer in list(self.replicas.values()):
                    if peer.url == best["url"]:
                        continue
                    await client.post(
                        peer.url + "/control/repoint",
                        headers={"X-Internal": INTERNAL},
                        json={"primary_url": best["url"]},
                        timeout=5,
                    )
            except httpx.HTTPError:
                return
            self.replicas.pop(best["url"], None)
            self.replicas[old] = Peer(old, role="replica")
            self.primary_url = best["url"]
            self.primary = Peer(best["url"], role="primary", healthy=True, applied_lsn=best["applied_lsn"])
            gap = max(0, old_lsn - best["applied_lsn"])
            self.event = {
                "seconds": time.perf_counter() - started,
                "detail": f"Promoted {best['url']} after {old} stopped",
                "gap": gap,
            }
            CLUSTER_FILE.write_text(json.dumps({
                "primary_url": self.primary_url,
                "updated_at": time.time(),
            }), encoding="utf-8")
            self.fails += 1


def create_router() -> FastAPI:
    cluster = Cluster(PRIMARY_URL, {url: Peer(url) for url in REPLICA_URLS})

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = httpx.AsyncClient(timeout=20)
        app.state.watch = asyncio.create_task(cluster.watch(app.state.client))
        yield
        app.state.watch.cancel()
        await app.state.client.aclose()

    app = FastAPI(title="RailSeat router", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def show_validation(request: Request, exc: RequestValidationError):
        accept = request.headers.get("accept", "")
        if request.method == "GET" and "application/json" not in accept:
            return RedirectResponse(
                "/?error=" + quote("Open a train from the timetable. The coach page needs a boarding stop and an alighting stop."),
                status_code=303,
            )
        return JSONResponse({"message": "That request is missing a required field.", "detail": exc.errors()}, status_code=422)
    app.state.cluster = cluster
    app.mount("/static", StaticFiles(directory=str(WEB / "static")), name="static")

    def session(request: Request):
        return read_session(request.cookies.get("railseat"))

    def min_lsn(request: Request) -> int:
        try:
            return int(request.cookies.get("lsn") or 0)
        except ValueError:
            return 0

    async def call(request: Request, method: str, path: str, *, read: bool, json_body=None, content=None, params=None):
        cluster.qps += 1
        user = session(request)
        headers = {"X-Internal": INTERNAL}
        if user:
            headers["X-User-Id"] = str(user["user_id"])
        if json_body is None and request.headers.get("content-type"):
            headers["Content-Type"] = request.headers["content-type"]
        if request.client:
            headers["X-Forwarded-For"] = request.client.host
        token = min_lsn(request)
        if read:
            headers["X-Min-LSN"] = str(token)
            base = await cluster.choose_read(token)
        else:
            base = cluster.primary_url
        response = await app.state.client.request(
            method, base + path, headers=headers, json=json_body, content=content,
            params=request.query_params if params is None else params,
        )
        return response

    def page(request: Request, name: str, **context):
        user = session(request)
        context.setdefault("user", None)
        context["request"] = request
        context["title"] = context.get("title", "RailSeat")
        ready = eligible_replicas(
            [{"url": p.url, "healthy": p.healthy, "applied_lsn": p.applied_lsn, "role": "replica"} for p in cluster.replicas.values()],
            min_lsn(request),
        )
        if ready:
            context["health"] = f"Replica caught up to LSN {ready[0]['applied_lsn']}"
        elif cluster.primary.healthy:
            context["health"] = "Reading the primary"
        else:
            context["health"] = "Primary unreachable"
        if user and context["user"] is None:
            context["user"] = {"user_id": user["user_id"], "role": user["role"], "name": user["role"].title()}
        return templates.TemplateResponse(request, name, context)

    async def viewer(request: Request):
        user = session(request)
        if not user:
            return None
        response = await call(request, "GET", "/me", read=True)
        if response.status_code != 200:
            return {"user_id": user["user_id"], "role": user["role"], "name": "Passenger"}
        return response.json()

    @app.get("/")
    async def home(request: Request, error: str | None = None):
        user = await viewer(request)
        stations = []
        upcoming = []
        try:
            stations = (await call(request, "GET", "/stations", read=True)).json().get("stations", [])
            upcoming = (await call(request, "GET", "/highlights", read=True)).json().get("runs", [])
        except httpx.HTTPError:
            stations = stations or []
        return page(
            request, "home.html",
            title="Search",
            user=user,
            stations=stations,
            schedules=upcoming,
            upcoming=upcoming,
            origin="CSMT",
            dest="PUNE",
            when=(date.today() + timedelta(days=1)).isoformat(),
            error=error,
        )

    @app.post("/login")
    async def login(request: Request, email: str = Form(), password: str = Form()):
        response = await app.state.client.post(
            cluster.primary_url + "/auth/login",
            headers={"X-Internal": INTERNAL},
            json={"email": email, "password": password},
        )
        if response.status_code == 429:
            return RedirectResponse("/?error=Too+many+attempts.+Wait+a+minute.", status_code=303)
        if response.status_code != 200:
            return RedirectResponse("/?error=That+email+or+password+does+not+match.", status_code=303)
        body = response.json()
        target = RedirectResponse("/", status_code=303)
        target.set_cookie(
            "railseat",
            sign_session(body["user_id"], body["role"]),
            httponly=True,
            samesite="lax",
            max_age=14 * 24 * 3600,
        )
        if "x-lsn" in response.headers:
            target.set_cookie("lsn", response.headers["x-lsn"], samesite="lax")
        return target

    @app.get("/signup")
    async def signup_page(request: Request, error: str | None = None):
        if session(request):
            return RedirectResponse("/", status_code=303)
        return page(request, "signup.html", title="Create account", error=error)

    @app.post("/signup")
    async def signup(
        request: Request,
        name: str = Form(),
        email: str = Form(),
        password: str = Form(),
        age: int = Form(),
        gender: str = Form(),
    ):
        response = await app.state.client.post(
            cluster.primary_url + "/auth/register",
            headers={"X-Internal": INTERNAL},
            json={"name": name, "email": email, "password": password, "age": age, "gender": gender},
        )
        if response.status_code not in (200, 201):
            try:
                message = response.json().get("message") or "Could not create the account"
            except ValueError:
                message = "Could not create the account"
            return RedirectResponse(f"/signup?error={quote(message)}", status_code=303)
        body = response.json()
        target = RedirectResponse("/account", status_code=303)
        target.set_cookie(
            "railseat",
            sign_session(body["user_id"], body["role"]),
            httponly=True,
            samesite="lax",
            max_age=14 * 24 * 3600,
        )
        if "x-lsn" in response.headers:
            target.set_cookie("lsn", response.headers["x-lsn"], samesite="lax")
        return target

    @app.get("/account")
    async def account(request: Request, error: str | None = None, saved: str | None = None):
        if not session(request):
            return RedirectResponse("/#sign-in", status_code=303)
        profile = await viewer(request)
        if not profile or "email" not in profile:
            return RedirectResponse("/?error=Sign+in+again.", status_code=303)
        return page(request, "account.html", title="Account", user=profile, error=error, saved=saved)

    @app.post("/account/password")
    async def account_password(
        request: Request,
        current_password: str = Form(),
        new_password: str = Form(),
    ):
        if not session(request):
            return RedirectResponse("/#sign-in", status_code=303)
        response = await call(
            request, "POST", "/me/password", read=False,
            json_body={"current_password": current_password, "new_password": new_password},
        )
        if response.status_code != 200:
            try:
                message = response.json().get("message") or "Could not change the password"
            except ValueError:
                message = "Could not change the password"
            return RedirectResponse(f"/account?error={quote(message)}", status_code=303)
        target = RedirectResponse("/account?saved=Password+updated", status_code=303)
        if "x-lsn" in response.headers:
            target.set_cookie("lsn", response.headers["x-lsn"], samesite="lax")
        return target

    @app.post("/account/preferences")
    async def account_preferences(request: Request):
        if not session(request):
            return RedirectResponse("/#sign-in", status_code=303)
        form = await request.form()
        response = await call(
            request, "POST", "/me/preferences", read=False,
            json_body={
                "pref_window": 1 if form.get("pref_window") else 0,
                "pref_scenic": 1 if form.get("pref_scenic") else 0,
                "pref_shade": 1 if form.get("pref_shade") else 0,
                "pref_lower": 1 if form.get("pref_lower") else 0,
                "pref_toilet": form.get("pref_toilet") or None,
            },
        )
        if response.status_code != 200:
            try:
                message = response.json().get("message") or "Could not save preferences"
            except ValueError:
                message = "Could not save preferences"
            return RedirectResponse(f"/account?error={quote(message)}", status_code=303)
        target = RedirectResponse("/account?saved=Preferences+saved", status_code=303)
        if "x-lsn" in response.headers:
            target.set_cookie("lsn", response.headers["x-lsn"], samesite="lax")
        return target

    @app.post("/logout")
    async def logout():
        target = RedirectResponse("/", status_code=303)
        target.delete_cookie("railseat")
        return target

    @app.get("/search")
    async def search(request: Request):
        origin = request.query_params.get("from", "")
        dest = request.query_params.get("to", "")
        when = request.query_params.get("date", "")
        response = await call(request, "GET", "/trains", read=True)
        if response.status_code != 200:
            raise HTTPException(response.status_code, "Search failed")
        body = response.json()
        stations = []
        try:
            stations = (await call(request, "GET", "/stations", read=True)).json().get("stations", [])
        except httpx.HTTPError:
            stations = []
        shown = body.get("suggested_date") or body["query"]["date"]
        try:
            schedules = (await call(request, "GET", "/highlights", read=True)).json().get("runs", [])
        except httpx.HTTPError:
            schedules = []
        return page(
            request, "results.html",
            title="Trains",
            user=await viewer(request),
            query=body["query"],
            suggested=body.get("suggested_date"),
            results=body["results"],
            unknown=body.get("unknown") or [],
            same=body.get("same") or False,
            stations=stations,
            origin=body["query"]["from"],
            dest=body["query"]["to"],
            when=shown,
            schedules=schedules,
        )

    @app.get("/runs/{run_id}")
    async def seat_page(run_id: int, request: Request, from_idx: int | None = None, to_idx: int | None = None, coach: str | None = None):
        params = {"coach": coach or "S1"}
        if from_idx is not None:
            params["from_idx"] = from_idx
        if to_idx is not None:
            params["to_idx"] = to_idx
        response = await call(request, "GET", f"/runs/{run_id}/seatmap", read=True, params=params)
        if response.status_code != 200:
            raise HTTPException(response.status_code, "No such train")
        body = response.json()
        if from_idx is None or to_idx is None:
            target = f"/runs/{run_id}?from_idx={body['from']['stop_idx']}&to_idx={body['to']['stop_idx']}"
            if coach:
                target += "&coach=" + quote(coach)
            return RedirectResponse(target, status_code=303)
        names = [item["coach_no"] for item in body["coaches"]]
        current = params["coach"] if params["coach"] in names else (names[0] if names else "")
        if current and current != params["coach"]:
            params["coach"] = current
            response = await call(request, "GET", f"/runs/{run_id}/seatmap", read=True, params=params)
            body = response.json()
        seats = body["seats"]
        for seat in seats:
            score = seat["display"]
            seat["band"] = "s4" if score >= 4 else "s3" if score >= 3 else "s2" if score >= 2 else "s1" if score >= 1 else "s0"
        bays: dict[int, dict] = {}
        for seat in seats:
            index = (seat["seat_no"] - 1) // 8
            slot = "side" if (seat["seat_no"] - 1) % 8 >= 6 else "main"
            bays.setdefault(index, {"main": [], "side": []})[slot].append(seat)
        payload = {
            "run_id": run_id,
            "from_idx": from_idx,
            "to_idx": to_idx,
            "bookable": body["run"]["status"] in ("SCHEDULED", "RUNNING"),
            "signed_in": session(request) is not None,
            "sun": body["sun"],
            "coaches": body["coaches"],
            "seats": seats,
        }
        return page(
            request, "seatmap.html",
            title=body["run"]["train_name"],
            user=await viewer(request),
            run=body["run"],
            origin=body["from"],
            dest=body["to"],
            coaches=body["coaches"],
            current=current,
            scenic=body.get("scenic_side"),
            bays=[bays[i] for i in sorted(bays)],
            seats_json=json.dumps(payload).replace("<", "\\u003c"),
        )

    @app.get("/guide")
    async def guide(request: Request, run_id: int, from_idx: int, to_idx: int, seat_id: int):
        response = await call(request, "GET", f"/runs/{run_id}/journey-guide", read=True)
        if response.status_code != 200:
            raise HTTPException(404, "No guide")
        return page(request, "guide.html", title="Journey guide", user=await viewer(request), guide=response.json())

    @app.get("/ticket/{booking_id}")
    async def ticket(booking_id: int, request: Request):
        if not session(request):
            return RedirectResponse("/#sign-in", status_code=303)
        response = await call(request, "GET", f"/bookings/{booking_id}", read=False)
        if response.status_code == 403:
            raise HTTPException(403, "Not your booking")
        if response.status_code != 200:
            raise HTTPException(404, "No booking")
        return page(request, "ticket.html", title=f"Booking {booking_id}", user=await viewer(request), b=response.json())

    @app.get("/ticket/{booking_id}/pdf")
    async def ticket_pdf(booking_id: int, request: Request):
        user = session(request)
        if not user:
            return RedirectResponse("/#sign-in", status_code=303)
        response = await call(request, "GET", f"/bookings/{booking_id}", read=False)
        if response.status_code == 403:
            raise HTTPException(403, "Not your booking")
        if response.status_code != 200:
            raise HTTPException(404, "No booking")
        body = response.json()
        if not body.get("downloadable"):
            raise HTTPException(403, "The PDF is available after payment, and not after a cancellation.")
        filename = f"RailSeat-RS{booking_id:06d}.pdf"
        return Response(
            content=render_ticket_pdf(body),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/trips")
    async def trips(request: Request):
        if not session(request):
            return RedirectResponse("/", status_code=303)
        response = await call(request, "GET", "/me/trips", read=True)
        return page(request, "trips.html", title="Trips", user=await viewer(request), trips=response.json().get("trips", []))

    @app.get("/inbox")
    async def inbox(request: Request):
        if not session(request):
            return RedirectResponse("/", status_code=303)
        response = await call(request, "GET", "/me/inbox", read=True)
        swaps = []
        try:
            swaps = (await call(request, "GET", "/me/swaps", read=False)).json().get("swaps", [])
        except httpx.HTTPError:
            swaps = []
        return page(
            request, "inbox.html", title="Inbox", user=await viewer(request),
            messages=response.json().get("messages", []),
            swaps=swaps,
        )

    @app.post("/swaps")
    async def propose(request: Request):
        return await _proxy(request, "/swaps")

    @app.post("/swaps/{swap_id}/accept")
    async def accept_swap_route(swap_id: int, request: Request):
        return await _proxy(request, f"/swaps/{swap_id}/accept")

    @app.post("/uploads")
    async def uploads(request: Request):
        return await _proxy(request, "/uploads")

    @app.get("/admin")
    async def admin(request: Request):
        user = await viewer(request)
        if not user or user.get("role") != "ADMIN":
            return RedirectResponse("/", status_code=303)
        disputes = (await call(request, "GET", "/admin/disputes", read=False)).json().get("disputes", [])
        _attach_decisions(disputes)
        disputes.sort(key=lambda row: (0 if row.get("status") in ("OPEN", "UNDER_REVIEW") else 1, -int(row.get("dispute_id") or 0)))
        ledger = (await call(request, "GET", "/admin/ledger", read=False)).json()
        node_metrics = {}
        try:
            measured = await call(request, "GET", "/admin/node-metrics", read=False)
            if measured.status_code == 200:
                node_metrics = measured.json()
        except httpx.HTTPError:
            node_metrics = {}
        return page(
            request, "admin.html", title="Admin", user=user,
            peers=cluster.snapshot(), disputes=disputes, ledger=ledger, event=cluster.event,
            node_metrics=node_metrics,
            open_count=sum(1 for row in disputes if row.get("status") in ("OPEN", "UNDER_REVIEW")),
            reasons={
                "TRAIN_CANCELLED": "Train cancelled",
                "COACH_NOT_ATTACHED": "Coach not attached",
                "SEAT_UNUSABLE": "Seat unusable",
                "WRONG_DESCRIPTION": "Wrong description",
            },
            outcomes={"REFUND_FULL": "Refunded in full", "REFUND_PARTIAL": "Partial refund", "REJECTED": "Rejected"},
            statuses={
                "OPEN": "Waiting for a decision",
                "UNDER_REVIEW": "Under review",
                "AUTO_RESOLVED": "Resolved automatically",
                "RESOLVED": "Decided",
            },
        )

    @app.api_route("/bookings/{path:path}", methods=["POST"])
    async def bookings(path: str, request: Request):
        return await _proxy(request, "/bookings/" + path)

    @app.post("/disputes")
    async def disputes(request: Request):
        return await _proxy(request, "/disputes")

    @app.post("/ratings")
    async def ratings(request: Request):
        return await _proxy(request, "/ratings")

    @app.post("/waitlist")
    async def waitlist(request: Request):
        return await _proxy(request, "/waitlist")

    @app.api_route("/admin/disputes/{path:path}", methods=["POST"])
    async def decide(path: str, request: Request):
        return await _proxy(request, "/admin/disputes/" + path)

    @app.post("/admin/demo/kill-primary")
    async def kill_primary(request: Request):
        user = await viewer(request)
        if not user or user.get("role") != "ADMIN":
            return JSONResponse({"message": "Admin only"}, status_code=403)
        try:
            await app.state.client.post(
                cluster.primary_url + "/control/shutdown",
                headers={"X-Internal": INTERNAL},
                timeout=2,
            )
        except httpx.HTTPError:
            pass
        return {"ok": True}

    @app.get("/admin/metrics")
    async def metrics(request: Request):
        body = {
            "qps_seen": cluster.qps,
            "failovers": cluster.fails,
            "primary": cluster.primary_url,
            "peers": cluster.snapshot(),
            "event": cluster.event,
        }
        user = session(request)
        if user and user.get("role") == "ADMIN":
            try:
                measured = await call(request, "GET", "/admin/node-metrics", read=False)
                if measured.status_code == 200:
                    node = measured.json()
                    body["lock_waits"] = node.get("lock_waits", 0)
                    body["book_ok"] = node.get("book_ok", 0)
                    body["book_fail"] = node.get("book_fail", 0)
            except httpx.HTTPError:
                pass
        return body

    async def _proxy(request: Request, path: str):
        body = await request.body()
        try:
            response = await call(request, request.method, path, read=False, content=body or None)
        except httpx.HTTPError:
            cluster.fails += 1
            return JSONResponse({"message": "Primary unavailable"}, status_code=503)
        out = Response(content=response.content, status_code=response.status_code, media_type="application/json")
        if "x-lsn" in response.headers:
            out.headers["X-LSN"] = response.headers["x-lsn"]
            out.set_cookie("lsn", response.headers["x-lsn"], samesite="lax")
        return out

    return app


def main():
    uvicorn.run(create_router(), host="0.0.0.0", port=int(__import__("os").environ.get("ROUTER_PORT", "8080")), log_level="info")


if __name__ == "__main__":
    main()
