"use strict";

const experiments = {
  baseline: {
    id: "exp_0000",
    parent: null,
    status: "BASELINE",
    title: "Start with a trustworthy measuring stick.",
    description: "Repeat checks on unchanged source. Audit the benchmark and gates. Independent approval freezes the baseline before optimization begins.",
    checks: ["Repeatable checks", "Protected measurement", "Reviewed"],
  },
  approved: {
    id: "exp_0001",
    parent: "baseline",
    status: "APPROVED",
    title: "A first gain becomes a new starting point.",
    description: "One of three parallel hypotheses clears the gain floor, passes its gates, and earns independent review. Round two can build on this exact snapshot.",
    checks: ["Measured gain", "Gates passed", "Reviewed"],
  },
  rejected: {
    id: "exp_0003",
    parent: "baseline",
    status: "NOT PROMOTED",
    title: "Not every direction earns another round.",
    description: "This direction fails a gate. It cannot become an approved parent, but its saved traces still help explain what to avoid next.",
    checks: ["Gate failed", "Evidence preserved", "No promotion"],
  },
  retained: {
    id: "exp_0002",
    parent: "baseline",
    status: "RETAINED",
    title: "Useful does not always mean best overall.",
    description: "This gain is below the study's meaningful-improvement floor, but task-level strengths make it worth retaining after review. Round two extends it instead of abandoning the branch.",
    checks: ["Task-level strengths", "Reviewed", "Reusable parent"],
  },
  refined: {
    id: "exp_0004",
    parent: "approved",
    status: "APPROVED",
    title: "Follow the evidence down a promising branch.",
    description: "A follow-up improves on exp_0001 under the same frozen benchmark. Its approved source becomes the base for a later combination, not an automatic merge.",
    checks: ["Approved parent", "Frozen benchmark", "Reviewed"],
  },
  donor: {
    id: "exp_0005",
    parent: "retained",
    status: "APPROVED DONOR",
    title: "A specialist branch contributes a useful idea.",
    description: "Extending exp_0002 produces an independently approved improvement. It is not the overall leader, but its compatible idea becomes an explicit donor to exp_0007.",
    checks: ["Specialist extended", "Donor recorded", "Reviewed"],
  },
  regressed: {
    id: "exp_0006",
    parent: "approved",
    status: "NOT PROMOTED",
    title: "A good parent does not guarantee a good child.",
    description: "A different follow-up to exp_0001 regresses protected behavior and fails a gate. Its evidence is kept, but this branch cannot become a source for recombination.",
    checks: ["Gate failed", "Lesson preserved", "No promotion"],
  },
  combined: {
    id: "exp_0007",
    parent: "refined",
    donors: ["donor"],
    status: "APPROVED RECOMBINATION",
    title: "Two promising branches. One new experiment.",
    description: "Keep the base's improvement and transfer a compatible idea from the donor. Remeasure the combination, pass the gates, and earn a new independent review. Nothing merges into main.",
    checks: ["Sources recorded", "Combination measured", "Reviewed"],
  },
};

const nodes = document.querySelectorAll("[data-experiment]");
const links = document.querySelectorAll(".tree-link");
const checks = document.getElementById("detail-checks");

function selectExperiment(key) {
  const experiment = experiments[key];
  const ancestors = new Set();
  function visit(source) {
    if (ancestors.has(source)) return;
    ancestors.add(source);
    const record = experiments[source];
    if (record.parent) visit(record.parent);
    for (const donor of record.donors || []) visit(donor);
  }
  visit(key);
  for (const node of nodes) {
    node.setAttribute("aria-pressed", String(node.dataset.experiment === key));
    node.classList.toggle("is-ancestor", ancestors.has(node.dataset.experiment));
  }
  for (const link of links) {
    link.classList.toggle("is-related", ancestors.has(link.dataset.from) && ancestors.has(link.dataset.to));
  }
  document.querySelector(".experiment-tree").classList.add("has-selection");
  document.getElementById("detail-id").textContent = experiment.id;
  document.getElementById("detail-status").textContent = experiment.status;
  document.getElementById("detail-title").textContent = experiment.title;
  document.getElementById("detail-description").textContent = experiment.description;
  const sources = experiment.parent
    ? [`${experiment.donors?.length ? "Base" : "Parent"}: ${experiments[experiment.parent].id}`]
    : ["Source: unchanged repository"];
  for (const donor of experiment.donors || []) {
    sources.push(`Donor: ${experiments[donor].id}`);
  }
  document.getElementById("detail-sources").textContent = sources.join(" · ");
  checks.replaceChildren(...experiment.checks.map((label) => {
    const stamp = document.createElement("span");
    stamp.textContent = label;
    return stamp;
  }));
}

for (const node of nodes) {
  node.disabled = false;
  node.addEventListener("click", () => selectExperiment(node.dataset.experiment));
  node.addEventListener("focus", () => {
    node.scrollIntoView({block: "nearest", inline: "nearest", behavior: "instant"});
  });
}

selectExperiment("combined");
document.querySelector(".interactive-hint").hidden = false;

const copyStatus = document.getElementById("copy-status");
let feedbackTimer;

for (const button of document.querySelectorAll("[data-copy]")) {
  button.hidden = false;
  button.addEventListener("click", async () => {
    clearTimeout(feedbackTimer);
    copyStatus.textContent = "";
    copyStatus.classList.remove("is-error");
    const source = document.getElementById(button.dataset.copy);
    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error("Clipboard API unavailable");
      }
      await navigator.clipboard.writeText(source.textContent.trim());
      copyStatus.textContent = button.dataset.copy === "clone-command"
        ? "Commands copied. Paste them into your terminal."
        : "Prompt copied. Paste it into your Copilot session.";
      feedbackTimer = setTimeout(() => { copyStatus.textContent = ""; }, 6000);
    } catch (error) {
      copyStatus.classList.add("is-error");
      copyStatus.textContent = "Clipboard access is unavailable. Select the text above and copy it manually.";
      console.warn("Olo: clipboard copy failed.", error);
    }
  });
}
