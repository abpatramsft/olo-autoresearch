"use strict";

const experiments = {
  baseline: {
    id: "exp_0000",
    status: "BASELINE",
    title: "Start with a trustworthy measuring stick.",
    description: "Repeat checks on unchanged source. Audit the benchmark and gates. Independent approval freezes the baseline before optimization begins.",
    checks: ["Repeatable checks", "Protected measurement", "Reviewed"],
  },
  rejected: {
    id: "exp_0001",
    status: "NOT PROMOTED",
    title: "A higher score cannot excuse a regression.",
    description: "This direction fails a gate. It cannot become an approved parent, but its saved traces still help explain what to avoid next.",
    checks: ["Gate failed", "Evidence preserved", "No promotion"],
  },
  retained: {
    id: "exp_0002",
    status: "RETAINED",
    title: "Useful does not always mean best overall.",
    description: "An independently reviewed specialist can stay available for future work without being called meaningful aggregate progress.",
    checks: ["Task-level strengths", "Reviewed", "Reusable parent"],
  },
  approved: {
    id: "exp_0003",
    status: "APPROVED",
    title: "A better score is only the start.",
    description: "The change clears the gain threshold, passes its gates, and earns independent approval. Now it can become a parent.",
    checks: ["Measured gain", "Gates passed", "Reviewed"],
  },
};

const nodes = document.querySelectorAll("[data-experiment]");
const checks = document.getElementById("detail-checks");

for (const node of nodes) {
  node.disabled = false;
  node.addEventListener("click", () => {
    const experiment = experiments[node.dataset.experiment];
    for (const other of nodes) {
      other.setAttribute("aria-pressed", String(other === node));
    }
    document.getElementById("detail-id").textContent = experiment.id;
    document.getElementById("detail-status").textContent = experiment.status;
    document.getElementById("detail-title").textContent = experiment.title;
    document.getElementById("detail-description").textContent = experiment.description;
    checks.replaceChildren(...experiment.checks.map((label) => {
      const stamp = document.createElement("span");
      stamp.textContent = label;
      return stamp;
    }));
  });
}

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
