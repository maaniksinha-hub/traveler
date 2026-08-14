// UI layer. Everything the engine needs (REGISTRY, makeTrip, planTrip, ...)
// is in scope because this file is concatenated directly after traveler.mjs
// into one inline module script -- see PORT_NOTES.md.

// ---------------------------------------------------------------------------
// Airport combobox
// ---------------------------------------------------------------------------

const ALL_AIRPORTS = [...REGISTRY.values()];

function searchAirports(query, limit = 8) {
  const q = query.trim().toUpperCase();
  if (q.length === 0) return [];
  const codeMatches = [];
  const cityMatches = [];
  for (const a of ALL_AIRPORTS) {
    if (a.iata.startsWith(q)) codeMatches.push(a);
    else if (a.city && a.city.toUpperCase().includes(q)) cityMatches.push(a);
    if (codeMatches.length >= limit) break;
  }
  return [...codeMatches, ...cityMatches].slice(0, limit);
}

function wireCombo(inputId, listId) {
  const input = document.getElementById(inputId);
  const list = document.getElementById(listId);
  let selectedCode = "";

  function render(matches) {
    list.innerHTML = "";
    for (const a of matches) {
      const item = document.createElement("div");
      item.className = "combo-item";
      item.innerHTML = `<span class="code">${a.iata}</span><span class="city">${a.city || "-"}, ${a.country}</span>`;
      item.addEventListener("mousedown", (e) => {
        e.preventDefault();
        input.value = `${a.iata} - ${a.city || a.iata}`;
        selectedCode = a.iata;
        list.classList.remove("open");
      });
      list.appendChild(item);
    }
    list.classList.toggle("open", matches.length > 0);
  }

  input.addEventListener("input", () => {
    selectedCode = "";
    render(searchAirports(input.value));
  });
  input.addEventListener("focus", () => { if (input.value) render(searchAirports(input.value)); });
  input.addEventListener("blur", () => { setTimeout(() => list.classList.remove("open"), 120); });

  return {
    code() {
      if (selectedCode) return selectedCode;
      const raw = input.value.trim().toUpperCase();
      // Power-user path: typed a bare 3-letter code without picking from the list.
      const bare = raw.split(/[\s-]/)[0];
      return bare;
    },
  };
}

const originCombo = wireCombo("origin-input", "origin-list");
const destCombo = wireCombo("dest-input", "dest-list");

// ---------------------------------------------------------------------------
// Cabin segmented control
// ---------------------------------------------------------------------------

let cabinValue = "economy";
const cabinControl = document.getElementById("cabin-control");
cabinControl.addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  for (const b of cabinControl.querySelectorAll("button")) b.setAttribute("aria-pressed", "false");
  btn.setAttribute("aria-pressed", "true");
  cabinValue = btn.dataset.value;
});

// ---------------------------------------------------------------------------
// Passenger stepper
// ---------------------------------------------------------------------------

let paxValue = 1;
const paxOutput = document.getElementById("pax-value");
document.getElementById("pax-minus").addEventListener("click", () => {
  paxValue = Math.max(1, paxValue - 1);
  paxOutput.textContent = String(paxValue);
});
document.getElementById("pax-plus").addEventListener("click", () => {
  paxValue = Math.min(9, paxValue + 1);
  paxOutput.textContent = String(paxValue);
});

// ---------------------------------------------------------------------------
// Flexibility chips. Only date_flex_days is mutually exclusive with
// hard_dates in the engine -- mirrored exactly, not over-restricted.
// ---------------------------------------------------------------------------

const flexState = {
  destinationFlex: false, originFlex: false, acceptsSelfTransfer: false,
  canBookFast: false, hardDates: false,
};
const flexSliderRow = document.getElementById("flex-slider-row");
const flexDaysInput = document.getElementById("flex-days");
const flexDaysValue = document.getElementById("flex-days-value");

document.getElementById("flex-chips").addEventListener("click", (e) => {
  const btn = e.target.closest(".chip");
  if (!btn) return;
  const key = btn.dataset.flex;
  const next = btn.getAttribute("aria-pressed") !== "true";
  btn.setAttribute("aria-pressed", String(next));
  flexState[key] = next;

  if (key === "hardDates") {
    flexSliderRow.classList.toggle("disabled", next);
    if (next) {
      flexDaysInput.value = "0";
      flexDaysValue.textContent = "0";
    }
  }
});

flexDaysInput.addEventListener("input", () => {
  flexDaysValue.textContent = flexDaysInput.value;
});

// ---------------------------------------------------------------------------
// Wallet: cards, category chips
// ---------------------------------------------------------------------------

const selectedCards = new Set();
const cardChipContainer = document.getElementById("card-chips");
for (const [key, card] of Object.entries(CARD_CATALOGUE)) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "chip";
  chip.dataset.card = key;
  chip.setAttribute("aria-pressed", "false");
  chip.textContent = card.name;
  chip.addEventListener("click", () => {
    const next = chip.getAttribute("aria-pressed") !== "true";
    chip.setAttribute("aria-pressed", String(next));
    if (next) selectedCards.add(key); else selectedCards.delete(key);
  });
  cardChipContainer.appendChild(chip);
}

const categoryState = { isStudent: false, isSenior: false, isArmedForces: false, nonIndianPassport: false };
document.querySelectorAll(".chip[data-cat]").forEach((chip) => {
  chip.addEventListener("click", () => {
    const key = chip.dataset.cat;
    const next = chip.getAttribute("aria-pressed") !== "true";
    chip.setAttribute("aria-pressed", String(next));
    categoryState[key] = next;
  });
});

let foreignPos = false;
document.getElementById("foreign-pos-chip").addEventListener("click", (e) => {
  const next = e.currentTarget.getAttribute("aria-pressed") !== "true";
  e.currentTarget.setAttribute("aria-pressed", String(next));
  foreignPos = next;
});

const decisionProbInput = document.getElementById("decision-prob-input");
const decisionProbValue = document.getElementById("decision-prob-value");
decisionProbInput.addEventListener("input", () => {
  decisionProbValue.textContent = `${decisionProbInput.value}%`;
});

// ---------------------------------------------------------------------------
// Rendering helpers
// ---------------------------------------------------------------------------

function urgencyBadgeClass(u) {
  return u === "book_now" ? "caution" : "neutral";
}
function urgencyLabel(u) {
  return { book_now: "Book now", hold_and_watch: "Hold & watch", wait: "Wait", too_early: "Too early" }[u] || u;
}
function verdictBadgeClass(text) {
  if (text.startsWith("BUY")) return "go";
  if (text.startsWith("MARGINAL")) return "caution";
  return "stop";
}
function verdictWord(text) {
  return text.split(".")[0];
}
function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function renderResults(plan, fare) {
  const root = document.getElementById("results");
  root.innerHTML = "";

  // Provenance -- always visible, never dropped for a cleaner look.
  root.appendChild(el(`
    <div class="provenance">
      <span class="dot"></span>
      <span>Uncalibrated priors. Rankings are more reliable than the rupee figures. ${MODEL_PROVENANCE}</span>
    </div>
  `));

  // --- Timing ---------------------------------------------------------
  const timingBlock = el(`<div class="result-block"><h2>Timing</h2></div>`);
  timingBlock.appendChild(el(`
    <span class="verdict-badge ${urgencyBadgeClass(plan.urgency)}">${urgencyLabel(plan.urgency)}</span>
  `));
  timingBlock.appendChild(el(`<p style="margin: 10px 0 0; font-size: 13px; color: var(--ink-soft);">${plan.timingNote}</p>`));
  if (plan.seasonNote) {
    timingBlock.appendChild(el(`<p style="margin: 8px 0 0; font-size: 13px; color: var(--caution);">${plan.seasonNote}</p>`));
  }
  if (plan.triggerPriceInr != null) {
    timingBlock.appendChild(el(`
      <p class="trigger-line">Trigger price: book without deliberation at or below
        <span class="price">₹${fmt(plan.triggerPriceInr)}</span>.</p>
    `));
  }
  if (plan.fareVerdict) {
    const word = verdictWord(plan.fareVerdict);
    const cls = verdictBadgeClass(plan.fareVerdict);
    const rest = plan.fareVerdict.slice(word.length + 1).trim();
    timingBlock.appendChild(el(`
      <p class="trigger-line">
        <span class="verdict-badge ${cls}" style="margin-right: 8px;">${word}</span>${rest}
      </p>
    `));
  }
  root.appendChild(timingBlock);

  // --- Tactics ----------------------------------------------------------
  const tacticsBlock = el(`<div class="result-block"><h2>Tactics</h2></div>`);
  const ranked = headlineTactics(plan);
  if (ranked.length === 0) {
    tacticsBlock.appendChild(el(`<p style="font-size:13px;color:var(--ink-faint);margin:0;">No tactics unlocked for this trip. Add flexibility or wallet detail.</p>`));
  } else {
    for (const t of ranked) {
      const saving = tacticSaving(t, fare);
      const row = el(`
        <div class="tactic">
          <div class="tactic-head">
            <span class="tactic-title">${t.title}${t.exclusivityGroup ? `<span class="tactic-group">${t.exclusivityGroup}</span>` : ""}</span>
            <span class="tactic-saving">${saving ? "₹" + estStr(saving) : (t.expectedValuePct * 100).toFixed(1) + "% EV"}</span>
          </div>
          <p class="tactic-detail">${t.detail}</p>
        </div>
      `);
      if (t.risks.length) {
        const ul = document.createElement("ul");
        ul.className = "tactic-risks";
        for (const r of t.risks) ul.appendChild(el(`<li>${r}</li>`));
        row.appendChild(ul);
      }
      tacticsBlock.appendChild(row);
    }
    const portfolio = portfolioEstimate(plan.tactics, fare);
    if (portfolio) {
      tacticsBlock.appendChild(el(`
        <p class="portfolio-line">Combined realistic saving: <span class="amt">₹${estStr(portfolio)}</span>.
        Tactics sharing an exclusivity group count only once: one ticket, one channel.</p>
      `));
    }
  }
  root.appendChild(tacticsBlock);

  // --- Optionality --------------------------------------------------------
  if (plan.options.length) {
    const optBlock = el(`<div class="result-block"><h2>Optionality</h2></div>`);
    for (const o of plan.options) {
      optBlock.appendChild(el(`
        <div class="option-row">
          <span class="option-tag ${o.recommended ? "buy" : "skip"}">${o.recommended ? "Buy" : "Skip"}</span>
          <div class="option-body">
            <div class="instrument">${o.instrument} <span class="num" style="color:var(--ink-faint);font-weight:400;">- ₹${fmt(o.costInr)}</span></div>
            <div class="rationale">${o.rationale}</div>
          </div>
        </div>
      `));
    }
    root.appendChild(optBlock);
  }

  // --- Payment --------------------------------------------------------
  if (plan.payment) {
    const pay = plan.payment;
    const payBlock = el(`<div class="result-block"><h2>Payment: ${pay.channel.replace("_", " ")}</h2></div>`);
    if (pay.steps.length) {
      const ul = document.createElement("ul");
      ul.className = "steps-list";
      for (const s of pay.steps) ul.appendChild(el(`<li>${s}</li>`));
      payBlock.appendChild(ul);
    }
    const netOption = pay.optionValueInr - pay.forfeitedOptionInr;
    payBlock.appendChild(el(`
      <div class="ledger">
        <div class="ledger-row"><span class="label">Cash off fare<span class="unit">money</span></span><span class="amt">₹${fmt(pay.cashOffInr)}</span></div>
        <div class="ledger-row"><span class="label">Points earned<span class="unit">speculative, depends on redeeming well</span></span><span class="amt">₹${fmt(pay.pointsValueInr)}</span></div>
        <div class="ledger-row"><span class="label">Option value<span class="unit">not money, the worth of keeping a choice</span></span><span class="amt">₹${fmt(netOption)}</span></div>
      </div>
      <p class="ledger-disclaimer">These are deliberately not summed into one figure.</p>
    `));
    if (pay.notes.length) {
      const ul = document.createElement("ul");
      ul.className = "steps-list";
      for (const n of pay.notes) ul.appendChild(el(`<li>${n}</li>`));
      payBlock.appendChild(ul);
    }
    root.appendChild(payBlock);
  }

  // --- Rights --------------------------------------------------------
  if (plan.rights.length) {
    const rightsBlock = el(`<div class="result-block"><h2>Your rights on this booking</h2></div>`);
    const ul = document.createElement("ul");
    ul.className = "plain-list";
    for (const r of plan.rights) ul.appendChild(el(`<li>${r}</li>`));
    rightsBlock.appendChild(ul);
    root.appendChild(rightsBlock);
  }

  // --- Risks --------------------------------------------------------
  if (plan.risks.length) {
    const risksBlock = el(`<div class="result-block"><h2>Risk control</h2></div>`);
    const ul = document.createElement("ul");
    ul.className = "plain-list";
    for (const r of plan.risks) ul.appendChild(el(`<li>${r}</li>`));
    risksBlock.appendChild(ul);
    root.appendChild(risksBlock);
  }

  root.classList.add("show");
  const blocks = root.querySelectorAll(".result-block");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  blocks.forEach((b, i) => {
    if (reduceMotion) {
      b.style.opacity = "1";
      b.style.transform = "none";
    } else {
      b.style.animationDelay = `${i * 60}ms`;
      requestAnimationFrame(() => b.classList.add("reveal"));
    }
  });

  root.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
}

// ---------------------------------------------------------------------------
// Submit
// ---------------------------------------------------------------------------

const errorBanner = document.getElementById("error-banner");

function showError(message) {
  errorBanner.textContent = message;
  errorBanner.classList.add("show");
}
function clearError() {
  errorBanner.classList.remove("show");
  errorBanner.textContent = "";
}

document.getElementById("trip-form").addEventListener("submit", (e) => {
  e.preventDefault();
  clearError();

  const originCode = originCombo.code();
  const destCode = destCombo.code();
  const departRaw = document.getElementById("depart-input").value;
  const returnRaw = document.getElementById("return-input").value;

  if (!/^[A-Z]{3}$/.test(originCode) || !/^[A-Z]{3}$/.test(destCode)) {
    showError("Pick an origin and destination from the list, or type a 3-letter airport code.");
    return;
  }
  if (!departRaw) {
    showError("Pick a departure date.");
    return;
  }
  const depart = mkDate(departRaw);
  const ret = returnRaw ? mkDate(returnRaw) : null;
  if (ret && ret < depart) {
    showError("Return date can't be before departure.");
    return;
  }

  const fareRaw = document.getElementById("fare-input").value;
  const baselineRaw = document.getElementById("baseline-input").value;
  const pointsRaw = document.getElementById("points-input").value;
  const programmesRaw = document.getElementById("programmes-input").value;

  let trip, traveller;
  try {
    const flexibility = makeFlexibility({
      dateFlexDays: flexState.hardDates ? 0 : Number(flexDaysInput.value),
      destinationFlex: flexState.destinationFlex,
      acceptsSelfTransfer: flexState.acceptsSelfTransfer,
      canBookFast: flexState.canBookFast,
      originFlex: flexState.originFlex,
      hardDates: flexState.hardDates,
    });
    trip = makeTrip({
      origin: originCode, destination: destCode, depart, ret,
      cabin: cabinValue, pax: paxValue, flexibility,
      observedFare: fareRaw ? Number(fareRaw) : null,
      baselineFare: baselineRaw ? Number(baselineRaw) : null,
    });
    traveller = makeTraveller({
      cards: [...selectedCards].map((k) => CARD_CATALOGUE[k]),
      loyaltyProgrammes: programmesRaw.split(",").map((s) => s.trim()).filter(Boolean),
      availablePoints: pointsRaw ? Number(pointsRaw) : 0,
      isStudent: categoryState.isStudent,
      isSenior: categoryState.isSenior,
      isArmedForces: categoryState.isArmedForces,
      indianPassport: !categoryState.nonIndianPassport,
    });
  } catch (err) {
    showError(err.message);
    return;
  }

  const plan = planTrip(trip, traveller, {
    regime: document.getElementById("regime-input").value,
    today: todayUTC(),
    decisionProbability: Number(decisionProbInput.value) / 100,
    foreignPos,
  });

  renderResults(plan, trip.referenceFare);
});
