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
  $("#mass").value = String(scenario.mass_kg);
  $("#target").value = String(scenario.target_liters_day);
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
    data_source: $("#data-source").value
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

function renderRecommendation(data) {
  const top = data.top;
  const scenario = data.scenario;
  const summary = data.climate_summary;
  const coverage = Number(top.target_coverage_percent || 0);

  const whenLabel = scenario.date ? `on ${scenario.date}` : `in ${scenario.month_name}`;
  $("#result-context").textContent = `${scenario.location} ${whenLabel}, using ${scenario.energy_source.toLowerCase()}. ${top.short_name} best balances climate fit, regeneration constraints, evidence quality, and simulated daily yield.`;

  $("#console-material").textContent = top.short_name;
  $("#console-confidence").textContent = `${top.confidence} confidence`;
  $("#console-adsorb").textContent = circularWindow(summary.capture_hours);
  $("#console-release").textContent = circularWindow(summary.release_hours);
  const yieldValue = $("#console-yield");
  yieldValue.textContent = `${top.estimated_range} (target ${Number(scenario.target_liters_day).toFixed(1)} L/day, ${coverage.toFixed(0)}%)`;
  yieldValue.classList.toggle("meets-target", coverage >= 100);

  $("#metric-grid").innerHTML = [
    metricCard("Best capture humidity", `${Number(top.adsorption_rh_percent).toFixed(0)}% RH`, "Average across the selected capture window"),
    metricCard("Best capture temperature", `${cToF(Number(top.adsorption_temp_c)).toFixed(1)} F`, "Lower temperature generally favors adsorption"),
    metricCard("Equivalent cycles/day", `${Number(top.cycles_day).toFixed(0)}`, "Simplified cycle estimate for comparison"),
    metricCard("Regeneration margin", `${Number(top.regen_margin_c) >= 0 ? "+" : ""}${cDeltaToF(Number(top.regen_margin_c)).toFixed(0)} F`, "User heat limit minus material target")
  ].join("");

  const reasons = [
    ["Climate match", `Its uptake transition is compatible with the capture window near ${Number(top.adsorption_rh_percent).toFixed(0)}% relative humidity.`],
    ["Heat feasibility", `Its ${cToF(Number(top.regen_temp_c)).toFixed(0)} F regeneration target is checked against the user limit of ${cToF(Number(scenario.max_regen_temp_c)).toFixed(0)} F.`],
    ["Evidence-aware ranking", `The score includes an evidence rating of ${Math.round(Number(top.evidence_score) * 100)}% and water-stability rating of ${Math.round(Number(top.water_stability_score) * 100)}%.`]
  ];
  $("#reason-grid").innerHTML = reasons.map((reason, index) => `
    <article class="reason"><b>${index + 1}</b><strong>${escapeHtml(reason[0])}</strong><p>${escapeHtml(reason[1])}</p></article>
  `).join("");
  $("#disclaimer").textContent = data.disclaimer;

  $("#console-reasons").innerHTML = reasons.map(reason => `
    <li><b>${escapeHtml(reason[0])}</b>${escapeHtml(reason[1])}</li>
  `).join("");

  $("#console-candidates").innerHTML = data.ranking.slice(1, 4).map(row => `
    <li><b>${escapeHtml(row.short_name)}</b><span>${escapeHtml(row.estimated_range)} &middot; ${escapeHtml(row.confidence)}</span></li>
  `).join("");
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

  const archetypeBadge = $("#climate-archetype-badge");
  const archetypeLabel = archetypeLabels[data.climate_archetype_name] || data.climate_archetype_name || "";
  const isAtypical = Number(data.ood_distance) > 1.8;
  archetypeBadge.textContent = archetypeLabel ? `${archetypeLabel}${isAtypical ? " (atypical)" : ""}` : "";
  archetypeBadge.classList.toggle("atypical", isAtypical);

  $("#climate-summary").innerHTML = `
    <h4>Climate snapshot</h4>
    <div class="summary-list">
      <div class="summary-item"><span>Daily average humidity</span><strong>${Number(summary.average_humidity_percent).toFixed(0)}%</strong></div>
      <div class="summary-item"><span>Daily average temperature</span><strong>${cToF(Number(summary.average_temperature_c)).toFixed(1)} F</strong></div>
      <div class="summary-item"><span>Peak solar availability</span><strong>${Number(summary.peak_solar_w_m2).toFixed(0)} W/m2</strong></div>
    </div>
    <span class="source-badge">${escapeHtml(data.climate_source)}</span>
  `;

  $("#timeline").innerHTML = schedule.map(row => {
    const className = row.action === "Capture" ? "capture" : row.action === "Release + condense" ? "release" : "idle";
    const title = `${String(row.hour).padStart(2, "0")}:00 - ${row.action}; ${cToF(Number(row.temperature_c)).toFixed(1)} F; RH ${Number(row.relative_humidity_percent).toFixed(1)}%; solar ${Number(row.solar_w_m2).toFixed(0)} W/m2`;
    return `<div class="timeline-cell ${className}" data-hour="${String(row.hour).padStart(2, "0")}" title="${escapeHtml(title)}"></div>`;
  }).join("");
}

function renderCandidateCards(ranking) {
  $("#candidate-cards").innerHTML = ranking.slice(0, 3).map((row, index) => `
    <article class="candidate ${index === 0 ? "top" : ""}">
      <span class="candidate-rank">#${index + 1}</span>
      <h4>${escapeHtml(row.short_name)}</h4>
      <div class="candidate-yield">${escapeHtml(row.estimated_range)}</div>
      <div class="candidate-meta">Working capacity: ${Number(row.predicted_working_capacity_kgkg).toFixed(3)} kg/kg</div>
      <div class="candidate-meta">Regeneration target: ${cToF(Number(row.regen_temp_c)).toFixed(0)} F</div>
      <div class="candidate-meta">Evidence score: ${Math.round(Number(row.evidence_score) * 100)}%</div>
      <span class="candidate-pill">${escapeHtml(row.confidence)} confidence | ${row.meets_target ? "Meets target" : "Below target"}</span>
    </article>
  `).join("");
}

function renderCandidateChart(ranking) {
  const yields = ranking.map(row => Number(row.estimated_liters_day));
  const sizes = yields.map(value => clamp(value * 7 + 18, 20, 62));
  const trace = {
    x: ranking.map(row => cToF(Number(row.regen_temp_c))),
    y: ranking.map(row => row.predicted_working_capacity_kgkg),
    text: ranking.map(row => row.short_name),
    customdata: ranking.map(row => [row.estimated_liters_day, row.confidence, row.evidence_score, row.limitation]),
    type: "scatter", mode: "markers+text", textposition: "top center",
    marker: {
      size: sizes, color: ranking.map(row => row.score), colorscale: [[0,"#E6E6FA"],[.55,"#007BFF"],[1,"#001F3F"]],
      showscale: true, colorbar: { title: "Rank score", thickness: 12 }, line: { color: "white", width: 1.5 }, opacity: .92
    },
    hovertemplate: "<b>%{text}</b><br>Working capacity %{y:.3f} kg/kg<br>Regeneration %{x:.0f} F<br>Yield %{customdata[0]:.2f} L/day<br>%{customdata[1]} confidence<br>Evidence %{customdata[2]:.0%}<br>%{customdata[3]}<extra></extra>"
  };
  const layout = {
    height: 385, margin: { l: 55, r: 60, t: 25, b: 50 }, paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(250,252,254,.78)",
    xaxis: { title: "Regeneration target (F)", gridcolor: "#e4edf1", zeroline: false },
    yaxis: { title: "Predicted working capacity (kg/kg)", gridcolor: "#e4edf1", zeroline: false },
    font: { family: "Inter, system-ui, sans-serif", color: "#0b2d47", size: 11 }
  };
  Plotly.react("candidate-chart", [trace], layout, { displayModeBar: false, responsive: true });
}

function renderCandidateDetail(ranking, selectedName) {
  const row = ranking.find(item => item.short_name === selectedName) || ranking[0];
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
    <div class="limitation"><strong>Main limitation:</strong> ${escapeHtml(row.limitation)}</div>
  `;
}

function renderRankingTable(ranking) {
  const headers = ["Rank", "MOF", "Yield", "Coverage", "Capacity", "Regen", "Evidence", "Confidence"];
  const body = ranking.map((row, index) => `
    <tr><td>${index + 1}</td><td><strong>${escapeHtml(row.short_name)}</strong></td><td>${escapeHtml(row.estimated_range)}</td>
    <td>${Number(row.target_coverage_percent).toFixed(0)}%</td><td>${Number(row.predicted_working_capacity_kgkg).toFixed(3)}</td>
    <td>${cToF(Number(row.regen_temp_c)).toFixed(0)} F</td><td>${Math.round(Number(row.evidence_score) * 100)}%</td><td>${escapeHtml(row.confidence)}</td></tr>
  `).join("");
  $("#ranking-table").innerHTML = `<thead><tr>${headers.map(header => `<th>${header}</th>`).join("")}</tr></thead><tbody>${body}</tbody>`;
}

function renderCandidates(data) {
  const ranking = data.ranking;
  renderCandidateCards(ranking);
  renderCandidateChart(ranking);
  const select = $("#candidate-select");
  select.innerHTML = ranking.map(row => `<option value="${escapeHtml(row.short_name)}">${escapeHtml(row.short_name)}</option>`).join("");
  select.value = ranking[0].short_name;
  renderCandidateDetail(ranking, select.value);
  select.onchange = () => renderCandidateDetail(ranking, select.value);
  renderRankingTable(ranking);
}

function renderFeatureChart(featureImportance) {
  const values = [...featureImportance].sort((a, b) => Number(a.importance) - Number(b.importance)).slice(-8);
  const labels = values.map(row => String(row.feature).replaceAll("_", " "));
  const trace = {
    x: values.map(row => row.importance), y: labels, type: "bar", orientation: "h",
    marker: { color: "#007BFF" }, hovertemplate: "%{y}<br>Importance %{x:.3f}<extra></extra>"
  };
  const layout = {
    height: 345, margin: { l: 180, r: 25, t: 20, b: 45 }, paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(250,252,254,.78)",
    xaxis: { title: "Random-forest feature importance", gridcolor: "#e4edf1", zeroline: false },
    yaxis: { automargin: true }, font: { family: "Inter, system-ui, sans-serif", color: "#0b2d47", size: 10 }
  };
  Plotly.react("feature-chart", [trace], layout, { displayModeBar: false, responsive: true });
}

function renderModel(data) {
  const metrics = data.metrics;
  $("#model-metrics").innerHTML = [
    metricCard("Model", "Random forest", "Demonstration uptake regressor"),
    metricCard("Held-out MAE", `${Number(metrics.mae_kgkg || 0).toFixed(3)} kg/kg`, "Mean absolute error"),
    metricCard("Held-out RMSE", `${Number(metrics.rmse_kgkg || 0).toFixed(3)} kg/kg`, "Root mean squared error"),
    metricCard("Held-out R2", Number(metrics.r2 || 0).toFixed(3), "Group split by MOF name")
  ].join("");
  renderFeatureChart(data.feature_importance || []);
  $("#evaluation-card").innerHTML = `
    <h4>Evaluation design</h4>
    <ul class="evaluation-list">
      <li>Training rows<b>${escapeHtml(metrics.training_rows ?? "n/a")}</b></li>
      <li>Test rows<b>${escapeHtml(metrics.test_rows ?? "n/a")}</b></li>
      <li>Split strategy<b>${escapeHtml(metrics.split ?? "n/a")}</b></li>
      <li>Held-out MOFs<b>${escapeHtml((metrics.held_out_mofs || []).join(", ") || "n/a")}</b></li>
      <li>Prediction source<b>${escapeHtml(data.top.model_source)}</b></li>
    </ul>
  `;
}

function renderAll(data) {
  renderRecommendation(data);
  renderClimate(data);
  renderCandidates(data);
  renderModel(data);
  $("#results").style.display = "block";
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
    if (scroll) $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
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
    ["climate-chart", "candidate-chart", "feature-chart"].forEach(id => {
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

function bindConsoleFooter() {
  $$(".ghost-button[data-scroll-tab]").forEach(button => {
    button.addEventListener("click", () => {
      activateTab(button.dataset.scrollTab);
      $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
}

async function initialize() {
  $("#month").innerHTML = monthNames.map((name, index) => `<option value="${index + 1}">${name}</option>`).join("");
  try {
    const response = await fetch("/api/locations");
    const result = await response.json();
    $("#location").innerHTML = result.locations.map(item => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)}</option>`).join("");
  } catch (error) {
    showToast("Could not load demo locations.");
    return;
  }

  ["#mass", "#regen", "#efficiency"].forEach(selector => $(selector).addEventListener("input", updateRangeOutputs));
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
  bindConsoleFooter();
  applyPreset("desert");
  await runAnalysis({ scroll: false });
}

document.addEventListener("DOMContentLoaded", initialize);
