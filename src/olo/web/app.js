import { marked } from "/vendor/marked.esm.js";
import DOMPurify from "/vendor/purify.es.mjs";

const ui = {
  connection: document.querySelector("#connection"),
  updatedAt: document.querySelector("#updated-at"),
  projectName: document.querySelector("#project-name"),
  targetName: document.querySelector("#target-name"),
  bestScore: document.querySelector("#best-score"),
  bestExperiment: document.querySelector("#best-experiment"),
  modeStatus: document.querySelector("#mode-status"),
  stallStatus: document.querySelector("#stall-status"),
  experimentCount: document.querySelector("#experiment-count"),
  experimentCounts: document.querySelector("#experiment-counts"),
  chart: document.querySelector("#experiment-chart"),
  chartEmpty: document.querySelector("#chart-empty"),
  frontierList: document.querySelector("#frontier-list"),
  strategyName: document.querySelector("#strategy-name"),
  ledger: document.querySelector("#experiment-ledger"),
  ledgerEmpty: document.querySelector("#ledger-empty"),
  proposalList: document.querySelector("#proposal-list"),
  annotationList: document.querySelector("#annotation-list"),
  dialog: document.querySelector("#detail-dialog"),
  detailTitle: document.querySelector("#detail-title"),
  detailBody: document.querySelector("#detail-body"),
  detailClose: document.querySelector("#detail-close"),
  version: document.querySelector("#evaluation-version"),
  direction: document.querySelector("#metric-direction"),
  search: document.querySelector("#experiment-search"),
  filter: document.querySelector("#status-filter"),
  summary: document.querySelector("#summary-dialog"),
  summaryOpen: document.querySelector("#summary-open"),
  summaryClose: document.querySelector("#summary-close"),
  summaryContent: document.querySelector("#summary-content"),
  summaryState: document.querySelector("#summary-state"),
};

let latestState = null;
let stateSignature = "";
let selectedExperiment = null;

function stateLabel(value) {
  return ({ committed: "approved", "pending-review": "awaiting review", retained: "retained", invalid: "invalidated" })[value] || String(value || "pending").replaceAll("-", " ");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatScore(value) {
  if (value === null || value === undefined) return "--";
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return Math.abs(number) >= 100 ? number.toFixed(1) : number.toFixed(4);
}

function formatCounts(counts) {
  const entries = Object.entries(counts || {});
  return entries.length
    ? entries.map(([key, value]) => `${value} ${stateLabel(key)}`).join(" / ")
    : "none yet";
}

function scoreDelta(node, nodes) {
  const parent = nodes[node.parent];
  if (!parent || node.score == null || parent.score == null) return null;
  return Number(node.score) - Number(parent.score);
}

function deltaClass(delta, metric) {
  if (delta == null || delta === 0) return "";
  const improved = metric === "min" ? delta < 0 : delta > 0;
  return improved ? "delta-up" : "delta-down";
}

function bestPath(nodes, bestId) {
  const path = new Set();
  let current = nodes[bestId];
  while (current && current.id !== "root") {
    path.add(current.id);
    current = nodes[current.parent];
  }
  return path;
}

function renderSummary(data) {
  const status = data.status;
  const mode = status.mode || {};
  ui.projectName.textContent = status.project_name || "Olo workspace";
  ui.targetName.textContent = status.target || "target not configured";
  ui.bestScore.textContent = formatScore(status.best_score);
  ui.bestExperiment.textContent = status.best_experiment || "no baseline";
  ui.modeStatus.textContent =
    stateLabel(mode.status && mode.status !== "idle" ? mode.status : status.phase || "idle");
  ui.stallStatus.textContent =
    `stall ${mode.stall_count || 0} / ${mode.stall_limit || 0}`;
  ui.experimentCount.textContent = String(status.experiments || 0);
  ui.experimentCounts.textContent = formatCounts(status.counts);
  ui.version.textContent = `Evaluation ${data.config.evaluation_version || "legacy"}`;
  ui.direction.textContent = data.config.metric === "min" ? "Lower scores are better" : "Higher scores are better";
}

function svgElement(name, attrs = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attrs)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function experimentStepPath(parentPoint, point) {
  return `M ${parentPoint.x} ${parentPoint.y} H ${point.x} V ${point.y}`;
}

function enhanceSummaryTables(container) {
  for (const table of container.querySelectorAll("table")) {
    const wrapper = document.createElement("div");
    wrapper.className = "summary-table-wrap";
    wrapper.tabIndex = 0;
    wrapper.setAttribute("role", "region");
    wrapper.setAttribute("aria-label", "Scrollable report table");

    const headers = [...table.querySelectorAll("thead th")];
    headers.forEach((header, index) => {
      const column = header.textContent
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "") || `column-${index + 1}`;
      header.scope = "col";
      header.dataset.column = column;
      for (const row of table.querySelectorAll("tbody tr")) {
        if (row.children[index]) row.children[index].dataset.column = column;
      }
    });

    table.before(wrapper);
    wrapper.append(table);
  }
}

function renderChart(data) {
  const nodesById = data.graph.nodes || {};
  const nodes = Object.values(nodesById)
    .filter((node) => node.id !== "root" && node.score != null)
    .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  ui.chart.replaceChildren();
  ui.chartEmpty.hidden = nodes.length > 0;
  if (!nodes.length) return;

  const width = Math.max(ui.chart.parentElement.clientWidth, nodes.length * 58 + 120);
  const height = 310;
  const margin = { top: 38, right: 40, bottom: 52, left: 58 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  ui.chart.setAttribute("viewBox", `0 0 ${width} ${height}`);
  ui.chart.setAttribute("width", width);
  ui.chart.setAttribute("height", height);

  const scores = nodes.map((node) => Number(node.score));
  let min = Math.min(...scores);
  let max = Math.max(...scores);
  if (min === max) {
    min -= 0.1;
    max += 0.1;
  } else {
    const pad = (max - min) * 0.14;
    min -= pad;
    max += pad;
  }

  const x = (index) =>
    margin.left + (nodes.length === 1 ? plotWidth / 2 : index * plotWidth / (nodes.length - 1));
  const y = (score) => margin.top + (data.config.metric === "min" ? Number(score) - min : max - Number(score)) * plotHeight / (max - min);

  for (let tick = 0; tick <= 4; tick += 1) {
    const score = min + (max - min) * tick / 4;
    const yy = y(score);
    ui.chart.append(
      svgElement("line", {
        x1: margin.left,
        x2: width - margin.right,
        y1: yy,
        y2: yy,
        class: "axis-line",
      })
    );
    const label = svgElement("text", {
      x: margin.left - 10,
      y: yy + 3,
      "text-anchor": "end",
      class: "axis-label",
    });
    label.textContent = formatScore(score);
    ui.chart.append(label);
  }

  const pointById = {};
  nodes.forEach((node, index) => {
    pointById[node.id] = { x: x(index), y: y(node.score) };
  });
  const pathIds = bestPath(nodesById, data.status.best_experiment);

  nodes.forEach((node) => {
    const parentPoint = pointById[node.parent];
    const point = pointById[node.id];
    if (!parentPoint) return;
    const edge = svgElement("path", {
      d: experimentStepPath(parentPoint, point),
      class: `branch-line ${pathIds.has(node.id) ? "best" : ""}`,
    });
    ui.chart.append(edge);
  });

  nodes.forEach((node) => {
    for (const donor of node.donors || []) {
      const source = pointById[donor];
      const destination = pointById[node.id];
      if (source) ui.chart.append(svgElement("line", { x1: source.x, y1: source.y, x2: destination.x, y2: destination.y, class: "donor-line" }));
    }
  });

  nodes.forEach((node) => {
    const point = pointById[node.id];
    const group = svgElement("g", {
      class: `experiment-node ${node.status || "pending"} ${node.id === data.status.best_experiment ? "best" : ""}`,
      role: "button",
      tabindex: "0",
      "aria-label": `${node.id}, score ${formatScore(node.score)}, ${node.status}`,
    });
    group.append(svgElement("circle", { cx: point.x, cy: point.y, r: 7 }));
    const tooltip = svgElement("title");
    tooltip.textContent = `${node.id}: ${stateLabel(node.status)}. ${node.hypothesis}`;
    group.append(tooltip);
    const label = svgElement("text", {
      x: point.x,
      y: point.y - 15,
      "text-anchor": "middle",
    });
    label.textContent = node.id.replace("exp_", "");
    group.append(label);
    group.addEventListener("click", () => openDetail(node.id));
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openDetail(node.id);
      }
    });
    ui.chart.append(group);
  });
}

function renderFrontier(data) {
  const frontier = data.frontier || { strategy: {}, picks: [] };
  ui.strategyName.textContent = frontier.strategy?.kind || "--";
  ui.frontierList.innerHTML = "";
  if (!frontier.picks?.length) {
    ui.frontierList.innerHTML =
      '<div class="empty-state small">No approved parents.</div>';
    return;
  }
  for (const item of frontier.picks) {
    const card = document.createElement("article");
    card.className = "frontier-card";
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.innerHTML = `
      <span class="frontier-rank">${escapeHtml(item.rank)}</span>
      <div>
        <strong>${escapeHtml(item.id)} / ${escapeHtml(formatScore(item.score))}</strong>
        <p>${escapeHtml(item.hypothesis)}</p>
        <small>${escapeHtml(stateLabel(data.graph.nodes[item.id]?.status))} / ${escapeHtml(item.reason)}</small>
      </div>
    `;
    card.addEventListener("click", () => openDetail(item.id));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openDetail(item.id); }
    });
    ui.frontierList.append(card);
  }
}

function renderLedger(data) {
  const nodesById = data.graph.nodes || {};
  const query = ui.search.value.trim().toLowerCase();
  const nodes = Object.values(nodesById)
    .filter((node) => node.id !== "root")
    .filter((node) => !ui.filter.value || node.status === ui.filter.value)
    .filter((node) => /^exp_\d+$/.test(query) ? node.id === query : `${node.id} ${node.hypothesis} ${node.parent} ${(node.donors || []).join(" ")}`.toLowerCase().includes(query))
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  ui.ledger.replaceChildren();
  ui.ledgerEmpty.hidden = nodes.length > 0;
  for (const node of nodes) {
    const delta = scoreDelta(node, nodesById);
    const row = document.createElement("tr");
    row.innerHTML = `
      <td class="mono-cell"><button class="row-link" type="button" aria-label="View ${escapeHtml(node.id)}">${escapeHtml(node.id)}</button></td>
      <td><span class="status-tag ${escapeHtml(node.status)}">${escapeHtml(stateLabel(node.status))}</span></td>
      <td class="mono-cell">${escapeHtml(formatScore(node.score))}</td>
      <td class="mono-cell ${deltaClass(delta, data.status.metric)}">${delta == null ? "--" : `${delta >= 0 ? "+" : ""}${delta.toFixed(4)}`}</td>
      <td class="mono-cell">${escapeHtml(node.parent)}${node.donors?.length ? `<small>donors: ${escapeHtml(node.donors.join(", "))}</small>` : ""}</td>
      <td class="mono-cell evidence-count">${node.attempts || 0} eval / ${node.probe_count || 0} probe${node.preflight_count ? `<small>${node.preflight_count} blocked</small>` : ""}</td>
      <td class="hypothesis-cell"><span class="hypothesis-preview">${escapeHtml(node.hypothesis)}</span></td>
    `;
    row.addEventListener("click", () => openDetail(node.id));
    ui.ledger.append(row);
  }
}

function renderNotes(container, values, kind) {
  container.replaceChildren();
  if (!values.length) {
    container.innerHTML =
      `<div class="empty-state small">No ${kind} recorded yet.</div>`;
    return;
  }
  for (const item of (kind === "proposals" ? values.slice().reverse() : values).slice(0, 8)) {
    const card = document.createElement("article");
    card.className = "note-card";
    if (kind === "proposals") {
      card.innerHTML = `
        <strong>${escapeHtml(item.title)}</strong>
        <p>${escapeHtml(item.hypothesis)}</p>
        <small>${escapeHtml(item.status || "proposed")} / ${escapeHtml(item.source)} / confidence ${escapeHtml(item.confidence)}</small>
      `;
    } else {
      card.innerHTML = `
        <strong>${escapeHtml(item.experiment_id || "workspace")}</strong>
        <p>${escapeHtml(item.text)}</p>
        <small>${escapeHtml(item.payload?.kind || item.type || "note")}${item.task_id ? ` / task ${escapeHtml(item.task_id)}` : ""}${item.payload?.tags?.length ? ` / ${escapeHtml(item.payload.tags.join(", "))}` : ""}</small>
      `;
    }
    container.append(card);
  }
}

async function openDetail(expId) {
  selectedExperiment = expId;
  ui.detailTitle.textContent = expId;
  ui.detailBody.innerHTML = '<div class="empty-state small">Loading evidence...</div>';
  if (!ui.dialog.open) ui.dialog.showModal();
  try {
    const response = await fetch(`/api/experiment/${encodeURIComponent(expId)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const detail = await response.json();
    if (selectedExperiment !== expId || !ui.dialog.open) return;
    const node = detail.node;
    const outcome = detail.outcome || {};
    const gates = (outcome.gate_results || [])
      .map((gate) => `${gate.name}: ${gate.passed ? "pass" : "fail"}`)
      .join(", ") || "none";
    const annotations = (detail.annotations || [])
      .filter((item) => item.type !== "verification")
      .map((item) => `<li>${escapeHtml(item.text)}</li>`)
      .join("") || "<li>No annotations.</li>";
    const changes = outcome.task_changes || {};
    const deltas = Object.entries(changes.deltas || {}).map(([task, delta]) => `<tr><td>${escapeHtml(task)}</td><td class="${delta > 0 ? "delta-up" : delta < 0 ? "delta-down" : ""}">${delta > 0 ? "+" : ""}${formatScore(delta)}</td></tr>`).join("");
    const sources = Object.entries(outcome.source_comparison || {}).map(([source, comparison]) => `<li>${escapeHtml(source)}: gain ${escapeHtml(formatScore(comparison.gain))}; ${comparison.task_changes?.improved?.length || 0} improved, ${comparison.task_changes?.regressed?.length || 0} regressed tasks.</li>`).join("");
    const records = (detail.records || []).map((record) => `<details><summary>${escapeHtml(record.kind)} / ${escapeHtml(record.status)} / ${escapeHtml(formatScore(record.score))}</summary><ul class="artifact-list">${record.artifacts.map((path) => `<li><a href="/api/artifact/${path.split("/").map(encodeURIComponent).join("/")}" target="_blank" rel="noopener">${escapeHtml(path)}</a></li>`).join("")}</ul></details>`).join("");
    const findings = (outcome.verification?.findings || []).map((finding) => `<li><strong>${escapeHtml(finding.severity)}</strong>: ${escapeHtml(finding.what)} (${escapeHtml(finding.where)})</li>`).join("");
    ui.detailBody.innerHTML = `
      <div class="detail-grid">
        <div class="detail-stat"><span>Status</span><strong>${escapeHtml(stateLabel(node.status))}</strong></div>
        <div class="detail-stat"><span>Latest measured score</span><strong>${escapeHtml(formatScore(outcome.score ?? node.score))}</strong></div>
        <div class="detail-stat"><span>Parent</span><strong>${escapeHtml(node.parent)}</strong></div>
        <div class="detail-stat"><span>Version</span><strong>${escapeHtml(outcome.evaluation_version || node.evaluation_version || "legacy")}</strong></div>
      </div>
      <section class="detail-section">
        <h3>Hypothesis</h3>
        <p>${escapeHtml(node.hypothesis)}</p>
      </section>
      <section class="detail-section">
        <h3>Review and gates</h3>
        <p>${escapeHtml(node.review ? `${node.review.reviewer}: ${node.review.reason}` : "No binding review recorded.")}</p>
        <p>${escapeHtml(gates)}</p><ul>${findings}</ul>
      </section>
      <section class="detail-section">
        <h3>Per-task changes</h3>
        <p>${(changes.improved || []).length} improved / ${(changes.regressed || []).length} regressed / ${(changes.missing || []).length} missing</p>
        ${deltas ? `<table class="task-table"><thead><tr><th>Task</th><th>Directional gain</th></tr></thead><tbody>${deltas}</tbody></table>` : ""}
        ${sources ? `<h3>Source comparisons</h3><ul>${sources}</ul>` : ""}
      </section>
      <section class="detail-section">
        <h3>Research notes</h3>
        <ul>${annotations}</ul>
      </section>
      <section class="detail-section"><h3>Evidence files</h3>${records || "No saved records."}</section>
      <section class="detail-section">
        <h3>Recorded diff</h3>
        <pre>${escapeHtml(detail.diff || "No diff recorded.")}</pre>
      </section>
      <section class="detail-section">
        <h3>Benchmark output</h3>
        <pre>${escapeHtml(detail.benchmark_stdout || detail.benchmark_stderr || "No benchmark log.")}</pre>
      </section>
    `;
  } catch (error) {
    ui.detailBody.innerHTML =
      `<div class="empty-state small">Could not load evidence: ${escapeHtml(error.message)}</div>`;
  }
}

async function refresh() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const signature = JSON.stringify(data);
    if (signature !== stateSignature) {
      stateSignature = signature;
      latestState = data;
      renderSummary(data);
      renderChart(data);
      renderFrontier(data);
      renderLedger(data);
      renderNotes(ui.proposalList, data.proposals || [], "proposals");
      renderNotes(ui.annotationList, data.learnings || [], "learnings");
    }
    ui.connection.textContent = "live";
    ui.connection.classList.add("live");
    ui.updatedAt.textContent = new Date().toLocaleTimeString();
  } catch (error) {
    ui.connection.textContent = "offline";
    ui.connection.classList.remove("live");
    ui.connection.title = error.message;
  } finally {
    setTimeout(refresh, 3000);
  }
}

async function openSummary() {
  ui.summaryContent.textContent = "Loading summary...";
  if (!ui.summary.open) ui.summary.showModal();
  try {
    const response = await fetch("/api/report", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const report = await response.json();
    ui.summaryState.textContent = stateLabel(report.phase || "Research report");
    const content = DOMPurify.sanitize(marked.parse(report.markdown), {
      RETURN_DOM_FRAGMENT: true, FORBID_TAGS: ["img", "style", "iframe", "form"], FORBID_ATTR: ["style"],
    });
    ui.summaryContent.replaceChildren(content);
    enhanceSummaryTables(ui.summaryContent);
    for (const link of ui.summaryContent.querySelectorAll("a")) {
      const path = link.getAttribute("href") || "";
      if (path.startsWith("experiments/") || path.startsWith("final-test/")) link.href = `/api/artifact/${path.split("/").map(encodeURIComponent).join("/")}`;
      else if (path === "measurement.json") link.href = "/api/measurement";
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
  } catch (error) {
    ui.summaryContent.textContent = `Could not load summary: ${error.message}`;
  }
}

ui.detailClose.addEventListener("click", () => ui.dialog.close());
ui.dialog.addEventListener("click", (event) => {
  if (event.target === ui.dialog) ui.dialog.close();
});
ui.summaryOpen.addEventListener("click", openSummary);
ui.summaryClose.addEventListener("click", () => ui.summary.close());
ui.summary.addEventListener("click", (event) => { if (event.target === ui.summary) ui.summary.close(); });
ui.search.addEventListener("input", () => { if (latestState) renderLedger(latestState); });
ui.filter.addEventListener("change", () => { if (latestState) renderLedger(latestState); });
window.addEventListener("resize", () => {
  if (latestState) renderChart(latestState);
});

refresh();
