"use strict";
(() => {
  const root = document.getElementById("contest-detail");
  if (!root) return;
  const {t} = window.CodeHarnessI18n;
  const terminal = ["completed", "blocked", "failed", "stopped", "interrupted"];
  const text = value => value === null || value === undefined ? t("Unknown") : String(value);
  const cell = (tag, value) => { const node = document.createElement(tag); node.textContent = text(value); return node; };
  const update = report => {
    root.querySelectorAll("[data-contest]").forEach(node => {
      const value = report[node.dataset.contest];
      node.textContent = ["status", "official_performance_status"].includes(node.dataset.contest) ? t(value) : text(value);
    });
    document.getElementById("contest-reason").textContent = t(report.reason || "");
    const resume = document.getElementById("resume-contest");
    if (resume) resume.hidden = !report.resumable;
    const rows = report.problems.map(problem => {
      const row = document.createElement("tr");
      const name = cell("td", `${problem.label} · `);
      if (problem.task_id) {
        const link = cell("a", problem.problem_id);
        link.href = `/tasks/${encodeURIComponent(problem.task_id)}`; name.append(link);
      } else name.append(cell("span", problem.problem_id));
      name.append(cell("small", problem.title));
      const status = cell("td", t(problem.terminal_status || problem.status || "pending"));
      status.append(cell("small", t(problem.termination_reason || "")));
      row.append(name, status, cell("td", problem.final_verdict),
        cell("td", problem.llm_calls), cell("td", problem.submission_attempts));
      return row;
    });
    if (!rows.length) {
      const row = document.createElement("tr"), empty = cell("td", t("No public problems loaded."));
      empty.colSpan = 5; row.append(empty); rows.push(row);
    }
    document.getElementById("contest-problems").replaceChildren(...rows);
  };
  const refresh = async () => {
    try {
      const response = await fetch(`/api/dashboard/contests/${encodeURIComponent(root.dataset.runId)}`,
        {cache: "no-store", signal: AbortSignal.timeout(10000)});
      if (!response.ok) throw new Error(t("Contest run unavailable"));
      const report = await response.json(); update(report);
      if (terminal.includes(report.status)) return;
    } catch (error) { document.getElementById("contest-message").textContent = error.message; }
    setTimeout(refresh, 2000);
  };
  if (!terminal.includes(root.dataset.status)) setTimeout(refresh, 1000);
  const performanceButton = document.getElementById("refresh-performance");
  if (performanceButton) performanceButton.addEventListener("click", async () => {
    performanceButton.disabled = true;
    try {
      const response = await fetch(`/api/dashboard/contests/${encodeURIComponent(root.dataset.runId)}/performance`, {
        method: "POST", headers: {"Content-Type": "application/json",
          "X-CodeHarness-CSRF": document.querySelector('meta[name="csrf-token"]').content},
        body: JSON.stringify({request_id: crypto.randomUUID(), confirm_remote_calls: true})});
      if (!response.ok) throw new Error(t("Performance refresh unavailable"));
      update(await response.json());
      document.getElementById("contest-message").textContent = t("Performance refreshed; Agent execution was not started.");
    } catch (error) { document.getElementById("contest-message").textContent = error.message; }
    finally { performanceButton.disabled = false; }
  });
})();
