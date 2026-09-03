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
};

let latestState = null;

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
    ? entries.map(([key, value]) => `${value} ${key}`).join(" / ")
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
    mode.status && mode.status !== "idle" ? mode.status : status.phase || "idle";
  ui.stallStatus.textContent =
    `stall ${mode.stall_count || 0} / ${mode.stall_limit || 0}`;
  ui.experimentCount.textContent = String(status.experiments || 0);
  ui.experimentCounts.textContent = formatCounts(status.counts);
}

function svgElement(name, attrs = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attrs)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function renderChart(data) {
  const nodesById = data.graph.nodes || {};
  const nodes = Object.values(nodesById)
    .filter((node) => node.id !== "root" && node.score != null)
    .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  ui.chart.replaceChildren();
  ui.chartEmpty.hidden = nodes.length > 0;
  if (!nodes.length) return;

  const width = Math.max(ui.chart.clientWidth || 800, 500);
  const height = Math.max(ui.chart.clientHeight || 410, 330);
  const margin = { top: 38, right: 40, bottom: 52, left: 58 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  ui.chart.setAttribute("viewBox", `0 0 ${width} ${height}`);

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
  const y = (score) =>
    margin.top + (max - Number(score)) * plotHeight / (max - min);

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
    const control = (parentPoint.x + point.x) / 2;
    const edge = svgElement("path", {
      d: `M ${parentPoint.x} ${parentPoint.y} C ${control} ${parentPoint.y}, ${control} ${point.y}, ${point.x} ${point.y}`,
      class: `branch-line ${pathIds.has(node.id) ? "best" : ""}`,
    });
    ui.chart.append(edge);
  });

  nodes.forEach((node) => {
    const point = pointById[node.id];
    const group = svgElement("g", {
      class: `experiment-node ${node.status || "pending"} ${node.id === data.status.best_experiment ? "best" : ""}`,
      role: "button",
      tabindex: "0",
      "aria-label": `${node.id}, score ${formatScore(node.score)}, ${node.status}`,
    });
    group.append(svgElement("circle", { cx: point.x, cy: point.y, r: 8 }));
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
      '<div class="empty-state small">Keep a valid baseline to open the frontier.</div>';
    return;
  }
  for (const item of frontier.picks) {
    const card = document.createElement("article");
    card.className = "frontier-card";
    card.tabIndex = 0;
    card.innerHTML = `
      <span class="frontier-rank">${escapeHtml(item.rank)}</span>
      <div>
        <strong>${escapeHtml(item.id)} / ${escapeHtml(formatScore(item.score))}</strong>
        <p>${escapeHtml(item.hypothesis)}</p>
        <small>${escapeHtml(item.reason)}</small>
      </div>
    `;
    card.addEventListener("click", () => openDetail(item.id));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter") openDetail(item.id);
    });
    ui.frontierList.append(card);
  }
}

function renderLedger(data) {
  const nodesById = data.graph.nodes || {};
  const nodes = Object.values(nodesById)
    .filter((node) => node.id !== "root")
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  ui.ledger.replaceChildren();
  ui.ledgerEmpty.hidden = nodes.length > 0;
  for (const node of nodes) {
    const delta = scoreDelta(node, nodesById);
    const row = document.createElement("tr");
    row.tabIndex = 0;
    row.innerHTML = `
      <td class="mono-cell">${escapeHtml(node.id)}</td>
      <td><span class="status-tag ${escapeHtml(node.status)}">${escapeHtml(node.status)}</span></td>
      <td class="mono-cell">${escapeHtml(formatScore(node.score))}</td>
      <td class="mono-cell ${deltaClass(delta, data.status.metric)}">${delta == null ? "--" : `${delta >= 0 ? "+" : ""}${delta.toFixed(4)}`}</td>
      <td class="mono-cell">${escapeHtml(node.parent)}</td>
      <td>${escapeHtml(node.hypothesis)}</td>
    `;
    row.addEventListener("click", () => openDetail(node.id));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter") openDetail(node.id);
    });
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
  for (const item of values.slice().reverse().slice(0, 8)) {
    const card = document.createElement("article");
    card.className = "note-card";
    if (kind === "proposals") {
      card.innerHTML = `
        <strong>${escapeHtml(item.title)}</strong>
        <p>${escapeHtml(item.hypothesis)}</p>
        <small>${escapeHtml(item.source)} / confidence ${escapeHtml(item.confidence)}</small>
      `;
    } else {
      card.innerHTML = `
        <strong>${escapeHtml(item.experiment_id || "workspace")}</strong>
        <p>${escapeHtml(item.text)}</p>
        <small>${escapeHtml(item.type || "note")}${item.task_id ? ` / task ${escapeHtml(item.task_id)}` : ""}</small>
      `;
    }
    container.append(card);
  }
}

async function openDetail(expId) {
  ui.detailTitle.textContent = expId;
  ui.detailBody.innerHTML = '<div class="empty-state small">Loading evidence...</div>';
  if (!ui.dialog.open) ui.dialog.showModal();
  try {
    const response = await fetch(`/api/experiment/${encodeURIComponent(expId)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const detail = await response.json();
    const node = detail.node;
    const outcome = detail.outcome || {};
    const gates = (outcome.gate_results || [])
      .map((gate) => `${gate.name}: ${gate.passed ? "pass" : "fail"}`)
      .join(", ") || "none";
    const annotations = (detail.annotations || [])
      .map((item) => `<li>${escapeHtml(item.text)}</li>`)
      .join("") || "<li>No annotations.</li>";
    ui.detailBody.innerHTML = `
      <div class="detail-grid">
        <div class="detail-stat"><span>Status</span><strong>${escapeHtml(node.status)}</strong></div>
        <div class="detail-stat"><span>Score</span><strong>${escapeHtml(formatScore(node.score))}</strong></div>
        <div class="detail-stat"><span>Parent</span><strong>${escapeHtml(node.parent)}</strong></div>
        <div class="detail-stat"><span>Gates</span><strong>${escapeHtml(gates)}</strong></div>
      </div>
      <section class="detail-section">
        <h3>Hypothesis</h3>
        <p>${escapeHtml(node.hypothesis)}</p>
      </section>
      <section class="detail-section">
        <h3>Verification and learnings</h3>
        <ul>${annotations}</ul>
      </section>
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
    latestState = await response.json();
    renderSummary(latestState);
    renderChart(latestState);
    renderFrontier(latestState);
    renderLedger(latestState);
    renderNotes(ui.proposalList, latestState.proposals || [], "proposals");
    renderNotes(ui.annotationList, latestState.annotations || [], "learnings");
    ui.connection.textContent = "live";
    ui.connection.classList.add("live");
    ui.updatedAt.textContent = new Date().toLocaleTimeString();
  } catch (error) {
    ui.connection.textContent = "offline";
    ui.connection.classList.remove("live");
    console.error(error);
  }
}

ui.detailClose.addEventListener("click", () => ui.dialog.close());
ui.dialog.addEventListener("click", (event) => {
  if (event.target === ui.dialog) ui.dialog.close();
});
window.addEventListener("resize", () => {
  if (latestState) renderChart(latestState);
});

refresh();
setInterval(refresh, 2000);
