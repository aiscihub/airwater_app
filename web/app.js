"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const monthNames = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"
];

const archetypeLabels = {
  hot_dry: "Hot & dry",
  warm_humid: "Warm & humid",
  tropical: "Tropical",
  mild_seasonal: "Mild / seasonal",
  generic: "Generic / mixed"
};

const VERDICT_LABELS = {
  MEETS_TARGET: "Meets target",
  BELOW_TARGET: "Below target",
  REGEN_INFEASIBLE: "Regen infeasible",
  INSUFFICIENT_EVIDENCE: "Insufficient evidence",
  OUT_OF_DOMAIN: "Out of domain"
};

const LAB_CAUTION_LABEL = "Candidate for laboratory testing — proceed with caution";

function isLabCaution(row) {
  return Boolean(row.meets_target) && row.confidence === "Low";
}

const presets = {
  desert: {
    location: "Phoenix, Arizona", month: 7, mass_kg: 10, target_liters_day: 3,
    max_regen_temp_c: 85, energy_source: "Solar only", efficiency: 0.55,
    data_source: "NASA POWER historical sample"
  },
  humid: {
    location: "Miami, Florida", month: 8, mass_kg: 8, target_liters_day: 5,
    max_regen_temp_c: 80, energy_source: "Solar only", efficiency: 0.60,
    data_source: "NASA POWER historical sample"
  },
  mild: {
    location: "Nairobi, Kenya", month: 1, mass_kg: 10, target_liters_day: 3,
    max_regen_temp_c: 75, energy_source: "Electricity or hybrid", efficiency: 0.55,
    data_source: "NASA POWER historical sample"
  },
  tropical: {
    location: "Singapore", month: 6, mass_kg: 6, target_liters_day: 4,
    max_regen_temp_c: 85, energy_source: "Solar only", efficiency: 0.60,
    data_source: "NASA POWER historical sample"
  }
};

let customLocation = null;
let geocodeAbortController = null;
let geocodeDebounceTimer = null;
let lastResult = null;
let materialLibraryCache = null;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function cToF(celsius) {
  return celsius * 9 / 5 + 32;
}

function fToC(fahrenheit) {
  return (fahrenheit - 32) * 5 / 9;
}

function cDeltaToF(celsiusDelta) {
  return celsiusDelta * 9 / 5;
}

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => { toast.hidden = true; }, 6000);
}

function setLoading(isLoading) {
  $("#loading").hidden = !isLoading;
  $("#run-button").disabled = isLoading;
  if (isLoading) $("#console-source-chip").textContent = "Climate: running model and optimizer...";
}

function setControlValues(scenario) {
  $("#location").value = scenario.location;
  $("#month").value = String(scenario.month);
  $("#target").value = String(scenario.target_liters_day);
  $("#mass").value = String(scenario.mass_kg);
  $("#regen").value = String(Math.round(cToF(scenario.max_regen_temp_c)));
  $("#energy").value = scenario.energy_source;
  $("#efficiency").value = String(scenario.efficiency);
  $("#data-source").value = scenario.data_source;
  $("#specific-date").value = "";
  updateRangeOutputs();
}

function applyPreset(name) {
  if (!presets[name]) return;
  clearCustomLocation();
  setControlValues(presets[name]);
  $$(".preset-chip").forEach(button => button.classList.toggle("active", button.dataset.preset === name));
}

function clearCustomLocation() {
  customLocation = null;
  $("#location-selected").hidden = true;
  $("#location-selected").innerHTML = "";
  $("#location-search").value = "";
  hideLocationResults();
}

function hideLocationResults() {
  const list = $("#location-results");
  list.hidden = true;
  list.innerHTML = "";
}

function renderLocationResults(results) {
  const list = $("#location-results");
  if (!results.length) {
    list.innerHTML = `<li><small>No matches. Try a broader search or a nearby major city.</small></li>`;
    list.hidden = false;
    return;
  }
  list.innerHTML = results.map((result, index) => `
    <li data-index="${index}">${escapeHtml(result.display_name)}<small>${result.latitude.toFixed(2)}, ${result.longitude.toFixed(2)} &middot; ${escapeHtml(result.climate_kind.replaceAll("_", " "))} climate band</small></li>
  `).join("");
  list.hidden = false;
  $$("li", list).forEach(item => {
    item.addEventListener("click", () => selectCustomLocation(results[Number(item.dataset.index)]));
  });
}

function selectCustomLocation(result) {
  customLocation = result;
  $("#location-search").value = result.display_name;
  hideLocationResults();
  $$(".preset-chip").forEach(button => button.classList.remove("active"));
  const selected = $("#location-selected");
  selected.hidden = false;
  selected.innerHTML = `<span>Using searched location: ${escapeHtml(result.display_name)} (${result.latitude.toFixed(2)}, ${result.longitude.toFixed(2)})</span><button type="button" id="clear-location" aria-label="Clear searched location">&times;</button>`;
  $("#clear-location").addEventListener("click", clearCustomLocation);
  if ($("#data-source").value !== "NASA POWER historical sample") {
    $("#data-source").value = "NASA POWER historical sample";
  }
}

async function searchLocations(query) {
  if (geocodeAbortController) geocodeAbortController.abort();
  geocodeAbortController = new AbortController();
  try {
    const response = await fetch(`/api/geocode?q=${encodeURIComponent(query)}`, { signal: geocodeAbortController.signal });
    const result = await response.json();
    renderLocationResults(result.results || []);
  } catch (error) {
    if (error.name !== "AbortError") showToast("Location search is unavailable right now.");
  }
}

function bindLocationSearch() {
  const input = $("#location-search");
  input.addEventListener("input", () => {
    const query = input.value.trim();
    window.clearTimeout(geocodeDebounceTimer);
    if (query.length < 2) {
      hideLocationResults();
      return;
    }
    geocodeDebounceTimer = window.setTimeout(() => searchLocations(query), 350);
  });
  input.addEventListener("keydown", event => {
    if (event.key === "Escape") hideLocationResults();
  });
  document.addEventListener("click", event => {
    if (!event.target.closest(".location-search")) hideLocationResults();
  });
  $("#location").addEventListener("change", clearCustomLocation);
}

function updateRangeOutputs() {
  $("#mass-output").textContent = `${Number($("#mass").value).toFixed(0)} kg`;
  $("#regen-output").textContent = `${Number($("#regen").value).toFixed(0)} F`;
  $("#efficiency-output").textContent = `${Math.round(Number($("#efficiency").value) * 100)}%`;
  $("#cost-output").textContent = `$${Number($("#alt-cost").value).toFixed(2)}/L`;
}

function readScenario() {
  const scenario = {
    location: $("#location").value,
    month: Number($("#month").value),
    mass_kg: Number($("#mass").value),
    target_liters_day: Number($("#target").value),
    max_regen_temp_c: fToC(Number($("#regen").value)),
    energy_source: $("#energy").value,
    efficiency: Number($("#efficiency").value),
    data_source: $("#data-source").value,
    alternative_cost_per_l: Number($("#alt-cost").value)
  };
  if (customLocation) {
    scenario.location = customLocation.display_name;
    scenario.latitude = customLocation.latitude;
    scenario.longitude = customLocation.longitude;
    scenario.climate_kind = customLocation.climate_kind;
  }
  const specificDate = $("#specific-date").value;
  if (specificDate) scenario.date = specificDate;
  return scenario;
}

function circularWindow(hours) {
  if (!hours || !hours.length) return "None";
  const values = hours.map(Number);
  const set = new Set(values);
  let start = values.find(hour => !set.has((hour + 23) % 24));
  if (start === undefined) start = values[0];
  let cursor = start;
  while (set.has((cursor + 1) % 24) && (cursor + 1) % 24 !== start) {
    cursor = (cursor + 1) % 24;
  }
  const end = (cursor + 1) % 24;
  return `${String(start).padStart(2, "0")}:00-${String(end).padStart(2, "0")}:00`;
}

function metricCard(label, value, note = "") {
  return `<article class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>${note ? `<small>${escapeHtml(note)}</small>` : ""}</article>`;
}

const ICON_SVG = {
  pass: `<svg viewBox="0 0 20 20"><polyline points="4,10 8,14 16,5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  warn: `<svg viewBox="0 0 20 20"><line x1="10" y1="3" x2="10" y2="11.5" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><circle cx="10" cy="15" r="1.3" fill="currentColor"/></svg>`,
  fail: `<svg viewBox="0 0 20 20"><line x1="5" y1="5" x2="15" y2="15" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><line x1="15" y1="5" x2="5" y2="15" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>`
};

function statusIcon(status) {
  const kind = status === "pass" ? "pass" : status === "warn" ? "warn" : "fail";
  return `<span class="status-icon ${kind}" aria-hidden="true">${ICON_SVG[kind]}</span>`;
}

function renderChecklist(containerId, checks) {
  $(`#${containerId}`).innerHTML = checks.map(check => `
    <div class="check-row ${check.status}">
      ${statusIcon(check.status)}
      <div><strong>${escapeHtml(check.label)}</strong><span>${escapeHtml(check.reason)}</span></div>
    </div>
  `).join("");
}

function materialImageSlug(shortName) {
  return String(shortName).toLowerCase().replaceAll(" ", "-");
}

function renderRecHeader(top) {
  const pill = $("#rec-target-pill");
  pill.textContent = top.meets_target ? "Meets target" : "Below target";
  pill.classList.toggle("pill-good", top.meets_target);
  pill.classList.toggle("pill-warn", !top.meets_target);
}

function renderStatRow(data) {
  const top = data.top;
  const scenario = data.scenario;
  $("#stat-row").innerHTML = `
    <div class="stat"><span>Equilibrium sorption potential</span><strong>${escapeHtml(top.estimated_range)}</strong><small>before device losses &middot; target ${Number(scenario.target_liters_day).toFixed(1)} L/day</small></div>
    <div class="stat"><span>Confidence</span><strong>${escapeHtml(top.confidence)}</strong><small>evidence score ${Math.round(Number(top.evidence_score) * 100)}%</small></div>
    <div class="stat"><span>Regeneration target</span><strong>${cToF(Number(top.regen_temp_c)).toFixed(0)} F</strong><small>within user limit</small></div>
    <div class="stat"><span>Equivalent cycles/day</span><strong>${Number(top.cycles_day).toFixed(0)}</strong><small>simplified estimate</small></div>
  `;
}

function renderWinnerPanel(data) {
  const top = data.top;
  const scenario = data.scenario;
  $("#console-material").textContent = top.short_name;
  $("#console-confidence").textContent = `${top.confidence} confidence`;

  const reasons = [
    ["Climate match", `Uptake window aligns with ${Number(top.adsorption_rh_percent).toFixed(0)}% RH in the capture window.`],
    ["Heat feasibility", `Can regenerate within the ${cToF(Number(scenario.max_regen_temp_c)).toFixed(0)} F user limit.`],
    ["Evidence-aware ranking", `${Math.round(Number(top.evidence_score) * 100)}% evidence score &middot; ${Math.round(Number(top.water_stability_score) * 100)}% stability score`]
  ];
  $("#console-reasons").innerHTML = reasons.map(reason => `
    <li>${statusIcon("pass")}<div><b>${escapeHtml(reason[0])}</b><span>${reason[1]}</span></div></li>
  `).join("");

  const figure = $("#molecule-figure");
  const img = $("#molecule-art");
  img.onerror = () => { figure.hidden = true; };
  img.onload = () => { figure.hidden = false; };
  $("#molecule-caption").textContent = `${top.short_name} crystal structure`;
  img.src = `/materials/${materialImageSlug(top.short_name)}.png`;
}

function renderSidebarRun(data) {
  const scenario = data.scenario;
  $("#run-location").textContent = scenario.location;
  $("#run-coords").textContent = `${Number(scenario.latitude).toFixed(2)} N, ${Number(scenario.longitude).toFixed(2)} W`;
  $("#run-month").textContent = scenario.date || scenario.month_name;
  $("#run-demand").textContent = `${Number(scenario.target_liters_day).toFixed(1)} L/day`;
  $("#run-mass").textContent = `${Number(scenario.mass_kg).toFixed(0)} kg`;
  $("#run-energy").textContent = scenario.energy_source;
  $("#run-regen").textContent = `${cToF(Number(scenario.max_regen_temp_c)).toFixed(0)} F`;
  $("#run-source").textContent = data.climate_source.length > 42 ? `${data.climate_source.slice(0, 39)}...` : data.climate_source;
  $("#run-source").title = data.climate_source;
  $("#run-time").textContent = new Date().toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function renderFooter(data) {
  $("#footer-climate").textContent = `Climate: ${data.climate_source}`;
  $("#footer-model").textContent = `Model: ${data.metrics.model || "Random Forest"}`;
  $("#footer-run").textContent = `Run: ${new Date(data.generated_at_unix * 1000).toISOString()}`;
}

function updateDateFallbackNote(source) {
  const note = $("#date-fallback-note");
  source = source || "";
  note.classList.remove("is-live");
  if (source.includes("solar from") || source.includes("solar modeled")) {
    const yearAgo = source.match(/solar from ([^,]+),/);
    note.textContent = yearAgo
      ? `This run: live temperature/humidity, but solar was backfilled from ${yearAgo[1]} (same date a year earlier) since satellite solar data isn't processed for this date yet.`
      : "This run: live temperature/humidity, but solar is modeled since satellite solar data isn't processed for this date yet.";
    note.hidden = false;
  } else if (source.indexOf("Demo profile fallback") === 0) {
    note.textContent = `This run: live NASA data wasn't available, so the offline demo profile was used instead (${source.replace("Demo profile fallback; NASA fetch failed: ", "")})`;
    note.hidden = false;
  } else if (source.indexOf("NASA POWER hourly") === 0) {
    note.textContent = "This run: fully live NASA POWER data (temperature, humidity, and solar all measured for this date).";
    note.classList.add("is-live");
    note.hidden = false;
  } else {
    note.hidden = true;
  }
}

function renderClimate(data) {
  const climate = data.climate;
  const schedule = data.schedule;
  const summary = data.climate_summary;
  const actionColors = {
    "Capture": "rgba(0,123,255,0.13)",
    "Release + condense": "rgba(255,218,185,0.38)"
  };
  const shapes = schedule
    .filter(row => actionColors[row.action])
    .map(row => ({
      type: "rect", xref: "x", yref: "paper", x0: Number(row.hour) - 0.48, x1: Number(row.hour) + 0.48,
      y0: 0, y1: 1, line: { width: 0 }, fillcolor: actionColors[row.action], layer: "below"
    }));
  const maxSolar = Math.max(...climate.map(row => Number(row.solar_w_m2)), 1);

  const traces = [
    {
      x: climate.map(row => row.hour), y: climate.map(row => row.solar_w_m2), type: "bar",
      name: "Solar availability", yaxis: "y3", marker: { color: "#E6E6FA" }, opacity: .72,
      hovertemplate: "%{x}:00<br>%{y:.0f} W/m2<extra></extra>"
    },
    {
      x: climate.map(row => row.hour), y: climate.map(row => row.relative_humidity_percent),
      type: "scatter", mode: "lines+markers", name: "Relative humidity",
      line: { color: "#007BFF", width: 3 }, marker: { size: 6 },
      hovertemplate: "%{x}:00<br>RH %{y:.1f}%<extra></extra>"
    },
    {
      x: climate.map(row => row.hour), y: climate.map(row => cToF(Number(row.temperature_c))),
      type: "scatter", mode: "lines+markers", name: "Temperature", yaxis: "y2",
      line: { color: "#FFDAB9", width: 3.5 }, marker: { size: 7, color: "#FFDAB9" },
      hovertemplate: "%{x}:00<br>%{y:.1f} F<extra></extra>"
    }
  ];
  const layout = {
    height: 300, margin: { l: 44, r: 46, t: 40, b: 40 }, paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(250,252,254,.78)", shapes,
    legend: { orientation: "h", x: 0, y: 1.12, font: { size: 10 } }, hovermode: "x unified",
    xaxis: { title: "Hour of day", range: [-0.5, 23.5], tickmode: "array", tickvals: [0,4,8,12,16,20,23], ticktext: ["00:00","04:00","08:00","12:00","16:00","20:00","23:00"], gridcolor: "#e4edf1" },
    yaxis: { title: "Humidity (%)", range: [0, 100], gridcolor: "#e4edf1", zeroline: false },
    yaxis2: { title: "Temperature (F)", overlaying: "y", side: "right", showgrid: false, zeroline: false },
    yaxis3: { overlaying: "y", side: "right", range: [0, maxSolar * 2.8], showgrid: false, showticklabels: false, zeroline: false },
    font: { family: "Inter, system-ui, sans-serif", color: "#0b2d47", size: 11 },
    bargap: .18
  };
  Plotly.react("climate-chart", traces, layout, { displayModeBar: false, responsive: true });

  $("#console-source-chip").textContent = `Climate: ${data.climate_source}`;
  updateDateFallbackNote(data.climate_source);

  const archetypeBadge = $("#climate-archetype-badge");
  const archetypeLabel = archetypeLabels[data.climate_archetype_name] || data.climate_archetype_name || "";
  const isAtypical = Number(data.ood_distance) > 1.8;
  archetypeBadge.textContent = archetypeLabel ? `${archetypeLabel}${isAtypical ? " (atypical)" : ""}` : "";
  archetypeBadge.classList.toggle("atypical", isAtypical);

  $("#climate-summary").innerHTML = `
    <div class="summary-list">
      <div class="summary-item"><span>Daily average humidity</span><strong>${Number(summary.average_humidity_percent).toFixed(0)}%</strong></div>
      <div class="summary-item"><span>Daily average temperature</span><strong>${cToF(Number(summary.average_temperature_c)).toFixed(1)} F</strong></div>
      <div class="summary-item"><span>Peak solar availability</span><strong>${Number(summary.peak_solar_w_m2).toFixed(0)} W/m2</strong></div>
    </div>
  `;

  $("#timeline").innerHTML = schedule.map(row => {
    const className = row.action === "Capture" ? "capture" : row.action === "Release + condense" ? "release" : "idle";
    const title = `${String(row.hour).padStart(2, "0")}:00 - ${row.action}; ${cToF(Number(row.temperature_c)).toFixed(1)} F; RH ${Number(row.relative_humidity_percent).toFixed(1)}%; solar ${Number(row.solar_w_m2).toFixed(0)} W/m2`;
    return `<div class="timeline-cell ${className}" data-hour="${String(row.hour).padStart(2, "0")}" title="${escapeHtml(title)}"></div>`;
  }).join("");
}

// ---------- Tab 1: Compare MOFs ----------

function lossReasonChips(reasons) {
  if (!reasons || !reasons.length) return "";
  return `<div class="loss-reasons">${reasons.map(reason => `<span class="loss-chip">${escapeHtml(reason)}</span>`).join("")}</div>`;
}

function renderCandidateCards(candidates) {
  const winner = candidates[0];
  $("#candidate-cards").innerHTML = candidates.slice(0, 3).map((row, index) => `
    <article class="candidate ${index === 0 ? "top" : ""}">
      <span class="candidate-rank">#${index + 1}</span>
      <h4>${escapeHtml(row.short_name)}</h4>
      <div class="candidate-yield">${escapeHtml(row.estimated_range)}</div>
      <div class="candidate-meta">Working capacity: ${Number(row.predicted_working_capacity_kgkg).toFixed(3)} kg/kg</div>
      <div class="candidate-meta">Regen target: ${cToF(Number(row.regen_temp_c)).toFixed(0)} F</div>
      <div class="candidate-meta">Evidence: ${Math.round(Number(row.evidence_score) * 100)}%</div>
      <div class="candidate-pills">
        <span class="candidate-pill ${row.meets_target ? "pill-good" : "pill-warn"}">${row.meets_target ? "Meets target" : "Below target"}</span>
        ${index === 0 ? `<span class="candidate-pill pill-accent">Best overall fit</span>` : ""}
        <span class="candidate-pill ${row.tier === "A" || row.tier === "B" ? "pill-good" : "pill-warn"}">${row.tier === "A" || row.tier === "B" ? "Real isotherm" : "Exploratory"}</span>
        ${isLabCaution(row) ? `<span class="candidate-pill pill-caution">${LAB_CAUTION_LABEL}</span>` : ""}
      </div>
      ${index === 0 ? "" : `<p class="loss-why">Why not #1: ${escapeHtml((row.loss_reasons || [])[0] || "Lower overall score")}</p>`}
      ${lossReasonChips(row.loss_reasons)}
    </article>
  `).join("");
}

function renderCandidateChart(candidates, maxRegenTempC) {
  const winnerName = candidates[0].name;
  const yields = candidates.map(row => Number(row.estimated_liters_day));
  const sizes = yields.map(value => clamp(value * 7 + 18, 20, 62));
  const userLimitF = cToF(maxRegenTempC);

  const trace = {
    x: candidates.map(row => cToF(Number(row.regen_temp_c))),
    y: candidates.map(row => row.predicted_working_capacity_kgkg),
    text: candidates.map(row => row.short_name),
    customdata: candidates.map(row => [row.estimated_liters_day, row.confidence, row.evidence_score, VERDICT_LABELS[row.verdict] || row.verdict]),
    type: "scatter", mode: "markers+text", textposition: "top center",
    marker: {
      size: sizes, color: candidates.map(row => row.evidence_score), colorscale: [[0,"#E6E6FA"],[1,"#003B5C"]],
      showscale: true, colorbar: { title: "Evidence", thickness: 12 },
      line: {
        color: candidates.map(row => row.name === winnerName ? "#001F3F" : (row.verdict !== "MEETS_TARGET" ? "#B8672E" : "white")),
        width: candidates.map(row => row.name === winnerName ? 3 : (row.verdict !== "MEETS_TARGET" ? 2.2 : 1.5))
      },
      opacity: .92
    },
    hovertemplate: "<b>%{text}</b><br>Working capacity %{y:.3f} kg/kg<br>Regeneration %{x:.0f} F<br>Sorption potential %{customdata[0]:.2f} L/day-equiv (before device losses)<br>%{customdata[1]} confidence<br>Evidence %{customdata[2]:.0%}<br>%{customdata[3]}<extra></extra>"
  };
  const layout = {
    height: 385, margin: { l: 55, r: 60, t: 30, b: 50 }, paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(250,252,254,.78)",
    shapes: [{ type: "line", x0: userLimitF, x1: userLimitF, y0: 0, y1: 1, yref: "paper", line: { dash: "dash", color: "#54708A", width: 1.5 } }],
    annotations: [{ x: userLimitF, y: 1, yref: "paper", yanchor: "bottom", text: `User heat limit: ${userLimitF.toFixed(0)} F`, showarrow: false, font: { size: 10, color: "#54708A" } }],
    xaxis: { title: "Regeneration target (F)", gridcolor: "#e4edf1", zeroline: false },
    yaxis: { title: "Predicted working capacity (kg/kg)", gridcolor: "#e4edf1", zeroline: false },
    font: { family: "Inter, system-ui, sans-serif", color: "#0b2d47", size: 11 }
  };
  Plotly.react("candidate-chart", [trace], layout, { displayModeBar: false, responsive: true });
}

function renderCandidateDetail(data, selectedName) {
  const candidates = data.candidates;
  const winner = candidates[0];
  const row = candidates.find(item => item.short_name === selectedName) || winner;
  const isWinner = row.name === winner.name;
  $("#candidate-detail").innerHTML = `
    <h4>${escapeHtml(row.short_name)}</h4>
    <p>${escapeHtml(row.notes)}</p>
    <p><strong>Literature cue:</strong> ${escapeHtml(row.source_hint)}</p>
    <div class="detail-stats">
      <div class="detail-stat"><span>Climate fit</span><strong>${Math.round(Number(row.climate_fit_score) * 100)}%</strong></div>
      <div class="detail-stat"><span>Target coverage</span><strong>${Number(row.target_coverage_percent).toFixed(0)}%</strong></div>
      <div class="detail-stat"><span>Capture uptake</span><strong>${Number(row.uptake_at_capture_kgkg).toFixed(3)} kg/kg</strong></div>
      <div class="detail-stat"><span>Residual uptake</span><strong>${Number(row.residual_uptake_kgkg).toFixed(3)} kg/kg</strong></div>
    </div>
    ${isWinner ? "" : `<div class="limitation"><strong>${escapeHtml(row.short_name)} vs. ${escapeHtml(winner.short_name)}:</strong> ${escapeHtml(row.short_name)} predicts ${Math.abs(row.estimated_liters_day - winner.estimated_liters_day).toFixed(2)} L/day ${row.estimated_liters_day < winner.estimated_liters_day ? "less" : "more"} than the winner, ${(row.loss_reasons || []).length ? `and ranks lower mainly on: ${row.loss_reasons.join(", ").toLowerCase()}.` : "but the winner still leads on overall score."}</div>`}
    <div class="limitation"><strong>Main limitation:</strong> ${escapeHtml(row.limitation)}</div>
    ${isLabCaution(row) ? `<p class="notice notice-red"><strong>${escapeHtml(LAB_CAUTION_LABEL)}.</strong> ${escapeHtml(row.short_name)} meets the yield target, but prediction confidence is Low (${Number(row.prediction_uncertainty_percent).toFixed(0)}% uncertainty${row.tier === "C" ? ", no NIST isotherm on file" : ""}). Treat this as a screening lead for lab validation, not a settled recommendation.</p>` : ""}
  `;
  renderEvidenceView(row, data);
}

let evidenceCache = {};

async function fetchIsothermDetail(materialName) {
  if (evidenceCache[materialName]) return evidenceCache[materialName];
  const response = await fetch(`/api/isotherm?material=${encodeURIComponent(materialName)}`);
  const result = await response.json();
  evidenceCache[materialName] = result;
  return result;
}

function renderLomoValidationTable(detail) {
  const maeByMaterial = detail.lomo_validation.per_material_mae_kgkg || {};
  const rows = Object.entries(maeByMaterial).sort((a, b) => a[1] - b[1]);
  return `
    <div class="lomo-validation">
      <h5>Nearest available validation: leave-one-MOF-out error on the 12 real-isotherm materials</h5>
      <p>This material has no isotherm of its own, so its prediction can't be validated directly against measurements. This is the closest evidence available -- how well the same extrapolation approach performs on materials it also hadn't seen, tested one at a time.</p>
      <table class="lomo-table">
        <thead><tr><th>Material</th><th>Leave-one-out MAE (kg/kg)</th></tr></thead>
        <tbody>${rows.map(([name, mae]) => `<tr><td>${escapeHtml(name)}</td><td>${Number(mae).toFixed(3)}</td></tr>`).join("")}</tbody>
      </table>
      <p class="lomo-footnote">Overall leave-one-MOF-out: MAE ${Number(detail.lomo_validation.overall_mae_kgkg).toFixed(3)} kg/kg, R&sup2; ${Number(detail.lomo_validation.overall_r2).toFixed(2)}.</p>
    </div>
  `;
}

function renderEvidenceChart(detail, row, siteRhMin, siteRhMax) {
  const traces = [];
  const shapes = [];
  const isReal = detail.tier === "A" || detail.tier === "B";
  const dataRange = detail.data_rh_range;

  if (siteRhMin != null && siteRhMax != null) {
    shapes.push({
      type: "rect", xref: "x", yref: "paper", x0: siteRhMin, x1: siteRhMax, y0: 0, y1: 1,
      fillcolor: "rgba(0,123,255,0.08)", line: { width: 0 }
    });
  }

  if (isReal && dataRange) {
    traces.push({
      x: detail.measured_points.map(p => p.rh_percent),
      y: detail.measured_points.map(p => p.uptake_kgkg),
      mode: "markers", type: "scatter", name: "Experimental measurements",
      marker: { size: 6, color: "#003B5C", opacity: 0.6 },
      hovertemplate: "%{x:.1f}% RH<br>%{y:.3f} kg/kg<extra>Measured</extra>"
    });

    const maxU = detail.fit.max_uptake_kgkg, rh50 = detail.fit.rh50_percent, k = detail.fit.steepness;
    const sigmoid = rh => maxU / (1 + Math.exp(-k * (rh - rh50)));
    const interpX = [], interpY = [], lowExtX = [], lowExtY = [], highExtX = [], highExtY = [];
    for (let rh = 0; rh <= 100; rh += 1) {
      const y = sigmoid(rh);
      if (rh < dataRange[0]) { lowExtX.push(rh); lowExtY.push(y); }
      else if (rh > dataRange[1]) { highExtX.push(rh); highExtY.push(y); }
      else { interpX.push(rh); interpY.push(y); }
    }
    if (lowExtX.length) { lowExtX.push(dataRange[0]); lowExtY.push(sigmoid(dataRange[0])); }
    if (highExtX.length) { highExtX.unshift(dataRange[1]); highExtY.unshift(sigmoid(dataRange[1])); }

    traces.push({ x: interpX, y: interpY, mode: "lines", type: "scatter", name: "Fitted curve (interpolation)", line: { color: "#007BFF", width: 2.5 } });
    const extName = "Fitted curve (extrapolation)";
    if (lowExtX.length) traces.push({ x: lowExtX, y: lowExtY, mode: "lines", type: "scatter", name: extName, legendgroup: "ext", showlegend: true, line: { color: "#B8672E", width: 2, dash: "dash" } });
    if (highExtX.length) traces.push({ x: highExtX, y: highExtY, mode: "lines", type: "scatter", name: extName, legendgroup: "ext", showlegend: !lowExtX.length, line: { color: "#B8672E", width: 2, dash: "dash" } });
  }

  traces.push({
    x: [Number(row.adsorption_rh_percent)], y: [Number(row.uptake_at_capture_kgkg)],
    mode: "markers", type: "scatter", name: "Adsorption point used",
    marker: { size: 13, color: "#1E8A5F", symbol: "diamond", line: { color: "white", width: 1.5 } },
    hovertemplate: "%{x:.1f}% RH<br>%{y:.3f} kg/kg<extra>Adsorption point used</extra>"
  });
  traces.push({
    x: [Number(row.residual_rh_percent)], y: [Number(row.residual_uptake_kgkg)],
    mode: "markers", type: "scatter", name: "Regeneration point used",
    marker: { size: 13, color: "#C1432E", symbol: "diamond", line: { color: "white", width: 1.5 } },
    hovertemplate: "%{x:.1f}% RH<br>%{y:.3f} kg/kg<extra>Regeneration point used</extra>"
  });

  const layout = {
    height: 380, margin: { l: 55, r: 20, t: 15, b: 40 }, paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(250,252,254,.78)",
    xaxis: { title: "Relative humidity (%)", range: [0, 100], gridcolor: "#e4edf1", zeroline: false },
    yaxis: { title: "Water uptake (kg/kg)", gridcolor: "#e4edf1", zeroline: false, rangemode: "tozero" },
    shapes,
    legend: { orientation: "h", y: -0.24, font: { size: 10 } },
    font: { family: "Inter, system-ui, sans-serif", color: "#0b2d47", size: 11 }
  };
  Plotly.react("evidence-chart", traces, layout, { displayModeBar: false, responsive: true });
}

async function renderEvidenceView(row, data) {
  const container = $("#evidence-view");
  if (!container) return;
  container.innerHTML = `<p class="evidence-loading">Loading evidence for ${escapeHtml(row.short_name)}...</p>`;

  let detail;
  try {
    detail = await fetchIsothermDetail(row.name);
  } catch (error) {
    container.innerHTML = `<p class="evidence-loading">Could not load evidence for ${escapeHtml(row.short_name)}.</p>`;
    return;
  }

  const climateRh = (data.climate || []).map(hour => Number(hour.relative_humidity_percent)).filter(value => !Number.isNaN(value));
  const siteRhMin = climateRh.length ? Math.min(...climateRh) : null;
  const siteRhMax = climateRh.length ? Math.max(...climateRh) : null;

  const isReal = detail.tier === "A" || detail.tier === "B";
  const dataRange = detail.data_rh_range;
  const isExtrapolating = rh => !dataRange || rh < dataRange[0] || rh > dataRange[1];
  const adsorbExtrapolating = isExtrapolating(Number(row.adsorption_rh_percent));
  const desorbExtrapolating = isExtrapolating(Number(row.residual_rh_percent));
  const anyExtrapolating = adsorbExtrapolating || desorbExtrapolating;

  container.innerHTML = `
    <div class="evidence-head">
      <h4>Evidence: is this interpolation or extrapolation?</h4>
      <p>${isReal
        ? `Real NIST ISODB water isotherm for ${escapeHtml(row.short_name)} -- ${detail.measured_points.length} measured points from ${detail.n_isotherms} isotherm(s), ${detail.n_papers} paper(s). Shaded band is the relative-humidity range this site actually sees across the day.`
        : `${escapeHtml(row.short_name)} has no NIST isotherm on file -- there is no measured curve to show. The two operating points below come from ML extrapolation across other materials' descriptor patterns, not a fitted curve for this material.`}</p>
    </div>
    <div class="evidence-body">
      <div class="chart-card"><div id="evidence-chart" class="plot"></div></div>
      <div class="evidence-side">
        <div class="evidence-fact">
          <span>Model is</span>
          <strong class="${anyExtrapolating ? "evidence-extrapolating" : "evidence-interpolating"}">${anyExtrapolating ? "Extrapolating" : "Interpolating"}</strong>
          <small>${isReal ? `Measured range: ${dataRange[0].toFixed(0)}-${dataRange[1].toFixed(0)}% RH` : "No measured range -- fully predicted"}</small>
        </div>
        <div class="evidence-fact">
          <span>Adsorption point used</span>
          <strong>${Number(row.adsorption_rh_percent).toFixed(0)}% RH, ${Number(row.uptake_at_capture_kgkg).toFixed(3)} kg/kg</strong>
          <small>${adsorbExtrapolating ? "Outside measured range" : "Within measured range"}</small>
        </div>
        <div class="evidence-fact">
          <span>Regeneration point used</span>
          <strong>${Number(row.residual_rh_percent).toFixed(0)}% RH, ${Number(row.residual_uptake_kgkg).toFixed(3)} kg/kg</strong>
          <small>${desorbExtrapolating ? "Outside measured range" : "Within measured range"}</small>
        </div>
        ${isReal ? `
        <div class="evidence-fact">
          <span>Source</span>
          <strong>${detail.doi_list.map(doi => `<a href="https://doi.org/${escapeHtml(doi)}" target="_blank" rel="noopener">${escapeHtml(doi)}</a>`).join(", ") || "n/a"}</strong>
          <small>${detail.n_isotherms} NIST isotherm ID(s) on file</small>
        </div>` : ""}
        ${detail.data_quality_flags.length ? `<div class="evidence-fact evidence-fact-warn"><span>Data-quality flags</span><strong>${escapeHtml(detail.data_quality_flags.join(", "))}</strong></div>` : ""}
      </div>
    </div>
    ${!isReal ? renderLomoValidationTable(detail) : ""}
  `;
  renderEvidenceChart(detail, row, siteRhMin, siteRhMax);
}

function renderRankingTable(candidates) {
  const headers = ["Rank", "MOF", "Yield", "Working capacity", "Regen", "Climate fit", "Evidence", "Stability", "Confidence", "Verdict", "Why not #1"];
  const body = candidates.map((row, index) => `
    <tr>
      <td>${index + 1}</td><td><strong>${escapeHtml(row.short_name)}</strong>${row.tier === "C" ? ` <span class="tier-tag" title="No NIST isotherm on file">Exploratory</span>` : ""}${isLabCaution(row) ? ` <span class="tier-tag tier-tag-caution" title="${escapeHtml(LAB_CAUTION_LABEL)}">Proceed with caution</span>` : ""}</td><td>${escapeHtml(row.estimated_range)}</td>
      <td>${Number(row.predicted_working_capacity_kgkg).toFixed(3)}</td><td>${cToF(Number(row.regen_temp_c)).toFixed(0)} F</td>
      <td>${Math.round(Number(row.climate_fit_score) * 100)}%</td><td>${Math.round(Number(row.evidence_score) * 100)}%</td>
      <td>${Math.round(Number(row.water_stability_score) * 100)}%</td><td>${escapeHtml(row.confidence)}</td>
      <td><span class="verdict-pill verdict-${row.verdict}">${escapeHtml(VERDICT_LABELS[row.verdict] || row.verdict)}</span></td>
      <td>${escapeHtml((row.loss_reasons || []).join(", ") || "--")}</td>
    </tr>
  `).join("");
  $("#ranking-table").innerHTML = `<thead><tr>${headers.map(header => `<th>${header}</th>`).join("")}</tr></thead><tbody>${body}</tbody>`;
}

function renderCompareTab(data) {
  const candidates = data.candidates;
  renderCandidateCards(candidates);
  renderCandidateChart(candidates, data.scenario.max_regen_temp_c);
  const select = $("#candidate-select");
  select.innerHTML = candidates.map(row => `<option value="${escapeHtml(row.short_name)}">${escapeHtml(row.short_name)}</option>`).join("");
  select.value = candidates[0].short_name;
  renderCandidateDetail(data, select.value);
  select.onchange = () => renderCandidateDetail(data, select.value);
  renderRankingTable(candidates);
}

// ---------- Tab 2: Why this recommendation ----------

function renderWhyHero(data) {
  const top = data.top;
  const scenario = data.scenario;
  $("#why-heading").textContent = `Why ${top.short_name} won`;
  $("#why-tab-sub").textContent = `Understand why ${top.short_name} won`;
  $("#why-hero").innerHTML = `
    <div class="why-hero-grid">
      <div><span>Equilibrium sorption potential</span><strong>${escapeHtml(top.estimated_range)}</strong><small>before device losses</small></div>
      <div><span>Demand</span><strong>${Number(scenario.target_liters_day).toFixed(1)} L/day</strong></div>
      <div><span>Deployment readiness</span><strong class="${data.decision === "VIABLE" ? "readiness-ok" : "readiness-bad"}">${data.decision === "VIABLE" ? "Meets criteria" : "Do not deploy"}</strong></div>
      <div><span>Adsorb</span><strong>${circularWindow(data.climate_summary.capture_hours)}</strong></div>
      <div><span>Release</span><strong>${circularWindow(data.climate_summary.release_hours)}</strong></div>
    </div>
    <p class="why-explanation">${escapeHtml(data.explanation && data.explanation[0] ? data.explanation[0] : `${top.short_name} ranks first because its uptake behavior aligns with the capture window, its regeneration requirement fits the site's heat limit, and it retains the strongest evidence-adjusted score among the candidate materials.`)}</p>
  `;
}

function renderScoreChart(contributions) {
  const order = ["yield_vs_target", "climate_fit", "evidence", "stability", "cost_proxy", "regen_penalty"];
  const labels = ["Yield vs target", "Climate fit", "Evidence", "Stability", "Cost proxy", "Regen penalty"];
  const values = order.map(key => Number(contributions[key] || 0));
  const total = values.reduce((sum, value) => sum + value, 0);

  const trace = {
    x: values, y: labels, type: "bar", orientation: "h",
    marker: { color: values.map(value => value >= 0 ? "#007BFF" : "#B8672E") },
    text: values.map(value => `${value >= 0 ? "+" : ""}${value.toFixed(1)}`), textposition: "outside",
    hovertemplate: "%{y}: %{x:+.1f}<extra></extra>"
  };
  const layout = {
    height: 260, margin: { l: 110, r: 40, t: 10, b: 36 }, paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(250,252,254,.78)",
    xaxis: { title: `Final score: ${total.toFixed(1)}`, zeroline: true, zerolinecolor: "#c9dbe6", gridcolor: "#e4edf1" },
    yaxis: { automargin: true },
    font: { family: "Inter, system-ui, sans-serif", color: "#0b2d47", size: 11 }
  };
  Plotly.react("score-chart", [trace], layout, { displayModeBar: false, responsive: true });
}

function renderPredictionTarget(data) {
  const top = data.top;
  const target = Number(data.scenario.target_liters_day);
  const low = Number(top.yield_low_liters_day);
  const high = Number(top.yield_high_liters_day);
  const scaleMax = Math.max(high, target) * 1.15;
  const pct = value => clamp((value / scaleMax) * 100, 0, 100);
  const warn = low < target;
  $("#prediction-target").innerHTML = `
    <div class="pt-track">
      <div class="pt-range ${warn ? "warn" : ""}" style="left:${pct(low)}%; width:${Math.max(pct(high) - pct(low), 1)}%"></div>
      <div class="pt-tick target" style="left:${pct(target)}%"><span>Target ${target.toFixed(1)}</span></div>
      <div class="pt-tick bound" style="left:${pct(low)}%"><span>${low.toFixed(2)}</span></div>
      <div class="pt-tick bound" style="left:${pct(high)}%"><span>${high.toFixed(2)}</span></div>
    </div>
    ${warn ? `<p class="pt-warning">Lower bound is below target &mdash; this scenario currently fails the water-target check.</p>` : ""}
  `;
}

function renderWhyConfidenceGrid(data) {
  const top = data.top;
  const metrics = data.metrics;
  const isReal = top.tier === "A" || top.tier === "B";
  const fitCard = isReal
    ? metricCard("Isotherm fit quality", top.fit_r2 != null ? `R² = ${Number(top.fit_r2).toFixed(2)}` : "n/a", `vs. ${top.n_isotherms} digitized NIST ISODB isotherm(s)`)
    : metricCard("ML extrapolation error", `${Number(metrics.mae_kgkg || 0).toFixed(3)} kg/kg MAE`, "Leave-one-MOF-out CV; no isotherm exists for this material itself");
  $("#why-confidence-grid").innerHTML = [
    metricCard("Evidence quality", top.evidence_quality, `${Math.round(Number(top.evidence_score) * 100)}% evidence score -- how much real support exists`),
    metricCard("Prediction uncertainty", `${top.prediction_uncertainty_label} (±${Number(top.prediction_uncertainty_percent).toFixed(0)}%)`, "Calibrated interval width -- how uncertain the number itself is"),
    fitCard,
    metricCard("OOD status", Number(data.ood_distance) > 1.8 ? "Atypical site" : "Within modeled domain", `distance ${Number(data.ood_distance).toFixed(2)}`)
  ].join("");
  renderEvidenceBreakdown(top);
}

function renderEvidenceBreakdown(top) {
  const el = $("#evidence-breakdown");
  if (!el) return;
  const c = top.evidence_components;
  if (!c) {
    el.innerHTML = `<p class="evidence-breakdown-note">No evidence audit trail for ${escapeHtml(top.short_name)} -- Tier C material with no NIST isotherm on file; its evidence score is an unverified placeholder, not a computed total.</p>`;
    return;
  }
  const rows = [
    ["Real water isotherm on file", c.has_water_isotherm, 40],
    ["Independent source papers", c.independent_sources, 20],
    ["Temperature coverage", c.temperature_coverage, 15],
    ["Point density", c.point_density, 15],
    ["Regeneration/stability data", c.regeneration_or_stability_data, 10],
    ["Data-quality penalty", c.data_quality_penalty, 0],
  ];
  const total = rows.reduce((sum, [, value]) => sum + Number(value), 0);
  el.innerHTML = `
    <h4>Evidence score breakdown -- ${Math.round(total)}/100</h4>
    <dl class="evidence-breakdown-list">
      ${rows.map(([label, value, max]) => `
        <div><dt>${escapeHtml(label)}</dt><dd>${value >= 0 ? "+" : ""}${value}${max ? ` / ${max}` : ""}</dd></div>
      `).join("")}
    </dl>
  `;
}

function renderAlgorithmSteps(data) {
  const metrics = data.metrics;
  $("#algorithm-steps").innerHTML = `
    <ol class="algo-list">
      <li><b>1</b><div><strong>Read hourly climate</strong><span>RH(t), temperature(t), solar(t) for the selected site and month.</span></div></li>
      <li><b>2</b><div><strong>Predict uptake</strong><span>${escapeHtml(metrics.model || "Random Forest")} material-response model (formula fallback if unavailable).</span></div></li>
      <li><b>3</b><div><strong>Optimize cycle</strong><span>Search adsorb / release / idle schedule for the strongest capture and release windows.</span></div></li>
      <li><b>4</b><div><strong>Rank candidates</strong><span>Yield + climate fit + evidence + stability, minus the regeneration penalty.</span></div></li>
      <li><b>5</b><div><strong>Decision gate</strong><span>Recommend, or refuse with itemized reasons.</span></div></li>
    </ol>
    <div class="algo-meta">
      <span><b>Model:</b> ${escapeHtml(metrics.model || "n/a")}</span>
      <span><b>Held-out materials:</b> ${escapeHtml((metrics.held_out_mofs || []).join(", ") || "n/a")}</span>
      <span><b>Prediction source:</b> ${escapeHtml(data.top.model_source)}</span>
    </div>
    <div class="chart-card"><div id="feature-chart" class="plot model-plot"></div></div>
    ${renderModelProvenanceNotice(data)}
  `;
  renderFeatureChart(data.feature_importance || []);
}

function renderModelProvenanceNotice(data) {
  const top = data.top;
  const tier = String(top.tier || "");
  if (tier === "A" || tier === "B") {
    const nPapers = Number(top.n_papers || 0);
    const nIso = Number(top.n_isotherms || 0);
    const qualifier = tier === "B" ? " A data-quality flag was raised on this material's source isotherm(s) -- see Data & assumptions." : "";
    return `<p class="notice notice-blue">${escapeHtml(top.short_name)}'s uptake curve is fit directly from ${nIso} real NIST ISODB water isotherm(s) across ${nPapers} source paper(s), not simulated. Regeneration temperature, cycle time, and stability/cost scores are still literature-informed estimates pending citation.${qualifier}</p>`;
  }
  return `<p class="notice notice-red">${escapeHtml(top.short_name)} has no water isotherm on file in NIST ISODB. This estimate is ML extrapolation from other materials' descriptor patterns and should be treated as exploratory, not a validated result.</p>`;
}

function renderFeatureChart(featureImportance) {
  const values = [...featureImportance].sort((a, b) => Number(a.importance) - Number(b.importance)).slice(-8);
  if (!values.length) return;
  const labels = values.map(row => String(row.feature).replaceAll("_", " "));
  const trace = {
    x: values.map(row => row.importance), y: labels, type: "bar", orientation: "h",
    marker: { color: "#007BFF" }, hovertemplate: "%{y}<br>Importance %{x:.3f}<extra></extra>"
  };
  const layout = {
    height: 300, margin: { l: 170, r: 25, t: 20, b: 45 }, paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(250,252,254,.78)",
    xaxis: { title: "Random-forest feature importance", gridcolor: "#e4edf1", zeroline: false },
    yaxis: { automargin: true }, font: { family: "Inter, system-ui, sans-serif", color: "#0b2d47", size: 10 }
  };
  Plotly.react("feature-chart", [trace], layout, { displayModeBar: false, responsive: true });
}

function renderWhyTab(data) {
  renderWhyHero(data);
  renderChecklist("decision-checklist", data.decision_checks);
  renderScoreChart(data.score_contributions);
  renderPredictionTarget(data);
  renderWhyConfidenceGrid(data);
  renderAlgorithmSteps(data);
}

// ---------- Tab 3: Responsible use ----------

function renderVerdictHero(data) {
  const passed = data.refusal_checks.filter(check => check.status === "pass").length;
  const total = data.refusal_checks.length;
  if (data.decision === "VIABLE") {
    $("#verdict-hero").innerHTML = `
      <div class="verdict-card verdict-good">
        <span class="verdict-kicker">Current screening verdict</span>
        <h3>Meets screening criteria</h3>
        <p>${passed} / ${total} decision checks passed</p>
      </div>`;
  } else {
    $("#verdict-hero").innerHTML = `
      <div class="verdict-card verdict-bad">
        <span class="verdict-kicker">Current screening verdict</span>
        <h3>Do not deploy</h3>
        <p>${escapeHtml(data.decision_reasons[0] || "One or more refusal rules failed.")}</p>
        <p class="verdict-subnote">${passed} / ${total} decision checks passed</p>
      </div>`;
  }
}

function renderProvenance(data) {
  const top = data.top;
  const tier = String(top.tier || "");
  const isReal = tier === "A" || tier === "B";
  const materialData = isReal
    ? `Real NIST ISODB water isotherm (${top.n_isotherms} isotherm(s), ${top.n_papers} paper(s))`
    : "No NIST isotherm on file (exploratory / prototype descriptors)";
  const uptakeModel = isReal
    ? "Direct interpolation of the measured isotherm"
    : `ML extrapolation, ${escapeHtml(data.metrics.model || "RandomForestRegressor")}`;
  const trainingTargets = isReal
    ? "Sigmoid fit to real digitized isotherm points (see data/provenance_manifest.json)"
    : "No real training data for this material -- descriptor curve is an unverified placeholder";
  const scientificStatus = tier === "A"
    ? "Research-screening estimate, real isotherm evidence"
    : tier === "B"
      ? "Research-screening estimate, real isotherm evidence with a data-quality flag"
      : "Exploratory -- not backed by measured data";
  $("#provenance-grid").innerHTML = `
    <div><span>Climate data</span><strong>${escapeHtml(data.climate_source)}</strong></div>
    <div><span>Material data</span><strong>${escapeHtml(materialData)}</strong></div>
    <div><span>Uptake model</span><strong>${uptakeModel}</strong></div>
    <div><span>Training targets</span><strong>${escapeHtml(trainingTargets)}</strong></div>
    <div><span>Scientific status</span><strong>${escapeHtml(scientificStatus)}</strong></div>
  `;
}

function renderCostBreakdown(data) {
  const el = $("#cost-breakdown");
  if (!el) return;
  el.innerHTML = `
    <h4>Cost &amp; energy breakdown</h4>
    <dl class="cost-breakdown-list">
      <div><dt>Material (amortized wear/replacement)</dt><dd>$${Number(data.material_cost_per_l).toFixed(2)}/L</dd></div>
      <div><dt>Regeneration energy</dt><dd>$${Number(data.energy_cost_per_l).toFixed(2)}/L</dd></div>
      <div><dt>Total estimated cost</dt><dd>$${Number(data.cost_per_l).toFixed(2)}/L</dd></div>
      <div><dt>Energy use</dt><dd>${Number(data.energy_kwh_per_l).toFixed(2)} kWh/L</dd></div>
    </dl>
    <p class="cost-breakdown-note">${escapeHtml(data.cost_scope_note)}</p>
  `;
}

function renderResponsibleTab(data) {
  renderVerdictHero(data);
  renderChecklist("refusal-checklist", data.refusal_checks);
  renderCostBreakdown(data);
  renderProvenance(data);
}

// ---------- Data & assumptions panel ----------

function renderAssumptionsPanel() {
  const groups = [
    {
      title: "Decision thresholds", items: [
        ["Evidence quality", "≥ 65%", "Below this, a candidate's literature/evidence base is treated as thin."],
        ["Climate fit", "≥ 50%", "Below this, the capture-window humidity poorly matches the isotherm."],
        ["Prediction uncertainty", "≤ 40% of estimate", "Confidence-interval width relative to the point yield estimate."],
        ["Out-of-domain distance", "≤ 1.8σ", "Normalized distance to the nearest known climate archetype."]
      ]
    },
    {
      title: "Ranking weights (score = Σ weight × factor)", items: [
        ["Yield vs. target", "0.43", ""], ["Evidence", "0.19", ""], ["Climate fit", "0.16", ""],
        ["Stability", "0.13", ""], ["Cost proxy", "0.09", ""], ["Regeneration penalty", "-0.20", ""]
      ]
    },
    {
      title: "Energy & cost model constants", items: [
        ["Sorbent specific heat", "1.2 kJ/kg·K", "Typical porous solid/composite heat capacity."],
        ["Water desorption enthalpy", "2450 kJ/kg", "Vaporization enthalpy plus MOF binding-energy premium."],
        ["Solar collector area", "0.35 m²/kg sorbent", "Assumed solar-thermal collector footprint."],
        ["Solar collection efficiency", "45%", "Flat-plate/PV-thermal hybrid collector efficiency."],
        ["Material wear cost", "$0.08/kg·cycle at cost_score=0", "Amortized sorbent replacement, scaled by each material's cost score."],
        ["Energy cost", "$0.03/kWh solar · $0.01/kWh waste heat · $0.15/kWh grid", ""],
        ["Cost formula scope", "Material wear + regeneration energy only", "Excludes device capex, maintenance labor, and water post-treatment -- not yet modeled."]
      ]
    },
    {
      title: "Equilibrium sorption potential -- scope", items: [
        ["Basis", "Isotherm-model uptake at capture RH/T minus residual uptake at release RH/T", "An equilibrium sorption calculation, not a measurement from a built device."],
        ["Deterministic inputs applied", "MOF mass, cycles/day (time- and energy-budget limited), device efficiency", "Multiplied in before the range shown, but each is a scenario assumption, not a measured quantity."],
        ["Device efficiency", "User-set 10-90% multiplier", "A single blanket assumption for condenser and mechanical losses -- not derived from condenser thermodynamics or validated against a built device."],
        ["Range shown (L/day-equivalent)", "±calibrated uncertainty fraction from the uptake model's residuals", "Reflects isotherm-model uncertainty only -- it does not propagate uncertainty in mass, cycle count, or the device-efficiency assumption. Treat the figure as sorption potential before device losses, not delivered water."]
      ]
    },
    {
      title: "Regeneration feasibility & cycling", items: [
        ["Achievable temperature (solar)", "T_ambient + (solar flux × 45%) / 6 W/m²K", "Steady-state flat-plate collector approximation using this scenario's actual solar flux, not a fixed assumption."],
        ["Achievable temperature (waste heat / grid)", "120°C / 180°C practical ceiling", "Not flux-limited, so treated as reachable up to a practical ceiling rather than computed from solar input."],
        ["Regeneration safety margin", "5°C", "Required margin below the achievable/user-limit temperature to count a material as feasible."],
        ["Cycle time split", "50% adsorb dwell / 50% desorb+cool dwell", "cycle_minutes isn't broken into phases by any source data; cycles/day is capped by whichever of time-window fit or energy budget is more restrictive."]
      ]
    }
  ];
  $("#assumptions-grid").innerHTML = groups.map(group => `
    <div class="assumptions-group">
      <h3>${escapeHtml(group.title)}</h3>
      <dl>${group.items.map(([label, value, note]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}${note ? `<small>${escapeHtml(note)}</small>` : ""}</dd></div>`).join("")}</dl>
    </div>
  `).join("");
}

// ---------- Material library panel ----------

function evidenceQualityFromScore(score) {
  const value = Number(score);
  if (value >= 0.75) return "Strong";
  if (value >= 0.50) return "Moderate";
  return "Limited";
}

function isothermCurveSvg(material) {
  const maxUptake = Number(material.max_uptake_kgkg) || 0.01;
  const rh50 = Number(material.rh50_percent) || 50;
  const k = Number(material.steepness) || 0.1;
  const width = 220, height = 72, pad = 4;
  const points = [];
  for (let rh = 0; rh <= 100; rh += 4) {
    const uptake = maxUptake / (1 + Math.exp(-k * (rh - rh50)));
    const x = pad + (rh / 100) * (width - pad * 2);
    const y = height - pad - (uptake / maxUptake) * (height - pad * 2);
    points.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  }
  return `
    <svg viewBox="0 0 ${width} ${height}" class="isotherm-curve" preserveAspectRatio="none" aria-label="Uptake vs relative humidity curve">
      <polyline points="${points.join(" ")}" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    </svg>
  `;
}

function materialLibraryCard(material) {
  const tier = String(material.tier || "C");
  const isReal = tier === "A" || tier === "B";
  const tierLabel = tier === "A" ? "Tier A -- real isotherm" : tier === "B" ? "Tier B -- flagged for review" : "Tier C -- exploratory";
  const slug = materialImageSlug(material.short_name);
  const dois = String(material.doi_list || "").split(";").filter(Boolean);
  return `
    <article class="library-card tier-${tier.toLowerCase()}">
      <div class="library-card-image">
        <img src="/materials/${slug}.png" alt="" loading="lazy" onerror="this.closest('.library-card-image').classList.add('no-image')">
      </div>
      <div class="library-card-body">
        <div class="library-card-head">
          <h4>${escapeHtml(material.short_name)}</h4>
          <span class="tier-pill tier-pill-${tier.toLowerCase()}">${escapeHtml(tierLabel)}</span>
        </div>
        <p class="library-card-family">${escapeHtml(material.metal_family || "")} framework</p>
        ${isothermCurveSvg(material)}
        <div class="library-card-stats">
          <div><span>Max uptake</span><strong>${Number(material.max_uptake_kgkg).toFixed(2)} kg/kg</strong></div>
          <div><span>RH50</span><strong>${Number(material.rh50_percent).toFixed(0)}%</strong></div>
          <div><span>Regen target</span><strong>${cToF(Number(material.regen_temp_c)).toFixed(0)} F</strong></div>
          <div><span>Evidence</span><strong>${evidenceQualityFromScore(material.evidence_score)}</strong></div>
        </div>
        ${isReal
          ? `<p class="library-card-source">${material.n_isotherms} isotherm(s) &middot; ${material.n_papers} paper(s)${dois.length ? ": " + dois.map(doi => `<a href="https://doi.org/${escapeHtml(doi)}" target="_blank" rel="noopener">${escapeHtml(doi)}</a>`).join(", ") : ""}</p>`
          : `<p class="library-card-source library-card-source-warn">No NIST isotherm on file -- descriptor curve is an unverified placeholder.</p>`
        }
        <p class="library-card-note">${escapeHtml(material.notes || "")}</p>
      </div>
    </article>
  `;
}

async function renderMaterialLibrary() {
  const grid = $("#library-grid");
  if (materialLibraryCache) {
    grid.innerHTML = materialLibraryCache.map(materialLibraryCard).join("");
    return;
  }
  grid.innerHTML = `<p class="library-loading">Loading material library...</p>`;
  try {
    const response = await fetch("/api/materials");
    const result = await response.json();
    materialLibraryCache = result.materials || [];
    grid.innerHTML = materialLibraryCache.map(materialLibraryCard).join("");
  } catch (error) {
    grid.innerHTML = `<p class="library-loading">Could not load the material library.</p>`;
  }
}

// ---------- Sidebar actions: download / share / nav ----------

function buildReportPdf(data) {
  const top = data.top;
  const scenario = data.scenario;
  const doc = new window.jspdf.jsPDF({ unit: "pt", format: "letter" });
  const pageWidth = doc.internal.pageSize.getWidth();
  const pageHeight = doc.internal.pageSize.getHeight();
  const margin = 54;
  const maxWidth = pageWidth - margin * 2;
  let y = 96;

  function ensureSpace(height) {
    if (y + height > pageHeight - margin) {
      doc.addPage();
      y = margin;
    }
  }
  function heading(text) {
    ensureSpace(24);
    doc.setFont("helvetica", "bold"); doc.setFontSize(12.5); doc.setTextColor(0, 31, 63);
    doc.text(text, margin, y);
    y += 18;
  }
  function body(text, opts = {}) {
    doc.setFont("helvetica", opts.bold ? "bold" : "normal");
    doc.setFontSize(opts.size || 10);
    const color = opts.color || [40, 50, 60];
    doc.setTextColor(color[0], color[1], color[2]);
    doc.splitTextToSize(text, maxWidth - (opts.indent || 0)).forEach(line => {
      ensureSpace(14);
      doc.text(line, margin + (opts.indent || 0), y);
      y += opts.lineHeight || 13;
    });
  }
  function spacer(h = 8) { y += h; }
  function rule() {
    ensureSpace(10);
    doc.setDrawColor(215, 227, 234);
    doc.line(margin, y, pageWidth - margin, y);
    y += 12;
  }

  doc.setFillColor(0, 31, 63);
  doc.rect(0, 0, pageWidth, 72, "F");
  doc.setTextColor(255, 255, 255);
  doc.setFont("helvetica", "bold"); doc.setFontSize(18);
  doc.text("AirWater AI", margin, 34);
  doc.setFont("helvetica", "normal"); doc.setFontSize(10.5);
  doc.text("Run report — research-screening estimate, not certified device performance.", margin, 52);

  heading("Scenario");
  body(`Location: ${scenario.location} (${Number(scenario.latitude).toFixed(2)}, ${Number(scenario.longitude).toFixed(2)})`);
  body(`When: ${scenario.date || scenario.month_name}`);
  body(`Daily demand: ${Number(scenario.target_liters_day).toFixed(1)} L/day     MOF mass: ${Number(scenario.mass_kg).toFixed(0)} kg     Heat source: ${scenario.energy_source}     Max regen temp: ${cToF(Number(scenario.max_regen_temp_c)).toFixed(0)} F`);
  body(`Climate data: ${data.climate_source}`);
  spacer();

  heading("Recommendation");
  body(`${top.short_name}  —  ${top.estimated_range}  —  ${top.confidence} confidence`, { bold: true, size: 12 });
  body(`Verdict: ${top.meets_target ? "Meets target" : "Below target"}`);
  spacer();

  heading("Screening decision");
  const isViable = data.decision === "VIABLE";
  body(isViable ? "VIABLE" : "DO NOT DEPLOY", { bold: true, size: 11, color: isViable ? [30, 138, 95] : [193, 67, 46] });
  (data.decision_reasons.length ? data.decision_reasons : ["All refusal checks passed."]).forEach(reason => body(`– ${reason}`, { indent: 10 }));
  spacer();

  heading("Candidates considered");
  data.candidates.forEach((row, index) => {
    body(`${index + 1}. ${row.short_name}  —  ${row.estimated_range}  —  ${VERDICT_LABELS[row.verdict] || row.verdict}`);
  });
  spacer();

  rule();
  body(data.disclaimer, { size: 8.5, color: [138, 74, 31] });
  body(`Run generated: ${new Date(data.generated_at_unix * 1000).toISOString()}`, { size: 8, color: [150, 160, 170] });

  return doc;
}

function downloadReport() {
  if (!lastResult) return;
  const doc = buildReportPdf(lastResult);
  doc.save(`airwater-report-${lastResult.top.short_name.toLowerCase().replaceAll(" ", "-")}.pdf`);
}

function showPanel(nav) {
  $$(".sidebar-link").forEach(item => item.classList.toggle("active", item.dataset.nav === nav));
  $("#console").hidden = nav !== "analysis";
  $("#data-assumptions-panel").hidden = nav !== "data";
  $("#material-library-panel").hidden = nav !== "library";
  if (nav === "data") renderAssumptionsPanel();
  if (nav === "library") renderMaterialLibrary();
}

function bindSidebarNav() {
  $$(".sidebar-link").forEach(button => {
    button.addEventListener("click", () => showPanel(button.dataset.nav));
  });
  $("#close-assumptions").addEventListener("click", () => showPanel("analysis"));
  $("#close-library").addEventListener("click", () => showPanel("analysis"));
  $("#edit-run").addEventListener("click", () => {
    showPanel("analysis");
    $("#scenario-form").scrollIntoView({ behavior: "smooth", block: "start" });
    $("#location").focus();
  });
  $("#download-report").addEventListener("click", downloadReport);
}

// ---------- Orchestration ----------

function renderAll(data) {
  lastResult = data;
  renderRecHeader(data.top);
  renderStatRow(data);
  renderWinnerPanel(data);
  renderClimate(data);
  renderSidebarRun(data);
  renderFooter(data);
  renderCompareTab(data);
  renderWhyTab(data);
  renderResponsibleTab(data);
}

async function runAnalysis({ scroll = false } = {}) {
  const payload = readScenario();
  setLoading(true);
  const started = performance.now();
  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || result.error || "Analysis failed.");
    const remaining = 380 - (performance.now() - started);
    if (remaining > 0) await delay(remaining);
    renderAll(result);
    if (scroll) $(".rec-header").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    showToast(error.message || String(error));
  } finally {
    setLoading(false);
  }
}

function activateTab(name) {
  const button = $(`.tab-button[data-tab="${name}"]`);
  if (!button) return;
  $$(".tab-button").forEach(item => item.classList.toggle("active", item === button));
  $$(".tab-panel").forEach(panel => panel.classList.toggle("active", panel.id === `tab-${name}`));
  window.setTimeout(() => {
    ["climate-chart", "candidate-chart", "score-chart", "feature-chart"].forEach(id => {
      const element = document.getElementById(id);
      if (element && element.data) Plotly.Plots.resize(element);
    });
  }, 80);
}

function bindTabs() {
  $$(".tab-button").forEach(button => {
    button.addEventListener("click", () => activateTab(button.dataset.tab));
  });
}

function latestValidNasaDate() {
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - 3);
  return cutoff.toISOString().slice(0, 10);
}

async function initialize() {
  $("#month").innerHTML = monthNames.map((name, index) => `<option value="${index + 1}">${name}</option>`).join("");
  $("#specific-date").max = latestValidNasaDate();
  try {
    const response = await fetch("/api/locations");
    const result = await response.json();
    $("#location").innerHTML = result.locations.map(item => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)}</option>`).join("");
  } catch (error) {
    showToast("Could not load demo locations.");
    return;
  }

  ["#mass", "#regen", "#efficiency", "#alt-cost"].forEach(selector => $(selector).addEventListener("input", updateRangeOutputs));
  $$(".preset-chip").forEach(button => button.addEventListener("click", () => {
    applyPreset(button.dataset.preset);
    runAnalysis({ scroll: false });
  }));
  $("#scenario-form").addEventListener("submit", event => {
    event.preventDefault();
    $$(".preset-chip").forEach(button => button.classList.remove("active"));
    runAnalysis({ scroll: false });
  });
  bindTabs();
  bindLocationSearch();
  bindSidebarNav();
  applyPreset("humid");
  await runAnalysis({ scroll: false });
}

document.addEventListener("DOMContentLoaded", initialize);
