"""A one-page reservation slip. Core PDF fonts only, so it renders in Docker too."""

from __future__ import annotations

from datetime import datetime

from fpdf import FPDF

CLASS_NAME = {"SL": "Sleeper", "3A": "AC 3-tier", "2A": "AC 2-tier", "1A": "AC first"}
BERTH_NAME = {
    "LB": "Lower",
    "MB": "Middle",
    "UB": "Upper",
    "SL": "Side lower",
    "SU": "Side upper",
}
# Money is with the railway. A refund or a cancellation removes the slip.
PAID_STATES = frozenset({
    "HELD", "CAPTURED", "DISPUTED", "UNDER_REVIEW",
    "RESOLVED_PARTIAL", "RESOLVED_REJECTED",
})


def ticket_is_downloadable(booking: dict) -> bool:
    if booking.get("status") in ("CANCELLED", "EXPIRED"):
        return False
    payment = booking.get("payment")
    if isinstance(payment, dict):
        state = payment.get("state")
    else:
        state = booking.get("payment_state")
    return state in PAID_STATES


STATUS_NAME = {
    "HELD": "On hold",
    "CONFIRMED": "Confirmed",
    "CANCELLED": "Cancelled",
    "COMPLETED": "Completed",
    "EXPIRED": "Expired",
}


def _when(value: str | None) -> str:
    if not value:
        return "—"
    text = str(value).strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text[:19] if " " in pattern else text[:10], pattern)
        except ValueError:
            continue
        if " " in pattern:
            return parsed.strftime("%a %d %b %Y, %H:%M")
        return parsed.strftime("%a %d %b %Y")
    return text


def render_ticket_pdf(booking: dict) -> bytes:
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_compression(False)
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()
    pdf.set_title(f"RailSeat RS{int(booking['booking_id']):06d}")

    pdf.set_fill_color(28, 25, 21)
    pdf.rect(14, 14, 182, 18, style="F")
    pdf.set_text_color(247, 243, 236)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_xy(18, 18)
    pdf.cell(90, 8, "RAILSEAT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(84, 8, "Reserved berth", align="R")

    pdf.set_text_color(28, 25, 21)
    pdf.set_xy(14, 38)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(90, 5, "BOOKING NUMBER")
    pdf.cell(92, 5, "STATUS", align="R")
    pdf.set_xy(14, 43)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(90, 9, f"RS{int(booking['booking_id']):06d}")
    pdf.set_font("Helvetica", "B", 12)
    status = STATUS_NAME.get(booking["status"], booking["status"])
    if booking.get("run_status") == "CANCELLED":
        status = f"{status}  /  train cancelled"
    pdf.cell(92, 9, status, align="R")

    pdf.set_draw_color(227, 220, 210)
    pdf.line(14, 56, 196, 56)

    pdf.set_xy(14, 60)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(111, 103, 94)
    pdf.cell(0, 5, f"{booking['train_no']}    {booking['train_name']}")
    pdf.set_text_color(28, 25, 21)

    def place(x, caption, clock, day, station, code, align="L"):
        pdf.set_xy(x, 70)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(111, 103, 94)
        pdf.cell(80, 4, caption, align=align)
        pdf.set_xy(x, 75)
        pdf.set_font("Helvetica", "B", 22)
        pdf.set_text_color(28, 25, 21)
        pdf.cell(80, 10, clock or "—", align=align)
        pdf.set_xy(x, 86)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(80, 5, day or "", align=align)
        pdf.set_xy(x, 92)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(80, 5, station or "", align=align)
        pdf.set_xy(x, 98)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(111, 103, 94)
        pdf.cell(80, 5, code or "", align=align)

    origin = booking.get("origin") or {}
    dest = booking.get("dest") or {}
    place(14, "BOARDING", booking.get("board_time"), booking.get("board_date"), origin.get("name"), origin.get("code"))
    place(116, "ARRIVAL", booking.get("alight_time"), booking.get("alight_date"), dest.get("name"), dest.get("code"), "R")

    pdf.set_text_color(111, 103, 94)
    pdf.set_xy(14, 108)
    pdf.set_font("Helvetica", "", 10)
    distance = booking.get("distance_km")
    km = f"{distance:g} km" if isinstance(distance, (int, float)) else ""
    pdf.cell(182, 5, f"{booking.get('duration') or ''}    {km}".strip(), align="C")

    pdf.set_fill_color(247, 243, 236)
    pdf.rect(14, 118, 182, 28, style="F")
    headers = ("Coach", "Berth", "Class", "Quota", "Side")
    side = "Right" if booking.get("travel_side") == "R" else "Left"
    window = "window" if booking.get("is_window") else "aisle"
    values = (
        f"{booking['coach_no']}",
        f"{booking['seat_no']}  {BERTH_NAME.get(booking['berth_type'], booking['berth_type'])}",
        CLASS_NAME.get(booking["coach_class"], booking["coach_class"]),
        str(booking.get("quota") or "GENERAL").title(),
        f"{side}, {window}",
    )
    pdf.set_xy(18, 121)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(111, 103, 94)
    for header in headers:
        pdf.cell(35, 5, header)
    pdf.set_xy(18, 128)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(28, 25, 21)
    for value in values:
        pdf.cell(35, 8, value)

    pdf.set_xy(14, 154)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(111, 103, 94)
    pdf.cell(90, 5, "PASSENGER")
    pdf.cell(92, 5, "FARE", align="R")
    pdf.set_xy(14, 160)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(28, 25, 21)
    pdf.cell(90, 7, booking.get("passenger") or "Passenger")
    pdf.cell(92, 7, f"Rs {float(booking['fare']):.2f}", align="R")
    pdf.set_xy(14, 168)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(111, 103, 94)
    pdf.cell(90, 5, booking.get("email") or "")
    payment = booking.get("payment") or {}
    paid = payment.get("state") or "Not paid"
    pdf.cell(92, 5, f"Payment  {paid}", align="R")

    y = 180
    pdf.set_text_color(28, 25, 21)
    pdf.set_font("Helvetica", "", 10)
    lines = [f"Issued  {_when(booking.get('created_at'))}"]
    if booking.get("status") == "HELD" and booking.get("hold_expires_at"):
        lines.append(f"This hold ends at  {_when(booking['hold_expires_at'])}. Pay before then to keep the berth.")
    if booking.get("explain"):
        lines.append(str(booking["explain"]))
    if booking.get("run_status") == "CANCELLED":
        lines.append("The train is cancelled. A dispute on a paid ticket refunds the full fare.")
    pdf.set_xy(14, y)
    pdf.multi_cell(182, 5, "\n".join(lines))

    pdf.set_xy(14, 270)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(111, 103, 94)
    pdf.multi_cell(
        182,
        4,
        "Simulated reservation. Times follow the illustrative timetable in RailSeat. "
        "This slip is not valid on a real railway.",
    )
    return bytes(pdf.output())
