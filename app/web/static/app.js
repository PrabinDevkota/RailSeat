async function postJSON(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.message || data.detail || "Request failed");
    error.payload = data;
    throw error;
  }
  return data;
}

function banner(text, bad) {
  const node = document.getElementById("flash");
  if (!node) return;
  node.hidden = false;
  node.className = bad ? "banner bad" : "banner";
  node.textContent = text;
}

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"]/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
  }[ch]));
}

function ico(name) {
  const paths = {
    window: '<rect x="2.2" y="2.2" width="11.6" height="11.6" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M8 2.4v11.2M2.4 8h11.2" stroke="currentColor" stroke-width="1.2"/>',
    side: '<path d="M3 13V3m0 5h7l-2.2-2.2M10 8l-2.2 2.2" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>',
    quota: '<circle cx="8" cy="5.2" r="2.2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M3.5 13.2c.7-2.3 2.4-3.4 4.5-3.4s3.8 1.1 4.5 3.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>',
    fare: '<path d="M4 4.5h8M4 8h8M4 11.5h5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>',
    sun: '<circle cx="8" cy="8" r="2.4" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M8 1.8v1.6M8 12.6v1.6M1.8 8h1.6M12.6 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>',
  };
  return `<svg class="ico" viewBox="0 0 16 16" aria-hidden="true">${paths[name] || ""}</svg>`;
}

function stars(score) {
  const full = Math.round(Math.max(0, Math.min(5, Number(score) || 0)));
  let html = `<span class="stars" aria-label="${full} out of 5">`;
  for (let i = 1; i <= 5; i += 1) {
    html += `<svg class="star${i <= full ? " on" : ""}" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.4 9.9 5.7l4.7.4-3.6 3.1 1.1 4.6L8 11.4 3.9 13.8l1.1-4.6L1.4 6.1l4.7-.4z"/></svg>`;
  }
  return html + "</span>";
}

function band(score) {
  if (score >= 4) return "Scenic";
  if (score >= 3) return "Good";
  if (score >= 2) return "Fair";
  if (score >= 1) return "Low";
  return "Quiet";
}

function money(value) {
  const n = Number(value);
  return "₹" + n.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function setupSeats() {
  const blob = document.getElementById("seat-data");
  if (!blob) return;
  const data = JSON.parse(blob.textContent);
  const seats = new Map(data.seats.map((seat) => [String(seat.seat_id), seat]));
  const card = document.getElementById("card");
  const placeholder = document.getElementById("placeholder");
  const fareFor = new Map(data.coaches.map((coach) => [coach.coach_no, coach.fare]));

    document.querySelectorAll(".berth").forEach((button) => {
    button.addEventListener("click", () => {
      const seat = seats.get(button.dataset.seat);
      if (!seat) return;
      document.querySelectorAll(".berth.on").forEach((node) => {
        node.classList.remove("on");
        node.setAttribute("aria-pressed", "false");
      });
      button.classList.add("on");
      button.setAttribute("aria-pressed", "true");
      placeholder.hidden = true;
      card.hidden = false;
      const blocked = button.dataset.blocked === "1";
      const names = { LB: "Lower berth", MB: "Middle berth", UB: "Upper berth", SL: "Side lower", SU: "Side upper" };
      const quota = { GENERAL: "General", LADIES: "Ladies", SENIOR: "Senior" };
      const tagNames = {
        good_view: "Good view",
        broken_window: "Broken window",
        no_socket: "No charging point",
        near_toilet: "Near the toilet",
        noisy: "Noisy",
        too_sunny: "Too sunny",
      };
      const chips = (seat.tags || []).map((tag) => `<span class="chip">${esc(tagNames[tag] || tag)}</span>`).join("");
      const display = Number(seat.display) || 0;
      const sunNote = (data.sun && data.sun.note) || "";
      card.innerHTML = `
        <p class="kicker">${esc(seat.coach_no)} · ${esc(seat.coach_class)}</p>
        <h2>Berth ${seat.seat_no} · ${esc(names[seat.berth_type] || seat.berth_type)}</h2>
        <div class="score-row">
          <p class="score">${display.toFixed(1)}</p>
          ${stars(display)}
        </div>
        <p class="quiet">${band(display)} · view ${Number(seat.view_score || 0).toFixed(0)} · ${seat.n ? Number(seat.bayes).toFixed(1) + " from " + seat.n + " ratings" : "no verified ratings yet"}</p>
        <ul class="facts">
          <li><span>${ico("side")} Side of travel</span><span>${seat.travel_side === "R" ? "Right" : "Left"}</span></li>
          <li><span>${ico("window")} Window</span><span>${seat.is_window ? "Yes" : "Aisle"}</span></li>
          <li><span>${ico("quota")} Quota</span><span>${esc(quota[seat.quota] || seat.quota)}</span></li>
          <li><span>${ico("fare")} Fare</span><span>${money(fareFor.get(seat.coach_no) || 0)}</span></li>
        </ul>
        ${chips ? `<div class="chips">${chips}</div>` : `<p class="quiet">No traveller tags yet.</p>`}
        <p class="sun-line ${seat.sun_hit ? "warn" : "quiet"}">${ico("sun")} <span>${esc(sunNote)}${seat.sun_hit ? " This berth is on that side." : ""}</span></p>
        <div class="row-actions">
          <button class="solid" type="button" id="hold" ${blocked || !data.bookable ? "disabled" : ""}>${!data.bookable ? "Not for sale" : blocked ? "Unavailable" : "Reserve this berth"}</button>
          <a class="ghost" href="/guide?run_id=${data.run_id}&from_idx=${data.from_idx}&to_idx=${data.to_idx}&seat_id=${seat.seat_id}">Journey guide</a>
        </div>`;
      const hold = document.getElementById("hold");
      if (hold && !blocked && data.bookable) {
        hold.addEventListener("click", async () => {
          if (document.body.dataset.signed !== "1") {
            banner("Sign in before reserving a berth.", true);
            window.location = "/#sign-in";
            return;
          }
          hold.disabled = true;
          try {
            const result = await postJSON("/bookings/hold", {
              run_id: data.run_id,
              seat_id: seat.seat_id,
              from_idx: data.from_idx,
              to_idx: data.to_idx,
            });
            window.location = "/ticket/" + result.booking_id;
          } catch (error) {
            banner(error.message, true);
            hold.disabled = false;
          }
        });
      }
    });
  });

  const alloc = document.getElementById("alloc");
  if (alloc) {
    alloc.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(alloc);
      const prefs = {};
      ["window", "scenic", "shade", "lower"].forEach((key) => {
        if (form.get(key)) prefs[key] = true;
      });
      if (form.get("toilet")) prefs.toilet = form.get("toilet");
      const button = alloc.querySelector("button");
      button.disabled = true;
      try {
        const result = await postJSON("/bookings/allocate", {
          run_id: data.run_id,
          from_idx: data.from_idx,
          to_idx: data.to_idx,
          coach_class: form.get("coach_class") || null,
          strategy: "L2",
          preferences: prefs,
        });
        window.location = "/ticket/" + result.booking_id;
      } catch (error) {
        banner(error.message, true);
        button.disabled = false;
      }
    });
  }

  const wait = document.getElementById("wait");
  if (wait) {
    wait.addEventListener("click", async () => {
      const note = document.getElementById("wait-status");
      const form = new FormData(alloc);
      const prefs = {};
      ["window", "scenic", "shade", "lower"].forEach((key) => {
        if (form.get(key)) prefs[key] = true;
      });
      const coachClass = form.get("coach_class") || "SL";
      wait.disabled = true;
      if (note) {
        note.hidden = false;
        note.textContent = "Joining the waitlist…";
      }
      try {
        await postJSON("/waitlist", {
          run_id: data.run_id,
          from_idx: data.from_idx,
          to_idx: data.to_idx,
          coach_class: coachClass,
          preferences: prefs,
        });
        wait.textContent = "On the waitlist";
        if (note) {
          note.textContent = "You are on the " + coachClass + " waitlist. When a berth opens, the hold appears in Messages.";
        }
      } catch (error) {
        const text = error.message || "Could not join the waitlist.";
        if (note) note.textContent = text;
        banner(text, true);
        wait.disabled = false;
      }
    });
  }
}

function setupTicket() {
  const root = document.getElementById("ticket");
  if (!root) return;
  const bookingId = root.dataset.booking;
  const pay = document.getElementById("pay");
  if (pay) {
    pay.addEventListener("click", async () => {
      pay.disabled = true;
      try {
        await postJSON("/bookings/" + bookingId + "/pay", {});
        window.location.reload();
      } catch (error) {
        banner(error.message, true);
        pay.disabled = false;
      }
    });
  }
  const cancel = document.getElementById("cancel");
  if (cancel) {
    cancel.addEventListener("click", async () => {
      if (!window.confirm("Cancel this booking? A paid ticket is refunded by the hours left before departure.")) return;
      cancel.disabled = true;
      try {
        await postJSON("/bookings/" + bookingId + "/cancel", {});
        window.location.reload();
      } catch (error) {
        banner(error.message, true);
        cancel.disabled = false;
      }
    });
  }
  const dispute = document.getElementById("dispute");
  if (dispute) {
    dispute.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(dispute);
      let photoPath = "";
      const file = form.get("photo");
      if (file && file.size) {
        const upload = new FormData();
        upload.append("file", file);
        const sent = await fetch("/uploads", { method: "POST", body: upload });
        const stored = await sent.json().catch(() => ({}));
        if (!sent.ok) {
          banner(stored.message || stored.detail || "Could not store the photo", true);
          return;
        }
        photoPath = stored.path || "";
      }
      try {
        await postJSON("/disputes", {
          booking_id: Number(bookingId),
          reason: form.get("reason"),
          detail: form.get("detail"),
          photo_path: photoPath,
        });
        window.location.reload();
      } catch (error) {
        banner(error.message, true);
      }
    });
  }
  const offer = document.getElementById("swap-offer");
  if (offer) {
    offer.addEventListener("submit", async (event) => {
      event.preventDefault();
      const raw = String(new FormData(offer).get("other_booking_id") || "").replace(/\D/g, "");
      const other = Number(raw);
      if (!other) {
        banner("Enter the other booking number.", true);
        return;
      }
      try {
        const result = await postJSON("/swaps", { booking_id: Number(bookingId), other_booking_id: other });
        banner("Offer " + result.swap_id + " sent. The other passenger accepts it in Messages.");
        offer.reset();
      } catch (error) {
        banner(error.message, true);
      }
    });
  }
  const rate = document.getElementById("rate");
  if (rate) {
    rate.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(rate);
      const tags = form.getAll("tag");
      try {
        await postJSON("/ratings", {
          booking_id: Number(bookingId),
          stars: Number(form.get("stars")),
          tags,
        });
        window.location.reload();
      } catch (error) {
        banner(error.message, true);
      }
    });
  }
  const clock = document.getElementById("clock");
  if (clock) {
    const end = new Date(clock.dataset.until.replace(" ", "T"));
    const tick = () => {
      const left = Math.max(0, end - new Date());
      const min = Math.floor(left / 60000);
      const sec = Math.floor((left % 60000) / 1000);
      clock.textContent = `${min}:${String(sec).padStart(2, "0")} left on this hold`;
      if (left === 0) clock.textContent = "Hold expired";
    };
    tick();
    setInterval(tick, 1000);
  }
}

function setupAdmin() {
  document.querySelectorAll("[data-decide]").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = button.dataset.decide;
      button.disabled = true;
      try {
        await postJSON("/admin/disputes/" + id + "/decide", {
          outcome: button.dataset.outcome,
          pct: button.dataset.pct ? Number(button.dataset.pct) : null,
          reason: button.dataset.reason,
        });
        window.location.reload();
      } catch (error) {
        banner(error.message, true);
        button.disabled = false;
      }
    });
  });
  const kill = document.getElementById("kill-primary");
  if (kill) {
    kill.addEventListener("click", async () => {
      kill.disabled = true;
      try {
        await postJSON("/admin/demo/kill-primary", {});
        banner("Primary stopped. Failover usually takes a few seconds. Refresh in a moment.");
      } catch (error) {
        banner(error.message, true);
        kill.disabled = false;
      }
    });
  }
}

function setupSearch() {
  const swap = document.getElementById("swap");
  const form = document.getElementById("find");
  if (!swap || !form) return;
  swap.addEventListener("click", () => {
    const from = form.from.value;
    form.from.value = form.to.value;
    form.to.value = from;
  });
  const schedule = document.getElementById("schedule");
  if (schedule) {
    schedule.addEventListener("change", () => {
      const parts = (schedule.value || "").split("|");
      if (parts.length !== 3 || !parts[0]) return;
      form.from.value = parts[0];
      form.to.value = parts[1];
      form.date.value = parts[2];
      paintDates();
    });
  }
  function isoDaysAhead(offset) {
    const day = new Date();
    day.setHours(12, 0, 0, 0);
    day.setDate(day.getDate() + Number(offset));
    const month = String(day.getMonth() + 1).padStart(2, "0");
    const date = String(day.getDate()).padStart(2, "0");
    return `${day.getFullYear()}-${month}-${date}`;
  }
  function paintDates() {
    form.querySelectorAll(".chip-date").forEach((button) => {
      button.classList.toggle("on", isoDaysAhead(button.dataset.offset) === form.date.value);
    });
  }
  form.querySelectorAll(".chip-date").forEach((button) => {
    button.addEventListener("click", () => {
      form.date.value = isoDaysAhead(button.dataset.offset);
      paintDates();
    });
  });
  if (form.date) {
    form.date.addEventListener("change", paintDates);
    paintDates();
  }
}

function setupPersonas() {
  document.querySelectorAll("[data-email]").forEach((button) => {
    button.addEventListener("click", () => {
      const form = document.getElementById("login");
      if (!form) return;
      form.email.value = button.dataset.email;
      form.password.value = "railseat";
    });
  });
}

function setupInbox() {
  document.querySelectorAll(".accept-swap").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await postJSON("/swaps/" + button.dataset.swap + "/accept", {});
        window.location.reload();
      } catch (error) {
        banner(error.message, true);
        button.disabled = false;
      }
    });
  });
}

setupSeats();
setupTicket();
setupAdmin();
setupPersonas();
setupSearch();
setupInbox();
