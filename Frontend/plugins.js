/* ============================================================
   A.3.T.H.E.R — Extensions Manager
   Gateway · Plugins · MCP Host · Remote Dev · Autopilot
============================================================ */
"use strict";

const API = {
  llm: "/api/llm/status",
  plugins: "/api/plugins",
  pluginToggle: (name) => `/api/plugins/${encodeURIComponent(name)}/toggle`,
  pluginReload: (name) => `/api/plugins/${encodeURIComponent(name)}/reload`,
  pluginReloadAll: "/api/plugins/reload",
  pluginRun: (name) => `/api/plugins/${encodeURIComponent(name)}/run`,
  mcpServers: "/api/mcp/servers",
  mcpConnect: (name) => `/api/mcp/servers/${encodeURIComponent(name)}/connect`,
  mcpDisconnect: (name) => `/api/mcp/servers/${encodeURIComponent(name)}/disconnect`,
  mcpTools: "/api/mcp/tools",
  mcpToolCall: "/api/mcp/tools/call",
  mcpCatalog: "/api/mcp/catalog",
  mcpCatalogInstall: "/api/mcp/catalog/install",
  remoteServers: "/api/remote/servers",
  remoteTest: (name) => `/api/remote/servers/${encodeURIComponent(name)}/test`,
  remoteExec: (name) => `/api/remote/servers/${encodeURIComponent(name)}/exec`,
  remoteStatus: "/api/remote/status",
  autopilot: "/api/autopilot/run",
};

/* ---------------- helpers ---------------- */
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
  const node = el("div", `toast ${type === "info" ? "" : type}`);
  node.appendChild(el("h4", null, title));
  node.appendChild(el("p", null, message));
  box.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ---------------- LLM gateway ---------------- */
async function renderLLM() {
  const data = await api(API.llm);
  const list = document.getElementById("llm-list");
  list.innerHTML = "";

  const providers = data.providers || [];
  for (const p of providers) {
    const row = el("div", "row");
    const main = el("div", "row-main");
    const title = el("div", "row-title");
    title.appendChild(el("span", `dot ${p.configured ? "ok" : "off"}`));
    title.appendChild(el("span", null, p.display_name));
    if (p.breaker_open) title.appendChild(el("span", "badge py", "cooldown"));
    main.appendChild(title);
    const sub = el("div", "row-sub");
    sub.textContent = `model: ${p.model || "—"} · priority #${p.order + 1}${p.configured ? "" : " · no key"}`;
    main.appendChild(sub);
    row.appendChild(main);
    list.appendChild(row);
  }

  const pill = document.getElementById("llm-pill");
  const count = providers.filter((p) => p.configured).length;
  pill.textContent = `${count} KEYED`;
  pill.className = `pill ${count ? "ok" : "warn"}`;
  document.getElementById("stat-llm").textContent = data.best_provider ? data.best_provider.toUpperCase() : "NONE";
}

/* ---------------- plugins ---------------- */
function pluginCard(info) {
  const card = el("div", "card");
  const head = el("div", "card-head");
  const title = el("div", "card-title");
  title.appendChild(el("span", null, info.name));
  title.appendChild(el("span", `badge ${info.plugin_type === "python" ? "py" : info.plugin_type === "javascript" ? "js" : "mcp"}`, info.plugin_type));
  head.appendChild(title);
  card.appendChild(head);

  if (info.description) card.appendChild(el("div", "card-desc", info.description));

  if (info.capabilities && info.capabilities.length) {
    const caps = el("div", "caps");
    info.capabilities.slice(0, 6).forEach((c) => caps.appendChild(el("span", "cap-chip", c.name)));
    if (info.capabilities.length > 6) caps.appendChild(el("span", "cap-chip", `+${info.capabilities.length - 6}`));
    card.appendChild(caps);
  }

  if (info.error) card.appendChild(el("div", "card-err", `⚠ ${info.error}`));

  const foot = el("div", "card-foot");
  const meta = el("div", "card-meta");
  meta.textContent = `v${info.version}${info.loaded ? " · loaded" : " · not loaded"}`;
  foot.appendChild(meta);

  const actions = el("div", "row-actions");
  const reload = el("button", "icon-btn");
  reload.innerHTML = '<i class="fa-solid fa-rotate"></i>';
  reload.title = "Hot-reload";
  reload.onclick = async () => {
    try { await api(API.pluginReload(info.name)); toast("Reloaded", `${info.name} hot-reloaded.`, "ok"); refreshAll(); }
    catch (err) { toast("Reload failed", err.message, "err"); }
  };
  actions.appendChild(reload);

  const toggle = el("label", "switch");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = !!info.enabled;
  input.onchange = async () => {
    try {
      await api(API.pluginToggle(info.name), { method: "POST", body: JSON.stringify({ enabled: input.checked }) });
      toast("Plugin", `${info.name} ${input.checked ? "enabled" : "disabled"}.`, "ok");
      refreshAll();
    } catch (err) { toast("Toggle failed", err.message, "err"); input.checked = !input.checked; }
  };
  toggle.appendChild(input);
  toggle.appendChild(el("span", "slider"));
  actions.appendChild(toggle);
  foot.appendChild(actions);
  card.appendChild(foot);
  return card;
}

async function renderPlugins() {
  const data = await api(API.plugins);
  const grid = document.getElementById("plugins-grid");
  grid.innerHTML = "";
  const plugins = data.plugins || [];
  if (!plugins.length) {
    grid.appendChild(el("p", "hint", "No plugins discovered yet."));
  }
  plugins.forEach((p) => grid.appendChild(pluginCard(p)));

  document.getElementById("stat-plugins").textContent = `${plugins.length}`;
}

/* ---------------- MCP servers ---------------- */
async function renderMCP() {
  const data = await api(API.mcpServers);
  const list = document.getElementById("mcp-list");
  list.innerHTML = "";
  const servers = data.servers || [];

  for (const s of servers) {
    const row = el("div", "row");
    const main = el("div", "row-main");
    const title = el("div", "row-title");
    const state = s.connected ? "ok" : s.error ? "err" : "off";
    title.appendChild(el("span", `dot ${state}`));
    title.appendChild(el("span", null, s.name));
    main.appendChild(title);
    const sub = el("div", "row-sub");
    sub.textContent = `${s.transport.toUpperCase()} · ${s.connected ? `${s.tools} tools` : s.error || "disconnected"}${s.enabled ? "" : " · disabled"}`;
    main.appendChild(sub);
    row.appendChild(main);

    const actions = el("div", "row-actions");
    if (s.connected) {
      const btn = el("button", "btn ghost danger", "Disconnect");
      btn.onclick = async () => {
        try { await api(API.mcpDisconnect(s.name), { method: "POST" }); toast("MCP", `${s.name} disconnected.`, "ok"); refreshAll(); }
        catch (err) { toast("Error", err.message, "err"); }
      };
      actions.appendChild(btn);
    } else if (s.enabled) {
      const btn = el("button", "btn ghost", "Connect");
      btn.onclick = async () => {
        try { await api(API.mcpConnect(s.name), { method: "POST" }); toast("MCP", `${s.name} connected.`, "ok"); refreshAll(); }
        catch (err) { toast("Connect failed", err.message, "err"); refreshAll(); }
      };
      actions.appendChild(btn);
    }

    const toggle = el("label", "switch");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = !!s.enabled;
    input.onchange = async () => {
      try {
        if (input.checked) await api(API.mcpConnect(s.name), { method: "POST" });
        else await api(API.mcpDisconnect(s.name), { method: "POST" });
        toast("MCP", `${s.name} ${input.checked ? "enabled" : "disabled"}.`, "ok");
      } catch (err) { toast("Toggle failed", err.message, "err"); }
      refreshAll();
    };
    toggle.appendChild(input);
    toggle.appendChild(el("span", "slider"));
    actions.appendChild(toggle);
    row.appendChild(actions);
    list.appendChild(row);
  }

  const connected = servers.filter((s) => s.connected).length;
  const pill = document.getElementById("mcp-pill");
  pill.textContent = `${connected}/${servers.length}`;
  pill.className = `pill ${connected ? "ok" : "warn"}`;
  document.getElementById("stat-mcp").textContent = `${connected} ONLINE`;
}

/* ---------------- MCP catalog ---------------- */
const KIND_BADGE = {
  npx: ["npm", "js"], uv: ["uv", "py"], go: ["go", "js"],
  git: ["build", "py"], app: ["app", "js"], index: ["index", "mcp"],
  cli: ["cli", "js"], collection: ["set", "mcp"], note: ["note", "js"],
  community: ["comm", "mcp"],
};

function catalogRow(entry, installed) {
  const row = el("div", "row");
  const main = el("div", "row-main");
  const title = el("div", "row-title");
  const [label, badgeClass] = KIND_BADGE[entry.kind] || [entry.kind, "mcp"];
  title.appendChild(el("span", `badge ${badgeClass}`, label));
  title.appendChild(el("span", null, entry.display || entry.name));
  if (installed.includes(entry.name)) title.appendChild(el("span", "badge js", "installed"));
  main.appendChild(title);
  if (entry.description) main.appendChild(el("div", "row-sub", entry.description));
  if (entry.requirements) {
    const req = el("div", "card-meta");
    req.textContent = `⚙ ${entry.requirements}`;
    main.appendChild(req);
  }
  row.appendChild(main);

  const actions = el("div", "row-actions");
  const canInstall =
    !installed.includes(entry.name) &&
    !["app", "cli", "collection", "note", "index"].includes(entry.kind) &&
    (entry.kind !== "community" || !!entry.install_hint);
  if (canInstall) {
    const btn = el("button", "btn primary", "Add & Enable");
    btn.onclick = () => installCatalogEntry(entry.name);
    actions.appendChild(btn);
  } else if (["app", "cli"].includes(entry.kind)) {
    actions.appendChild(el("span", "card-meta", "app / CLI — see setup steps below"));
  } else if (entry.kind === "collection") {
    actions.appendChild(el("span", "card-meta", "collection — install the servers above"));
  } else if (entry.kind === "note") {
    actions.appendChild(el("span", "card-meta", "already in A3THER"));
  } else if (entry.kind === "index") {
    actions.appendChild(el("span", "card-meta", "server list — browse its Community tab"));
  } else if (entry.kind === "community" && !entry.install_hint) {
    actions.appendChild(el("span", "card-meta", "no one-click install — check repo"));
  }
  if (entry.source) {
    const link = el("a", "btn ghost", "Repo");
    link.href = entry.source;
    link.target = "_blank";
    link.rel = "noopener";
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
  list.appendChild(el("p", "hint", "Loading catalog…"));
  try {
    const data = await api(`${API.mcpCatalog}?${params.toString()}`);
    list.innerHTML = "";
    const installed = data.installed || [];
    if (data.index_error && source) {
      list.appendChild(el("p", "hint", `⚠ Index fetch failed: ${esc(data.index_error)} — try again in a moment.`));
    }
    const curated = data.curated || [];
    curated.forEach((e) => list.appendChild(catalogRow(e, installed)));
    const community = data.community || [];
    if (source && community.length) {
      const divider = el("div", "row-sub");
      divider.textContent = `── ${community.length} community servers (${source}) ──`;
      list.appendChild(divider);
      community.slice(0, 60).forEach((e) => list.appendChild(catalogRow(e, installed)));
      if (community.length > 60) list.appendChild(el("p", "hint", `…and ${community.length - 60} more — use search to narrow.`));
    } else if (source) {
      list.appendChild(el("p", "hint", q ? "No matches." : "Index empty or unreachable."));
    }
  } catch (err) {
    list.innerHTML = "";
    list.appendChild(el("p", "hint", `Catalog error: ${esc(err.message)}`));
  }
}

async function installCatalogEntry(name) {
  const source = document.getElementById("cat-source").value || null;
  const buttons = [...document.querySelectorAll("#catalog-list .btn.primary")];
  buttons.forEach((b) => { b.disabled = true; });
  try {
    const data = await api(API.mcpCatalogInstall, {
      method: "POST",
      body: JSON.stringify({ name, source }),
    });
    if (data.ok) {
      toast("MCP catalog", `${name} installed${data.note ? ` — ${data.note}` : ""}.`, "ok");
    } else {
      toast("Install failed", data.error || "unknown error", "err");
    }
  } catch (err) {
    toast("Install failed", err.message, "err");
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
    await renderCatalog();
    refreshAll();
  }
}

/* ---------------- MCP tools ---------------- */
let toolModalTarget = null;

async function renderTools() {
  const data = await api(API.mcpTools);
  const list = document.getElementById("tools-list");
  list.innerHTML = "";
  const tools = data.tools || [];

  for (const t of tools) {
    const chip = el("div", "tool");
    const left = el("div");
    left.appendChild(el("div", "tool-name", `${t.server}__${t.tool}`));
    left.appendChild(el("div", "tool-server", t.server));
    chip.appendChild(left);
    if (t.description) chip.appendChild(el("div", "tool-desc", t.description));
    chip.onclick = () => openToolModal(t.server, t.tool, t.input_schema);
    list.appendChild(chip);
  }

  const pill = document.getElementById("tools-pill");
  pill.textContent = `${tools.length}`;
  pill.className = `pill ${tools.length ? "ok" : ""}`;
}

function openToolModal(server, tool, schema) {
  toolModalTarget = { server, tool };
  const modal = document.getElementById("modal");
  document.getElementById("modal-title").textContent = `Invoke ${server}__${tool}`;
  const sample = (schema && schema.properties) ? Object.fromEntries(
    Object.keys(schema.properties).slice(0, 2).map((k) => [k, ""])
  ) : {};
  document.getElementById("modal-args").value = JSON.stringify(sample, null, 2);
  document.getElementById("modal-result").textContent = "";
  modal.hidden = false;
}

async function runToolCall() {
  if (!toolModalTarget) return;
  const { server, tool } = toolModalTarget;
  let argumentsObject = {};
  try {
    const raw = document.getElementById("modal-args").value.trim();
    argumentsObject = raw ? JSON.parse(raw) : {};
  } catch (err) {
    toast("Invalid JSON", err.message, "err");
    return;
  }
  const resultBox = document.getElementById("modal-result");
  resultBox.textContent = "Invoking…";
  try {
    const data = await api(API.mcpToolCall, {
      method: "POST",
      body: JSON.stringify({ server, tool, arguments: argumentsObject }),
    });
    resultBox.textContent = typeof data.result === "string" ? data.result : JSON.stringify(data.result, null, 2);
  } catch (err) {
    resultBox.textContent = `ERROR: ${err.message}`;
  }
}

/* ---------------- remote SSH servers ---------------- */
async function renderRemote() {
  const data = await api(API.remoteServers);
  const list = document.getElementById("remote-list");
  list.innerHTML = "";
  const servers = data.servers || [];

  for (const s of servers) {
    const row = el("div", "row");
    const main = el("div", "row-main");
    const title = el("div", "row-title");
    title.appendChild(el("span", null, s.name));
    main.appendChild(title);
    const sub = el("div", "row-sub");
    sub.textContent = `${s.user}@${s.host}:${s.port}${s.key_path ? ` · ${s.key_path}` : ""}${s.has_password ? " · password set" : ""}`;
    main.appendChild(sub);
    row.appendChild(main);

    const actions = el("div", "row-actions");
    const btn = el("button", "btn ghost", "Test");
    btn.onclick = async () => {
      btn.disabled = true;
      btn.textContent = "Testing…";
      try {
        const result = await api(API.remoteTest(s.name), { method: "POST" });
        toast("SSH test", `${s.name}: ${result.message}`, result.ok ? "ok" : "err");
      } catch (err) { toast("SSH test failed", err.message, "err"); }
      finally { btn.disabled = false; btn.textContent = "Test"; }
    };
    actions.appendChild(btn);
    row.appendChild(actions);
    list.appendChild(row);
  }

  const pill = document.getElementById("remote-pill");
  pill.textContent = `${servers.length} PROFILES`;
  pill.className = `pill ${servers.length ? "ok" : ""}`;
}

/* ---------------- autopilot ---------------- */
function consoleLine(text, kind = "info") {
  const output = document.getElementById("ap-output");
  const empty = output.querySelector(".console-empty");
  if (empty) empty.remove();
  output.appendChild(el("div", `line ${kind}`, text));
  output.scrollTop = output.scrollHeight;
}

async function runAutopilot() {
  const command = document.getElementById("ap-command").value.trim();
  if (!command) { toast("Autopilot", "Enter a command first.", "warn"); return; }
  const cwd = document.getElementById("ap-cwd").value.trim() || null;
  const run = document.getElementById("ap-run");
  run.disabled = true;

  const output = document.getElementById("ap-output");
  output.innerHTML = "";
  consoleLine(`$ ${command}${cwd ? `  (cwd: ${cwd})` : ""}`, "info");

  try {
    const data = await api(API.autopilot, {
      method: "POST",
      body: JSON.stringify({ command, cwd, timeout: 60, max_attempts: 3 }),
    });
    for (const msg of data.messages || []) {
      consoleLine(msg, msg.toLowerCase().includes("patched") ? "warn" : "info");
    }
    consoleLine(`→ ${data.ok ? "PASSED" : "FAILED"} after ${data.attempts} attempt(s)` + (data.error_type && data.error_type !== "none" ? ` (${data.error_type})` : ""), data.ok ? "ok" : "err");
    if (data.patched_files && data.patched_files.length) {
      consoleLine("Patched: " + data.patched_files.join(", "), "warn");
    }
    if (data.final_output) {
      consoleLine("── final output ──", "dim");
      consoleLine(data.final_output.slice(0, 1500), "dim");
    }
    toast("Autopilot", data.ok ? "Command passed — self-healed." : "Could not self-heal; see console.", data.ok ? "ok" : "err");
  } catch (err) {
    consoleLine(`ERROR: ${err.message}`, "err");
    toast("Autopilot failed", err.message, "err");
  } finally {
    run.disabled = false;
  }
}

/* ---------------- global ---------------- */
async function refreshAll() {
  try { await Promise.all([renderLLM(), renderPlugins(), renderMCP(), renderTools(), renderRemote()]); }
  catch (err) { toast("Refresh failed", err.message, "err"); }
}

function bindUI() {
  document.getElementById("reload-all").onclick = async () => {
    try { const data = await api(API.pluginReloadAll, { method: "POST" }); toast("Plugins", `Reloaded ${data.reloaded} plugin(s).`, "ok"); refreshAll(); }
    catch (err) { toast("Reload failed", err.message, "err"); }
  };
  document.getElementById("ap-run").onclick = runAutopilot;
  document.getElementById("ap-command").addEventListener("keydown", (e) => { if (e.key === "Enter") runAutopilot(); });

  const sourceSelect = document.getElementById("cat-source");
  sourceSelect.onchange = renderCatalog;
  let searchTimer = null;
  document.getElementById("cat-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(renderCatalog, 350);
  });

  const modal = document.getElementById("modal");
  document.getElementById("modal-close").onclick = () => { modal.hidden = true; };
  document.getElementById("modal-cancel").onclick = () => { modal.hidden = true; };
  document.getElementById("modal-go").onclick = runToolCall;
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.hidden = true; });
}

document.addEventListener("DOMContentLoaded", () => {
  bindUI();
  refreshAll();
  renderCatalog();
  setInterval(refreshAll, 6000);
});
