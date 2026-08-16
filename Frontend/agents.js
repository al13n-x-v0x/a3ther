/* A.3.T.H.E.R — Agent Swarm Console */
"use strict";

const LAST_EVENT_KEY = "a3ther_swarm_last_event";

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let data = null;
  try { data = await response.json(); } catch (_) { /* empty */ }
  if (!response.ok) throw new Error((data && data.error) || `HTTP ${response.status}`);
  return data;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function toast(title, message, type = "info") {
  const box = document.getElementById("toasts");
  const node = el("div", `toast ${type}`);
  node.appendChild(el("h4", null, title));
  node.appendChild(el("p", null, message));
  box.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

const KIND_TAGS = {
  plan: ["plan", "PLAN"],
  start: ["start", "START"],
  transfer: ["transfer", "HANDOFF"],
  event: ["event", "EVENT"],
  result: ["result", "RESULT"],
  done: ["done", "DONE"],
  error: ["error", "ERROR"],
};

function appendEvent(entry) {
  const output = document.getElementById("console-output");
  const [cls, tag] = KIND_TAGS[entry.kind] || ["event", entry.kind.toUpperCase()];
  const line = el("div", "line");
  const stamp = new Date(entry.ts * 1000).toLocaleTimeString();
  line.appendChild(el("span", "tag " + cls, tag));
  line.appendChild(el("span", "agent", `[${entry.agent}]`));
  line.appendChild(document.createTextNode(` ${entry.message}`));
  line.title = stamp;
  output.appendChild(line);
  output.scrollTop = output.scrollHeight;
  return line;
}

async function pollEvents() {
  try {
    const since = Number(localStorage.getItem(LAST_EVENT_KEY) || 0);
    const data = await api(`/api/agents/events?limit=200`);
    const events = data.events || [];
    let newest = since;
    for (const entry of events) {
      if (entry.id > since) {
        appendEvent(entry);
        newest = Math.max(newest, entry.id);
      }
    }
    if (newest > since) localStorage.setItem(LAST_EVENT_KEY, String(newest));
    document.getElementById("stat-events").textContent = String(events.length);
  } catch (err) {
    console.warn("poll failed:", err.message);
  }
}

async function loadStatus() {
  try {
    const data = await api("/api/agents/status");
    document.getElementById("stat-agents").textContent = String((data.agents || []).length);
  } catch (_) { /* backend not up yet */ }
}

async function dispatchTask() {
  const input = document.getElementById("task-input");
  const task = input.value.trim();
  if (!task) { toast("Commander", "Enter a task first.", "warn"); return; }
  const go = document.getElementById("task-go");
  go.disabled = true;

  const planBox = document.getElementById("plan-panel");
  planBox.classList.add("hidden");
  planBox.innerHTML = "";

  try {
    const data = await api("/api/agents/run", {
      method: "POST",
      body: JSON.stringify({ task }),
    });
    if (data.steps && data.steps.length) {
      planBox.innerHTML = "";
      planBox.appendChild(el("div", "kicker", "PLAN"));
      for (const step of data.steps) planBox.appendChild(el("span", "step-chip", `#${step}`));
      planBox.classList.remove("hidden");
    }
    if (!data.ok) toast("Swarm failed", data.error || "unknown error", "err");
    else toast("Swarm", `Task complete — ${(data.steps || []).length} step(s).`, "ok");
  } catch (err) {
    toast("Dispatch failed", err.message, "err");
  } finally {
    go.disabled = false;
    input.value = "";
  }
  await pollEvents();
}

function bind() {
  document.getElementById("task-go").onclick = dispatchTask;
  document.getElementById("task-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") dispatchTask();
  });
  document.getElementById("clear-console").onclick = () => {
    document.getElementById("console-output").innerHTML = "";
  };
}

document.addEventListener("DOMContentLoaded", () => {
  bind();
  loadStatus();
  pollEvents();
  setInterval(pollEvents, 1500);
});
