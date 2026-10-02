"use strict";
(() => {
  const {t} = window.CodeHarnessI18n;
  const token = document.querySelector('meta[name="csrf-token"]')?.content;
  const form = document.getElementById("launch-form");
  const resume = document.getElementById("resume-task");
  const post = async (endpoint, payload) => {
    const response = await fetch(endpoint, {method: "POST", headers: {
      "Content-Type": "application/json", "X-CodeHarness-CSRF": token}, body: JSON.stringify(payload)});
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || t("Task unavailable"));
    location.assign(body.contest_run_id ? `/contests/${encodeURIComponent(body.contest_run_id)}`
      : `/tasks/${encodeURIComponent(body.task_id)}`);
  };
  if (form) {
    const mode = form.elements.mode, profile = form.elements.profile;
    const mixed = profile.options[0];
    const names = ["max_cost_cny", "max_llm_calls", "max_submissions", "http_timeout", "poll_interval", "deadline"];
    const counts = ["max_llm_calls", "max_submissions"];
    const savedHarnessCounts = {};
    const updateSummary = () => {
      document.getElementById("config-summary").textContent = t("{cost} CNY · {calls} model calls · {submissions} submissions", {
        cost: form.elements.max_cost_cny.value, calls: form.elements.max_llm_calls.value,
        submissions: form.elements.max_submissions.value});
    };
    const updateMode = () => {
      mixed.disabled = mode.value === "code-only";
      if (mixed.disabled && !profile.value) profile.value = "standard";
      for (const name of counts) {
        const field = form.elements[name];
        if (mixed.disabled) {
          if (!field.disabled) savedHarnessCounts[name] = field.value;
          field.value = "1";
        } else if (field.disabled) {
          field.value = savedHarnessCounts[name] || field.defaultValue;
        }
        field.disabled = mixed.disabled;
      }
      document.getElementById("mode-note").textContent = t(mixed.disabled
        ? "One CODE call · at most one formal submission · no samples or retries"
        : "Public samples first · 3 failed DEBUG candidates replan · no model escalation");
      updateSummary();
    };
    mode.addEventListener("change", updateMode);
    names.forEach(name => form.elements[name].addEventListener("input", updateSummary));
    form.addEventListener("invalid", event => {
      if (names.includes(event.target.name)) document.getElementById("task-config").open = true;
    }, true);
    document.getElementById("reset-task-config").addEventListener("click", () => {
      names.forEach(name => {
        const field = form.elements[name];
        field.value = field.defaultValue;
        savedHarnessCounts[name] = field.defaultValue;
      });
      updateMode();
    });
    updateMode();
    const requestId = crypto.randomUUID();
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const button = form.querySelector('button[type="submit"]');
      const message = document.getElementById("launch-message");
      button.disabled = true; message.hidden = false; message.textContent = t("Queuing task…");
      try {
        const contest = form.dataset?.target === "contest";
        await post(contest ? "/api/dashboard/contests/launch" : "/api/dashboard/launch", {
          ...(contest ? {contest_id: form.elements.contest_id.value.trim(),
            total_cost_cny: Number(form.elements.total_cost_cny.value)}
            : {problem_id: form.elements.problem_id.value.trim()}),
          mode: mode.value, profile: profile.value || null,
          task_config: Object.fromEntries(names.map(name => [name, Number(form.elements[name].value)])),
          request_id: requestId, confirm_remote_calls: form.elements.confirm.checked});
      } catch (error) {
        message.textContent = error.message; button.disabled = false;
      }
    });
  }
  const resumeContest = document.getElementById("resume-contest");
  if (resumeContest) {
    const requestId = crypto.randomUUID();
    resumeContest.addEventListener("click", async () => {
      if (!confirm(t("Resume keeps the frozen contest problems and all saved budgets. Continue?"))) return;
      resumeContest.disabled = true;
      try {
        await post(`/api/dashboard/contests/${encodeURIComponent(resumeContest.dataset.runId)}/resume`,
          {request_id: requestId, confirm_remote_calls: true});
      } catch (error) {
        document.getElementById("contest-message").textContent = error.message;
        resumeContest.disabled = false;
      }
    });
  }
  if (resume) {
    const requestId = crypto.randomUUID();
    resume.addEventListener("click", async () => {
      if (!confirm(t("Resume may continue model calls and MiniOJ queries within the saved budget. Continue?"))) return;
      resume.disabled = true;
      try {
        await post(`/api/dashboard/tasks/${encodeURIComponent(resume.dataset.taskId)}/resume`,
          {request_id: requestId, confirm_remote_calls: true});
      } catch (error) {
        document.getElementById("launch-status").textContent = error.message;
        resume.disabled = false;
      }
    });
  }
})();
