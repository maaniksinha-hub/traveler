/* Traveler, local app.
 *
 * The engine lives in Python and is reached over /api/*. Nothing here
 * reimplements pricing, so there is no port to keep in sync.
 *
 * House style: plain language on the surface, the technical term available
 * one layer in. No em-dashes anywhere in user-facing copy.
 */

import { AIRPORTS_RAW } from "/airports-data.mjs";

// --------------------------------------------------------------------------
// Airport registry, reused from the file the Artifact build already generates
// --------------------------------------------------------------------------

const AIRPORTS = [];
for (const line of AIRPORTS_RAW.trim().split("\n")) {
  const [iata, country, size, city] = line.split("|");
  AIRPORTS.push({ iata, country, size, city });
}

const SIZE_RANK = { L: 0, M: 1, S: 2 };

function searchAirports(query, limit = 7) {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const byCode = [];
  const byCity = [];
  for (const a of AIRPORTS) {
    if (a.iata.toLowerCase() === q) byCode.unshift(a);
    else if (a.iata.toLowerCase().startsWith(q)) byCode.push(a);
    else if (a.city && a.city.toLowerCase().startsWith(q)) byCity.push(a);
    else if (a.city && a.city.toLowerCase().includes(q)) byCity.push(a);
    if (byCode.length + byCity.length > 400) break;
  }
  const rank = (a, b) => (SIZE_RANK[a.size] ?? 3) - (SIZE_RANK[b.size] ?? 3);
  return [...byCode, ...byCity.sort(rank)].slice(0, limit);
}

// --------------------------------------------------------------------------
// Plain language. The engine speaks in trade terms; people do not.
// The technical name is kept and shown inside the expanded detail, so the
// concept stays learnable instead of merely hidden.
// --------------------------------------------------------------------------

const TACTIC_WORDS = {
  consolidator:     ["Buy through a specialist agent", "Known in the trade as consolidator or net fares."],
  award_redemption: ["Pay with points instead of cash", "Known as an award redemption."],
  error_fare:       ["Watch for pricing mistakes", "Known as error or mistake fares."],
  open_jaw:         ["Fly home from a different city", "Known as an open-jaw or multi-city fare."],
  split_ticket:     ["Book two separate tickets", "Known as split ticketing or self-transfer."],
  positioning:      ["Start your trip from another city", "Known as a positioning flight."],
  udan_cap:         ["This route has a government price cap", "Under the UDAN regional scheme."],
  point_of_sale:    ["Book as though you were abroad", "Known as point-of-sale arbitrage."],
  off_peak_shift:   ["Fly on a quieter day", "Known as an off-peak shift."],
  tier2_airport:    ["Use a smaller airport nearby", "Sometimes called a tier-2 or alternate airport."],
  stopover_program: ["Add a free stopover on the way", "Known as a stopover programme."],
  category_student: ["Student discount", ""],
  category_senior:  ["Senior citizen discount", ""],
  category_armed_forces: ["Armed forces discount", ""],
};

const CHANNEL_WORDS = {
  airline_direct: "the airline's own website",
  ota: "a booking site",
  consolidator: "a specialist agent",
  award: "points",
};

const URGENCY_CALL = {
  book_now:       ["Book soon.", "caution-tone"],
  hold_and_watch: ["You have time. Keep an eye on it.", "caution-tone"],
  wait:           ["Wait. Prices should still come down.", "go-tone"],
  too_early:      ["It is early. Set a reminder and come back.", "go-tone"],
};

const VERDICT_CALL = {
  BUY:      ["This is a good price. Take it.", "go-tone"],
  MARGINAL: ["This price is fair, not a bargain.", "caution-tone"],
  HOLD:     ["This is too expensive. Wait.", "stop-tone"],
};

// --------------------------------------------------------------------------
// Small helpers
// --------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);

/** Escape before interpolation. Engine text is trusted today, user text is
 *  not, and one escaping rule is easier to keep right than two. */
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

const rupees = (n) => "₹" + Math.round(n).toLocaleString("en-IN");

/** The engine writes for a terminal and uses ASCII " -- " as an aside marker.
 *  It reads as a typo on a page, and the house rule rules out swapping in an
 *  em-dash, so it becomes ordinary punctuation. */
const prose = (s) => String(s ?? "").replace(/\s+--\s+/g, ", ");

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

// --------------------------------------------------------------------------
// Form wiring
// --------------------------------------------------------------------------

function wireCombo(inputId, listId) {
  const input = $(inputId);
  const list = $(listId);
  let chosen = null;
  let cursor = -1;

  const close = () => { list.classList.remove("open"); list.innerHTML = ""; cursor = -1; };

  const pick = (a) => {
    chosen = a.iata;
    input.value = `${a.city} (${a.iata})`;
    close();
  };

  const paint = () => {
    const matches = searchAirports(input.value);
    if (!matches.length) return close();
    list.innerHTML = matches.map((a, i) => `
      <div class="combo-item" role="option" data-i="${i}" aria-selected="${i === cursor}">
        <span class="combo-code">${esc(a.iata)}</span>
        <span class="combo-city">${esc(a.city || "")}</span>
        <span class="combo-country">${esc(a.country || "")}</span>
      </div>`).join("");
    list.classList.add("open");
    list.querySelectorAll(".combo-item").forEach((node, i) => {
      node.addEventListener("mousedown", (e) => { e.preventDefault(); pick(matches[i]); });
    });
  };

  input.addEventListener("input", () => { chosen = null; cursor = -1; paint(); });
  input.addEventListener("focus", () => { if (input.value) paint(); });
  input.addEventListener("blur", () => setTimeout(close, 120));
  input.addEventListener("keydown", (e) => {
    const items = list.querySelectorAll(".combo-item");
    if (!items.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      cursor = e.key === "ArrowDown"
        ? Math.min(cursor + 1, items.length - 1)
        : Math.max(cursor - 1, 0);
      items.forEach((n, i) => n.setAttribute("aria-selected", String(i === cursor)));
      items[cursor].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" && cursor >= 0) {
      e.preventDefault();
      pick(searchAirports(input.value)[cursor]);
    } else if (e.key === "Escape") {
      close();
    }
  });

  return {
    code() {
      if (chosen) return chosen;
      // Typed a bare code without picking from the list.
      const raw = input.value.trim().toUpperCase();
      const bare = raw.match(/\(([A-Z]{3})\)$/);
      if (bare) return bare[1];
      return /^[A-Z]{3}$/.test(raw) ? raw : "";
    },
    set(code) {
      const a = AIRPORTS.find((x) => x.iata === code);
      if (a) pick(a);
    },
    get input() { return input; },
  };
}

const origin = wireCombo("origin-input", "origin-list");
const dest = wireCombo("dest-input", "dest-list");

$("swap").addEventListener("click", () => {
  const a = origin.code(), b = dest.code();
  const av = origin.input.value, bv = dest.input.value;
  if (a) dest.set(a); else dest.input.value = av;
  if (b) origin.set(b); else origin.input.value = bv;
});

// Travellers and cabin
let pax = 1;
let cabin = "economy";

function paintWho() {
  const names = { economy: "economy", premium_economy: "premium", business: "business", first: "first" };
  $("pax-value").textContent = String(pax);
  $("who-summary").textContent =
    `${pax} ${pax === 1 ? "adult" : "adults"}, ${names[cabin]}`;
}
$("pax-minus").addEventListener("click", () => { pax = Math.max(1, pax - 1); paintWho(); });
$("pax-plus").addEventListener("click", () => { pax = Math.min(9, pax + 1); paintWho(); });
$("cabin-control").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  cabin = b.dataset.value;
  [...e.currentTarget.children].forEach((c) =>
    c.setAttribute("aria-pressed", String(c === b)));
  paintWho();
});

// Flexibility
const flex = {
  destinationFlex: false, originFlex: false,
  acceptsSelfTransfer: false, canBookFast: false, hardDates: false,
};
let flexDays = 0;

function paintFlex() {
  const on = Object.entries(flex).filter(([k, v]) => v && k !== "hardDates").length;
  const parts = [];
  if (flex.hardDates) parts.push("fixed dates");
  else if (flexDays) parts.push(`±${flexDays} days`);
  if (on) parts.push(`${on} more`);
  $("flex-summary").textContent = parts.length ? parts.join(", ") : "Not set";

  $("flex-days-value").textContent =
    flexDays === 0 ? "Not at all" : `${flexDays} day${flexDays === 1 ? "" : "s"}`;
  // Mirrors the engine's own validation, so an impossible combination cannot
  // be submitted rather than being silently resolved server-side.
  $("flex-slider-row").classList.toggle("disabled", flex.hardDates);
}

$("flex-chips").addEventListener("click", (e) => {
  const b = e.target.closest(".chip");
  if (!b) return;
  const key = b.dataset.flex;
  flex[key] = !flex[key];
  b.setAttribute("aria-pressed", String(flex[key]));
  paintFlex();
});
$("hard-dates-chip").addEventListener("click", (e) => {
  flex.hardDates = !flex.hardDates;
  e.currentTarget.setAttribute("aria-pressed", String(flex.hardDates));
  if (flex.hardDates) { flexDays = 0; $("flex-days").value = "0"; }
  paintFlex();
});
$("flex-days").addEventListener("input", (e) => {
  flexDays = Number(e.target.value);
  paintFlex();
});

// Wallet
const CARDS = {
  infinia: "HDFC Infinia", diners_black: "HDFC Diners Club Black",
  scapia: "Federal Bank Scapia", idfc_wow: "IDFC FIRST WoW",
  mayura: "IDFC FIRST Mayura", magnus_burgundy: "Axis Magnus Burgundy",
};
const chosenCards = new Set();
const categories = { is_student: false, is_senior: false, is_armed_forces: false, non_indian_passport: false };

const cardBox = $("card-chips");
for (const [key, label] of Object.entries(CARDS)) {
  const chip = el(`<button type="button" class="chip" aria-pressed="false">${esc(label)}</button>`);
  chip.addEventListener("click", () => {
    chosenCards.has(key) ? chosenCards.delete(key) : chosenCards.add(key);
    chip.setAttribute("aria-pressed", String(chosenCards.has(key)));
    paintWallet();
  });
  cardBox.appendChild(chip);
}

function paintWallet() {
  const cats = Object.values(categories).filter(Boolean).length;
  const bits = [];
  if (chosenCards.size) bits.push(`${chosenCards.size} card${chosenCards.size === 1 ? "" : "s"}`);
  if (cats) bits.push(`${cats} discount${cats === 1 ? "" : "s"}`);
  $("wallet-summary").textContent = bits.length ? bits.join(", ") : "None added";
}

document.querySelectorAll("[data-cat]").forEach((chip) => {
  chip.addEventListener("click", () => {
    const k = chip.dataset.cat;
    categories[k] = !categories[k];
    chip.setAttribute("aria-pressed", String(categories[k]));
    paintWallet();
  });
});

for (const id of ["fare-input", "baseline-input"]) {
  $(id).addEventListener("input", () => {
    const fare = $("fare-input").value;
    $("fare-summary").textContent = fare ? rupees(Number(fare)) : "Not yet";
  });
}

// Theme
const themeBtn = $("theme-toggle");
themeBtn.addEventListener("click", () => {
  const now = document.documentElement.getAttribute("data-theme");
  const next = now === "dark" ? "light" : now === "light" ? "dark"
    : (matchMedia("(prefers-color-scheme: dark)").matches ? "light" : "dark");
  document.documentElement.setAttribute("data-theme", next);
  $("theme-icon").textContent = next === "dark" ? "◑" : "◐";
});

paintWho(); paintFlex(); paintWallet();

// Default departure: far enough out to be actionable.
{
  const d = new Date();
  d.setDate(d.getDate() + 45);
  $("depart-input").value = d.toISOString().slice(0, 10);
  $("depart-input").min = new Date().toISOString().slice(0, 10);
}

// --------------------------------------------------------------------------
// Submit
// --------------------------------------------------------------------------

function payload() {
  const num = (id) => {
    const v = $(id).value.trim();
    return v === "" ? null : Number(v);
  };
  return {
    origin: origin.code(),
    destination: dest.code(),
    depart: $("depart-input").value,
    ret: $("return-input").value || null,
    cabin, pax,
    flex_days: flexDays,
    destination_flex: flex.destinationFlex,
    origin_flex: flex.originFlex,
    self_transfer: flex.acceptsSelfTransfer,
    book_fast: flex.canBookFast,
    hard_dates: flex.hardDates,
    fare: num("fare-input"),
    baseline: num("baseline-input"),
    cards: [...chosenCards],
    points: num("points-input") || 0,
    programmes: $("programmes-input").value,
    ...categories,
  };
}

async function post(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Something went wrong.");
  return data;
}

function showError(message) {
  const box = $("error-banner");
  box.textContent = message;
  box.hidden = false;
}

$("trip-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("error-banner").hidden = true;

  const body = payload();
  if (!body.origin || !body.destination) {
    return showError("Pick where you are flying from and to.");
  }
  if (body.origin === body.destination) {
    return showError("Those are the same airport.");
  }
  if (!body.depart) return showError("Choose the date you are leaving.");

  const go = $("go");
  try {
    go.disabled = true;
    $("go-text").textContent = "Looking";
    $("working").hidden = false;

    // Flights and the calendar do not depend on each other, so they run
    // together. The plan waits, because a real fare makes its verdict real.
    const [found, calendar] = await Promise.all([
      post("/api/flights", body),
      post("/api/calendar", body),
    ]);

    const cheapest = found.available && found.flights.length ? found.flights[0] : null;
    // A price the user typed always wins over a scraped one.
    const planBody = { ...body };
    if (planBody.fare == null && cheapest) {
      planBody.fare = cheapest.price_inr * body.pax;
    }
    const plan = await post("/api/plan", planBody);

    render({ plan, found, calendar, pax: body.pax });
  } catch (err) {
    showError(err.message);
  } finally {
    go.disabled = false;
    $("go-text").textContent = "Find me a good price";
    $("working").hidden = true;
  }
});

// --------------------------------------------------------------------------
// Render
// --------------------------------------------------------------------------

function headlineFor(plan, cheapest, pax) {
  let priceLine, sub;
  if (cheapest) {
    priceLine = rupees(cheapest.price_inr);
    const bits = [cheapest.airline_label, cheapest.duration_label, cheapest.stops_label]
      .filter(Boolean).join(", ");
    sub = pax > 1
      ? `${bits}. Per person, so ${rupees(cheapest.price_inr * pax)} for ${pax}.`
      : `${bits}. Cheapest we found right now.`;
  } else if (plan.trigger_price_inr) {
    priceLine = rupees(plan.trigger_price_inr);
    sub = "What a fair price looks like for this trip.";
  } else {
    priceLine = "No price yet";
    sub = "Add the price you have seen and we can judge it.";
  }

  let call, tone;
  const verdict = plan.fare_verdict ? plan.fare_verdict.split(".")[0].trim() : null;
  if (verdict && VERDICT_CALL[verdict]) {
    [call, tone] = VERDICT_CALL[verdict];
  } else {
    [call, tone] = URGENCY_CALL[plan.urgency] || ["Here is what we found.", ""];
  }

  // One sentence of reason, never the engine's full paragraph.
  let because = prose(plan.timing_note).split(".")[0] + ".";
  if (plan.season_note) because = prose(plan.season_note).split(".")[0] + ".";

  return `
    <div class="headline">
      <span class="price">${esc(priceLine)}</span>
      <span class="price-sub">${esc(sub)}</span>
      <p class="call ${tone}">${esc(call)}</p>
      <p class="because">${esc(because)}</p>
    </div>`;
}

function flightsPanel(found, plan, pax) {
  if (!found.available) {
    return `
      <div class="empty">
        <h3>We could not reach live prices</h3>
        <p>The flight lookup did not respond, so there is nothing real to show
          here. Everything else on this page still applies.</p>
        <p>${plan.trigger_price_inr
          ? `Aim to pay ${esc(rupees(plan.trigger_price_inr))} or less.`
          : "Add a price you have seen and we will judge it."}</p>
      </div>`;
  }
  if (!found.flights.length) {
    return `<div class="empty"><h3>No flights came back</h3>
      <p>Nothing was listed for this route on that date.</p></div>`;
  }

  const rows = found.flights.map((f, i) => `
    <div class="flight${i === 0 ? " best" : ""}">
      <div class="flight-when">
        ${i === 0 ? '<span class="tag">Cheapest</span>' : ""}
        <div class="flight-time">${esc(f.depart_hhmm || "")}${
          f.arrive_hhmm ? " to " + esc(f.arrive_hhmm) : ""
        }${f.arrives_next_day ? " <span class=\"flight-meta\">next day</span>" : ""}</div>
        <div class="flight-meta">${esc(f.airline_label)}${
          f.duration_label ? ", " + esc(f.duration_label) : ""
        }, ${esc(f.stops_label)}</div>
      </div>
      <div class="flight-price">
        <span class="amount">${esc(rupees(f.price_inr))}</span>
      </div>
    </div>`).join("");

  const pay = plan.payment;
  const payLine = pay && pay.cash_off_inr > 0
    ? `<div class="card"><strong>${esc(rupees(pay.cash_off_inr))} off</strong>
         if you book through ${esc(CHANNEL_WORDS[pay.channel] || pay.channel)}
         the way we suggest. See <em>Pay less</em> for the steps.</div>`
    : "";

  const unit = found.per_passenger && pax > 1
    ? `<p class="hint">Prices are per person. Multiply by ${pax} for the total.</p>`
    : "";

  return payLine + rows + unit;
}

function tipsPanel(plan) {
  if (!plan.tactics.length) {
    // Most levers unlock from things a first-time visitor has not filled in
    // yet, so say which ones rather than dead-ending on "nothing found".
    return `<div class="empty">
      <h3>Tell us a little more</h3>
      <p>Most ways to save depend on who you are and how much you can bend.
        Open <strong>How flexible are you</strong> and <strong>Cards and
        discounts</strong> above, then search again.</p>
      <p>Being able to move your dates by even a few days usually opens up
        the most.</p></div>`;
  }

  const tips = plan.tactics.map((t) => {
    const [title, jargon] = TACTIC_WORDS[t.key] || [t.title, ""];
    const save = t.saving
      ? `saves about ${rupees(t.saving.mid)}`
      : `about ${Math.round(t.expected_value_pct * 100)}% off`;
    const risks = t.risks.length
      ? `<ul>${t.risks.map((r) => `<li>${esc(prose(r))}</li>`).join("")}</ul>`
      : "";
    return `
      <details class="tip">
        <summary>
          <span class="tip-title">${esc(title)}</span>
          <span class="tip-save">${esc(save)}</span>
        </summary>
        <div class="tip-body">
          <p>${esc(prose(t.detail))}</p>
          ${risks}
          ${jargon ? `<p class="jargon">${esc(jargon)}</p>` : ""}
        </div>
      </details>`;
  }).join("");

  const total = plan.portfolio_saving_inr
    ? `<div class="card">Doing several of these together could save roughly
         <strong>${esc(rupees(plan.portfolio_saving_inr.mid))}</strong>.
         Some cannot be combined, so this is not the sum of the list.</div>`
    : "";

  return total + `<div class="card">${tips}</div>`;
}

function datesPanel(cal) {
  let out = "";

  if (cal.better_nearby && cal.saving_pct) {
    const d = new Date(cal.better_nearby.date + "T00:00:00");
    const pretty = d.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" });
    // A seasonal gain means the day itself is better. A gain with no seasonal
    // component comes from how far ahead you are buying, which is a different
    // thing to do about it, so it gets different words.
    const why = cal.better_reason === "season"
      ? `Around ${esc(cal.saving_pct)}% cheaper than the date you picked${
          cal.chosen && cal.chosen.peaks.length
            ? `, which falls in ${esc(cal.chosen.peaks.join(" and "))}` : ""}.`
      : `Around ${esc(cal.saving_pct)}% cheaper, because your current date is
         awkwardly close or unusually far off for this route. The day of the
         week is not the reason here.`;
    out += `
      <div class="swap-suggestion">
        <strong>Leave on ${esc(pretty)} instead.</strong>
        <p class="swap-why">${why}</p>
      </div>`;
  } else if (cal.chosen) {
    out += `<div class="card">Your date looks fine. Nothing close by is
      meaningfully cheaper.</div>`;
  }

  const months = cal.months.filter((m) => m.bookable_in_window);
  if (months.length) {
    const values = months.map((m) => m.index);
    const lo = Math.min(...values), hi = Math.max(...values);
    const best = cal.cheapest_month;
    const cells = months.map((m) => {
      const span = hi - lo || 1;
      const rel = (m.index - lo) / span;
      const cls = rel < 0.25 ? "cheap" : rel > 0.75 ? "dear" : "";
      const isBest = best && m.year === best.year && m.month === best.month;
      const [name, year] = m.label.split(" ");
      return `
        <div class="month ${cls}${isBest ? " is-best" : ""}">
          <div class="m-name">${esc(name.slice(0, 3))}</div>
          <div class="m-index">${esc(year)}</div>
          <div class="m-peak">${m.peaks.length ? esc(m.peaks[0].split(" ")[0]) : "quiet"}</div>
        </div>`;
    }).join("");

    out += `
      <h3 class="months-title">Cheapest months to fly this route</h3>
      <p class="hint months-hint">Assuming you book each one at the right time.
        Green is cheaper, red is busier.</p>
      <div class="months">${cells}</div>`;
  }

  out += `<p class="provenance">This compares seasons and how far ahead you
    book. It does not compare days of the week, because this tool has no
    day-of-week pricing data and will not guess at one. As a rule of thumb,
    Tuesday and Wednesday departures tend to be cheaper than Friday and
    Sunday.</p>`;

  return out;
}

function detailsBlock(plan) {
  const list = (items) => items.map((i) => `<li>${esc(prose(i))}</li>`).join("");
  const pay = plan.payment;

  const options = plan.options.length ? `
    <div class="detail-block">
      <h3>Worth buying time</h3>
      <ul>${plan.options.map((o) => `
        <li><strong>${o.recommended ? "Yes" : "No"}</strong>:
          ${esc(o.instrument)} at ${esc(rupees(o.cost_inr))}. ${esc(prose(o.rationale))}</li>`).join("")}</ul>
    </div>` : "";

  const payment = pay ? `
    <div class="detail-block">
      <h3>How to pay</h3>
      <ul>${list(pay.steps)}</ul>
      <div class="ledger">
        <div class="ledger-row"><span>Cash off the fare<br>
          <span class="ledger-note">real money</span></span>
          <span class="amount">${esc(rupees(pay.cash_off_inr))}</span></div>
        <div class="ledger-row"><span>Points earned<br>
          <span class="ledger-note">only worth this if you redeem well</span></span>
          <span class="amount">${esc(rupees(pay.points_value_inr))}</span></div>
        <div class="ledger-row"><span>Flexibility kept<br>
          <span class="ledger-note">not money, the value of being able to change your mind</span></span>
          <span class="amount">${esc(rupees(pay.option_value_inr))}</span></div>
      </div>
      <p class="ledger-note ledger-caveat">These are in different
        units, so they are shown separately and never added together.</p>
      ${pay.notes.length ? `<ul class="pay-notes">${list(pay.notes)}</ul>` : ""}
    </div>` : "";

  return `
    <details class="more details-block">
      <summary>
        <span class="more-label">The full detail</span>
        <span class="more-value">rights, payment, risks</span>
        <span class="chev" aria-hidden="true">›</span>
      </summary>
      <div class="more-body">
        <div class="detail-block">
          <h3>Timing</h3>
          <ul><li>${esc(prose(plan.timing_note))}</li>
            ${plan.season_note ? `<li>${esc(prose(plan.season_note))}</li>` : ""}
            ${plan.fare_verdict ? `<li>${esc(prose(plan.fare_verdict))}</li>` : ""}
            <li>${plan.days_out} days until departure.</li></ul>
        </div>
        ${options}
        ${payment}
        <div class="detail-block"><h3>Your rights</h3><ul>${list(plan.rights)}</ul></div>
        <div class="detail-block"><h3>Watch out for</h3><ul>${list(plan.risks)}</ul></div>
        <p class="provenance">Rupee figures come from hand-written estimates,
          not from measured fares, so treat the ordering as more reliable than
          the exact numbers. Reference data: ${esc(plan.snapshot_date)}.</p>
      </div>
    </details>`;
}

function render({ plan, found, calendar, pax }) {
  const cheapest = found.available && found.flights.length ? found.flights[0] : null;
  const box = $("results");

  box.innerHTML = `
    ${headlineFor(plan, cheapest, pax)}
    <div class="tabs" role="tablist">
      <button role="tab" data-panel="p-flights" aria-selected="true">Cheapest flights</button>
      <button role="tab" data-panel="p-tips" aria-selected="false">Pay less</button>
      <button role="tab" data-panel="p-dates" aria-selected="false">Better dates</button>
    </div>
    <div class="panel active" id="p-flights">${flightsPanel(found, plan, pax)}</div>
    <div class="panel" id="p-tips">${tipsPanel(plan)}</div>
    <div class="panel" id="p-dates">${datesPanel(calendar)}</div>
    ${detailsBlock(plan)}`;

  box.querySelector(".tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    box.querySelectorAll('[role="tab"]').forEach((t) =>
      t.setAttribute("aria-selected", String(t === b)));
    box.querySelectorAll(".panel").forEach((p) =>
      p.classList.toggle("active", p.id === b.dataset.panel));
  });

  box.hidden = false;

  // Staggered so the answer arrives as a sequence rather than one slab.
  // Rare, once per search, and reduced-motion flattens it to a plain fade.
  const parts = [box.querySelector(".headline"), box.querySelector(".tabs"),
                 box.querySelector(".panel.active"), box.querySelector("details.more")];
  parts.filter(Boolean).forEach((node, i) => {
    node.classList.add("reveal");
    setTimeout(() => node.classList.add("shown"), i * 40);
  });

  box.scrollIntoView({ behavior: "smooth", block: "start" });
}
