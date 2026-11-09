from app.services.booking import hold_seat
from app.services.readmodel import booking_detail
from app.services.ticket_pdf import render_ticket_pdf, ticket_is_downloadable
from tests.world import make_world


def test_every_status_renders_a_pdf_with_the_timetable_clocks(tmp_path):
    conn = make_world(tmp_path)
    booking_id = hold_seat(conn, 1, 1, 7, 1, 4)
    detail = booking_detail(conn, booking_id)
    assert detail["board_time"] == "08:00"
    assert detail["alight_time"] == "09:00"
    assert "h" in detail["duration"]

    for status in ("HELD", "CONFIRMED", "CANCELLED", "COMPLETED", "EXPIRED"):
        detail["status"] = status
        pdf = render_ticket_pdf(detail)
        assert pdf.startswith(b"%PDF")
        assert b"08:00" in pdf
        assert b"09:00" in pdf
        text = pdf.decode("latin-1", errors="ignore")
        assert f"RS{booking_id:06d}" in text
        assert "Asha" in text


def test_pdf_is_only_for_a_paid_ticket_that_was_not_cancelled():
    unpaid = {"status": "HELD", "payment": None}
    paid = {"status": "CONFIRMED", "payment": {"state": "HELD"}}
    captured = {"status": "COMPLETED", "payment": {"state": "CAPTURED"}}
    cancelled = {"status": "CANCELLED", "payment": {"state": "REFUNDED"}}
    refunded = {"status": "CONFIRMED", "payment_state": "REFUNDED"}
    assert ticket_is_downloadable(unpaid) is False
    assert ticket_is_downloadable(paid) is True
    assert ticket_is_downloadable(captured) is True
    assert ticket_is_downloadable(cancelled) is False
    assert ticket_is_downloadable(refunded) is False
