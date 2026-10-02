/* All Workspace content enters the DOM through textContent, never raw HTML. */
"use strict";
(() => {
  const {t, message} = window.CodeHarnessI18n;
  const root = document.getElementById("task-detail");
  if (!root) return;
  const revealSection = () => {
    let target;
    try { target = document.getElementById(decodeURIComponent(location.hash.slice(1))); }
    catch { return; }
    if (!target) return;
    let ancestor = target.closest("details");
    while (ancestor) {
      ancestor.open = true;
      ancestor = ancestor.parentElement?.closest("details");
    }
    target.scrollIntoView();
  };
  window.addEventListener("hashchange", revealSection);
  revealSection();
  const unknown = value => value === null || value === undefined || value === "" ? t("Unknown") : String(value);
  const cost = value => value.estimated_cost !== null && value.cost_currency
    ? `${value.cost_currency} ${Number(value.estimated_cost).toFixed(6)}`
    : value.cost_status === "not_applicable" ? t("Not applicable") : t("Unknown");
  const title = value => t(unknown(value).replaceAll("_", " ").replace(/\b\w/g, char => char.toUpperCase()));
  const pretty = value => JSON.stringify(value, null, 2);
  const timestamp = value => {
    if (!value) return t("Unknown");
    if (!/(Z|[+-]\d\d:\d\d)$/.test(value)) return value.replace("T", " ") + " " + t("(zone unrecorded)");
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toISOString().slice(0, 19).replace("T", " ");
  };
  const node = (tag, text, className) => {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = unknown(text);
    if (className) result.className = className;
    return result;
  };
  const badge = value => {
    const result = node("span", value, "badge");
    if (["AC", "accepted"].includes(value)) result.classList.add("good");
    if (["WA", "CE", "RE", "TLE", "MLE", "OLE"].includes(value)) result.classList.add("bad");
    if (["IE", "client_failure", "model_failure", "result_unknown", "remote_infrastructure_failure"].includes(value)) result.classList.add("warn");
    return result;
  };
  const pair = (first, second, className) => {
    const result = node("span", first, className);
    result.append(node("small", second));
    return result;
  };
  const metadata = value => {
    const result = node("details");
    result.append(node("summary", t("Safe metadata")), node("pre", pretty(value)));
    return result;
  };
  const table = (id, rows, columns, empty) => {
    const body = document.getElementById(id);
    const fragment = document.createDocumentFragment();
    rows.forEach(row => {
      const tr = node("tr");
      columns.forEach(column => {
        const td = node("td");
        const value = column(row);
        if (value instanceof Node) td.append(value);
        else td.textContent = unknown(value);
        tr.append(td);
      });
      fragment.append(tr);
    });
    if (!rows.length) {
      const tr = node("tr"), td = node("td", t(empty), "empty");
      td.colSpan = columns.length;
      tr.append(td); fragment.append(tr);
    }
    body.replaceChildren(fragment);
  };
  const update = detail => {
    const task = detail.summary;
    const resume = document.getElementById("resume-task");
    if (resume) resume.hidden = !detail.resumable;
    const job = document.getElementById("launch-status");
    if (job) job.textContent = detail.launch_job ? t(detail.launch_job.status) : "";
    root.querySelectorAll("[data-summary]").forEach(element => {
      const field = element.dataset.summary;
      element.textContent = field === "duration" ? (task.duration === null ? t("Unknown") : `${task.duration.toFixed(3)} s`)
        : field === "cost_status" ? title(task[field]) : ["created_at", "finished_at"].includes(field)
          ? timestamp(task[field]) : unknown(task[field]);
      if (["status", "final_verdict"].includes(field)) element.className = badge(task[field]).className;
    });
    root.querySelector("[data-cost]").textContent = cost(task);
    root.querySelectorAll("[data-recovery]").forEach(element => {
      const value = (detail.recovery_metrics || {})[element.dataset.recovery];
      element.textContent = typeof value === "boolean" ? t(value ? "Yes" : "No")
        : Array.isArray(value) ? value.map(t).join(", ") || t("None") : unknown(value);
    });
    document.getElementById("solution-code").textContent = detail.solution === null ? t("Solution unavailable") : detail.solution;
    document.getElementById("current-state").textContent = pretty(detail.current_state);
    const notice = document.getElementById("data-notice");
    notice.textContent = detail.warnings.map(message).join(" · "); notice.hidden = !detail.warnings.length;
    table("versions-body", detail.code_versions, [v => node("span", v.version, "mono"), v => pair(v.phase, v.created_at),
      v => node("span", v.model_call_id, "mono wrap"), v => node("span", v.sha256, "mono hash"),
      v => v.submission_ids.join(", ") || t("Unknown"), v => v.verdicts.join(", ") || t("Unknown")], "No recorded code versions.");
    table("models-body", detail.model_calls, [v => pair(v.phase, v.role), v => v.profile, v => pair(v.provider, v.model),
      v => pair(v.status, v.finish_reason), v => `${unknown(v.input_tokens)} / ${unknown(v.output_tokens)}`,
      v => `${unknown(v.latency_ms)} ms`, v => pair(cost(v), title(v.cost_status)), v => pair(v.call_id, v.request_id, "mono wrap")], "No recorded model calls.");
    table("tools-body", detail.tool_calls, [v => node("span", v.name, "mono"), v => v.phase, v => v.timestamp,
      v => badge(v.status), v => `${unknown(v.duration_ms)} ms`, v => metadata(v.metadata)], "No recorded tool calls.");
    table("submissions-body", detail.submissions, [v => node("span", v.submission_id, "mono"), v => v.code_version,
      v => v.status, v => badge(v.verdict), v => `${unknown(v.time_ms)} ms`, v => `${unknown(v.memory_kb)} KB`,
      v => v.test_summary ? message(v.test_summary) : t("Unknown"), v => pair(v.created_at, v.finished_at)], "No confirmed submissions.");
    const artifacts = document.getElementById("artifact-list");
    artifacts.replaceChildren(...detail.artifacts.map(file => {
      const a = node("a", file.name);
      a.href = `/tasks/${encodeURIComponent(task.task_id)}/artifacts/${file.name.split("/").map(encodeURIComponent).join("/")}`;
      a.target = "_blank"; a.rel = "noopener";
      a.append(node("small", ` ${file.size} B`)); return a;
    }));
    return task;
  };
  const appendEvents = events => {
    const target = document.getElementById("timeline-events");
    if (events.length) document.getElementById("empty-timeline")?.remove();
    events.forEach(event => {
      const entry = node("div", undefined, "timeline-event");
      const body = node("div", undefined, "event-body");
      const heading = node("div");
      heading.append(node("span", event.event_type, "event-type"), node("span", event.phase, "phase-label"));
      body.append(heading, node("p", event.summary));
      if (event.original_type) body.append(node("small", `${t("Original type:")} ${event.original_type}`));
      const extra = metadata(event.metadata);
      extra.querySelector("summary").textContent = `${t("Safe metadata")}${event.correlation_id ? ` · ${event.correlation_id}` : ""}`;
      body.append(extra);
      entry.append(node("div", timestamp(event.timestamp), "event-time mono"), body); target.append(entry);
    });
  };
  document.getElementById("copy-code").addEventListener("click", async event => {
    try {
      await navigator.clipboard.writeText(document.getElementById("solution-code").textContent);
      event.target.textContent = t("Copied");
      setTimeout(() => { event.target.textContent = t("Copy"); }, 1800);
    } catch { event.target.textContent = t("Select text to copy"); }
  });
  if (root.dataset.terminal === "true") return;
  const endpoint = `/api/dashboard/tasks/${encodeURIComponent(root.dataset.taskId)}`;
  const label = document.getElementById("refresh-status");
  let cursor = Number(root.dataset.cursor), generation = Number(root.dataset.generation);
  let terminalSeen = false;
  const get = async url => {
    const response = await fetch(url, {cache: "no-store", signal: AbortSignal.timeout(10000)});
    if (!response.ok) throw new Error("Read unavailable");
    return response.json();
  };
  const refresh = async () => {
    try {
      const detail = await get(endpoint);
      const task = update(detail);
      let more;
      do {
        const data = await get(`${endpoint}/events?cursor=${cursor}&generation=${generation}`);
        if (data.reset) document.getElementById("timeline-events").replaceChildren();
        appendEvents(data.events); cursor = data.next_cursor; generation = data.generation;
        document.getElementById("event-count").textContent = cursor;
        more = cursor < data.total;
      } while (more);
      // One final drain accommodates State DONE being saved before the final trace append.
      if (task.terminal && terminalSeen) { label.textContent = t("Terminal · refresh stopped"); return; }
      terminalSeen = task.terminal;
      label.textContent = t(task.terminal ? "Terminal · draining final events" : "Live · updates every 2 s");
    } catch {
      label.textContent = t("Temporarily unavailable · retaining last view");
    }
    setTimeout(refresh, 2000);
  };
  setTimeout(refresh, 2000);
})();
