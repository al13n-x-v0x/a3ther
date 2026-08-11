/* ============================================================
   A.3.T.H.E.R — Hub (Flowbite UI)
   Boot engine · MCP servers · tools · catalog · autopilot
============================================================ */
"use strict";

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty */ }
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function toast(title, message, type = "info") {
  const node = el("div", `fixed bottom-4 right-4 z-50 max-w-sm rounded-xl border px-4 py-3 shadow-xl ${
    type === "ok" ? "border-emerald-500/40 bg-emerald-950" : type === "err" ? "border-rose-500/40 bg-rose-950" : "border-slate-700 bg-slate-900"
  }`);
  node.appendChild(el("p", "font-semibold text-sm", title));
  node.appendChild(el("p", "text-xs text-slate-300 mt-1", message));
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------- Boot engine ---------------- */
async function renderEngine() {
  let data;
  try { data = await api("/api/engine/status"); } catch { return; }
  const pre = data.preflight || {};
  const mark = (ok) => (ok ? "text-emerald-400" : "text-rose-400");
  document.getElementById("pre-adb").innerHTML =
    pre.done ? `<span class="${mark(pre.adb && pre.adb.ok)}">${pre.adb && pre.adb.ok ? "PASS" : "FAIL"}</span> <span class="text-slate-500">${esc((pre.adb && pre.adb.path) || "not found")}</span>`
             : "checking…";
  document.getElementById("pre-ffmpeg").innerHTML =
    pre.done ? `<span class="${mark(pre.ffmpeg && pre.ffmpeg.ok)}">${pre.ffmpeg && pre.ffmpeg.ok ? "PASS" : "FAIL"}</span> <span class="text-slate-500">${esc((pre.ffmpeg && pre.ffmpeg.path) || "not found")}</span>`
             : "checking…";
  const usb = data.usb_running ? "RUNNING" : "IDLE";
  document.getElementById("pre-usb").innerHTML =
    `<span class="${data.usb_running ? "text-aether-500" : "text-slate-500"}">${usb}</span> <span class="text-slate-500">${(data.usb_devices || []).length} device(s)</span>`;

  const pill = document.getElementById("engine-pill");
  pill.textContent = pre.done && data.usb_running ? "ONLINE" : "BOOTING…";
  pill.className = `text-xs px-3 py-1 rounded-full ${pre.done && data.usb_running ? "bg-emerald-900/60 text-emerald-300" : "bg-slate-800 text-slate-300"}`;

  const logBox = document.getElementById("engine-log");
  const events = data.events || [];
  if (events.length) {
    logBox.innerHTML = "";
    events.slice(-80).forEach((line) => {
      const p = el("p", line.includes("FAIL") || line.includes("error") ? "text-rose-400" : line.includes("⚡") || line.includes("✓") ? "text-emerald-400" : "text-slate-400", line);
      logBox.appendChild(p);
    });
    logBox.scrollTop = logBox.scrollHeight;
  }
}

/* ---------------- MCP servers ---------------- */
function serverCard(s) {
  const card = el("div", "rounded-xl border border-slate-800 bg-slate-950 p-4");
  const head = el("div", "flex items-center justify-between");
  const title = el("div", "flex items-center gap-2");
  title.appendChild(el("span", `w-2.5 h-2.5 rounded-full ${s.connected ? "bg-emerald-400" : s.error ? "bg-rose-500" : "bg-slate-600"}`));
  title.appendChild(el("span", "font-semibold text-sm", s.name));
  head.appendChild(title);
  const toggle = el("label", "relative inline-flex items-center cursor-pointer");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.className = "sr-only peer";
  input.checked = !!s.enabled;
  input.onchange = async () => {
    try {
      if (input.checked) await api(`/api/mcp/servers/${encodeURIComponent(s.name)}/connect`, { method: "POST" });
      else await api(`/api/mcp/servers/${encodeURIComponent(s.name)}/disconnect`, { method: "POST" });
      toast("MCP", `${s.name} ${input.checked ? "connected" : "disabled"}.`, "ok");
    } catch (err) { toast("Toggle failed", err.message, "err"); }
    refresh();
  };
  toggle.appendChild(input);
  toggle.appendChild(el("div", "w-9 h-5 bg-slate-700 peer-focus:ring-2 peer-focus:ring-aether-500/50 rounded-full peer after:content-[''] after:absolute after:top-0.5 after:start-0.5 after:bg-slate-300 after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full peer-checked:after:bg-aether-500"));
  head.appendChild(toggle);
  card.appendChild(head);

  const sub = el("p", "mt-2 text-xs text-slate-400 font-mono",
    `${s.transport.toUpperCase()} · ${s.connected ? `${s.tools} tools` : s.error ? esc(s.error) : "disconnected"}${s.enabled ? "" : " · disabled"}`);
  card.appendChild(sub);
  if (s.connected) {
    const btn = el("button", "mt-3 text-xs font-medium text-rose-400 border border-rose-500/40 rounded-lg px-3 py-1 hover:bg-rose-500/10", "Disconnect");
    btn.onclick = async () => { try { await api(`/api/mcp/servers/${encodeURIComponent(s.name)}/disconnect`, { method: "POST" }); } catch (e) { toast("Error", e.message, "err"); } refresh(); };
    card.appendChild(btn);
  }
  return card;
}

async function renderServers() {
  let data;
  try { data = await api("/api/mcp/servers"); } catch (e) { toast("Servers", e.message, "err"); return; }
  const grid = document.getElementById("servers-grid");
  grid.innerHTML = "";
  const servers = data.servers || [];
  servers.forEach((s) => grid.appendChild(serverCard(s)));
  const connected = servers.filter((s) => s.connected).length;
  const pill = document.getElementById("servers-pill");
  pill.textContent = `${connected}/${servers.length} connected`;
  pill.className = `text-xs px-3 py-1 rounded-full ${connected ? "bg-emerald-900/60 text-emerald-300" : "bg-slate-800 text-slate-300"}`;
}

/* ---------------- MCP tools ---------------- */
async function renderTools() {
  let data;
  try { data = await api("/api/mcp/tools"); } catch { return; }
  const list = document.getElementById("tools-list");
  list.innerHTML = "";
  const tools = data.tools || [];
  if (!tools.length) list.appendChild(el("p", "text-sm text-slate-500", "No connected tools yet — connect a server above."));
  tools.forEach((t) => {
    const chip = el("button", "text-xs font-mono rounded-full px-3 py-1.5 border border-slate-700 bg-slate-950 text-slate-300 hover:border-aether-500 hover:text-aether-500", `${t.server}__${t.tool}`);
    chip.title = t.description || "";
    list.appendChild(chip);
  });
  document.getElementById("tools-pill").textContent = `${tools.length} tool(s)`;
}

/* ---------------- Catalog ---------------- */
const KIND_LABEL = { npx: "npm", uv: "uv", go: "go", git: "build", app: "app", cli: "cli", collection: "set", note: "note", index: "index", community: "comm" };

function catalogRow(entry, installed) {
  const row = el("div", "flex items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-950 p-3");
  const main = el("div", "min-w-0");
  const title = el("div", "flex items-center gap-2 flex-wrap");
  title.appendChild(el("span", "text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full border border-slate-700 text-slate-400", KIND_LABEL[entry.kind] || entry.kind));
  title.appendChild(el("span", "text-sm font-semibold", entry.display || entry.name));
  if (installed.includes(entry.name)) title.appendChild(el("span", "text-[10px] uppercase px-2 py-0.5 rounded-full border border-emerald-500/40 text-emerald-400", "installed"));
  main.appendChild(title);
  if (entry.description) main.appendChild(el("p", "text-xs text-slate-500 mt-1 truncate", entry.description));
  if (entry.requirements) main.appendChild(el("p", "text-[11px] text-slate-600 mt-0.5", `⚙ ${entry.requirements}`));
  row.appendChild(main);

  const actions = el("div", "flex items-center gap-2 shrink-0");
  const canInstall = !installed.includes(entry.name) && !["app", "cli", "collection", "note", "index"].includes(entry.kind) && (entry.kind !== "community" || !!entry.install_hint);
  if (canInstall) {
    const btn = el("button", "text-slate-950 bg-gradient-to-r from-aether-500 to-aether-600 text-xs font-semibold rounded-lg px-3 py-2 hover:brightness-110", "Add & Enable");
    btn.onclick = () => installCatalogEntry(entry.name);
    actions.appendChild(btn);
  }
  if (entry.source) {
    const link = el("a", "text-xs text-slate-400 border border-slate-700 rounded-lg px-3 py-2 hover:text-aether-500", "Repo");
    link.href = entry.source; link.target = "_blank"; link.rel = "noopener";
    actions.appendChild(link);
  }
  row.appendChild(actions);
  return row;
}

async function renderCatalog() {
  const list = document.getElementById("catalog-list");
  const source = document.getElementById("cat-source").value;
  const q = document.getElementById("cat-search").value.trim();
  const params = new URLSearchParams();
  if (source) params.set("source", source);
  if (q) params.set("q", q);
  list.innerHTML = "";
  list.appendChild(el("p", "text-sm text-slate-500", "Loading catalog…"));
  try {
    const data = await api(`/api/mcp/catalog?${params.toString()}`);
    list.innerHTML = "";
    const installed = data.installed || [];
    (data.curated || []).forEach((e) => list.appendChild(catalogRow(e, installed)));
    const community = data.community || [];
    if (source && community.length) {
      list.appendChild(el("p", "text-xs text-slate-500 mt-2", `── ${community.length} community servers (${source}) ──`));
      community.slice(0, 40).forEach((e) => list.appendChild(catalogRow(e, installed)));
    }
  } catch (err) {
    list.innerHTML = "";
    list.appendChild(el("p", "text-sm text-rose-400", `Catalog error: ${esc(err.message)}`));
  }
}

async function installCatalogEntry(name) {
  const source = document.getElementById("cat-source").value || null;
  try {
    const data = await api("/api/mcp/catalog/install", { method: "POST", body: JSON.stringify({ name, source }) });
    toast("Catalog", data.ok ? `${name} installed.` : (data.error || "unknown error"), data.ok ? "ok" : "err");
  } catch (err) { toast("Install failed", err.message, "err"); }
  renderCatalog(); refresh();
}

/* ---------------- Autopilot ---------------- */
function consoleLine(text, kind = "") {
  const out = document.getElementById("ap-output");
  out.appendChild(el("p", kind === "err" ? "text-rose-400" : kind === "warn" ? "text-amber-400" : "text-slate-400", text));
  out.scrollTop = out.scrollHeight;
}

async function runAutopilot() {
  const command = document.getElementById("ap-command").value.trim();
  if (!command) { toast("Autopilot", "Enter a command first.", "warn"); return; }
  const run = document.getElementById("ap-run");
  run.disabled = true;
  const out = document.getElementById("ap-output");
  out.innerHTML = "";
  consoleLine(`$ ${command}`);
  try {
    const data = await api("/api/autopilot/run", { method: "POST", body: JSON.stringify({ command, timeout: 60, max_attempts: 3 }) });
    (data.messages || []).forEach((m) => consoleLine(m, m.toLowerCase().includes("patched") ? "warn" : ""));
    consoleLine(`→ ${data.ok ? "PASSED" : "FAILED"} after ${data.attempts} attempt(s)`, data.ok ? "" : "err");
    if (data.final_output) consoleLine(data.final_output.slice(0, 1200));
  } catch (err) { consoleLine(`ERROR: ${err.message}`, "err"); }
  run.disabled = false;
}

/* ---------------- global ---------------- */
function refresh() {
  renderEngine(); renderServers(); renderTools();
}

let catalogTimer = null;
document.addEventListener("DOMContentLoaded", () => {
  refresh();
  renderCatalog();
  setInterval(refresh, 2000);
  document.getElementById("cat-source").onchange = renderCatalog;
  document.getElementById("cat-search").addEventListener("input", () => {
    clearTimeout(catalogTimer);
    catalogTimer = setTimeout(renderCatalog, 350);
  });
  document.getElementById("ap-run").onclick = runAutopilot;
  document.getElementById("ap-command").addEventListener("keydown", (e) => { if (e.key === "Enter") runAutopilot(); });
});
