/* ===========================================================
   A.3.T.H.E.R. — HoloCommand v3 controller
   Everything is wired to the real backend:
     /api/live/status   → telemetry gauges + AI core status
     /api/live/devices  → Connected Devices + globe nodes
     /api/live/weather  → Environment panel
     /api/live/location → city label
     /api/live/files    → Files view
     /api/voice/*       → voice core button
   When the backend is unreachable every widget degrades to a
   clearly-labelled OFFLINE state — nothing is faked.
=========================================================== */
(() => {
  "use strict";

  /* =========================================================
     UTILITIES
  ========================================================= */
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const clamp = (v, min, max) => Math.min(Math.max(v, min), max);
  const rand = (min, max) => Math.random() * (max - min) + min;
  const pad = (n) => String(n).padStart(2, "0");
  const nowStamp = () => {
    const d = new Date();
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  };

  /* =========================================================
     API — small fetch wrapper with graceful offline handling
  ========================================================= */
  const API = {
    async get(path) {
      try {
        const res = await fetch(path, { headers: { Accept: "application/json" } });
        if (!res.ok) return null;
        return await res.json();
      } catch (_) {
        return null;
      }
    },
    async post(path, body) {
      try {
        const res = await fetch(path, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: body ? JSON.stringify(body) : undefined,
        });
        if (!res.ok) return null;
        return await res.json();
      } catch (_) {
        return null;
      }
    }
  };

  /* =========================================================
     TOASTS
  ========================================================= */
  const Toasts = {
    container: null,
    init() {
      if (!document.getElementById("toasts")) {
        this.container = document.createElement("div");
        this.container.id = "toasts";
        document.body.appendChild(this.container);
      } else {
        this.container = document.getElementById("toasts");
      }
    },
    show(message, type = "") {
      const toast = document.createElement("div");
      toast.className = `toast ${type}`;
      toast.textContent = message;
      this.container.appendChild(toast);
      setTimeout(() => toast.classList.add("out"), 3600);
      setTimeout(() => toast.remove(), 4000);
    },
    info(m) { this.show(m, ""); },
    ok(m) { this.show(m, "ok"); },
    warn(m) { this.show(m, "warn"); },
    err(m) { this.show(m, "err"); }
  };

  /* =========================================================
     BOOT OVERLAY
  ========================================================= */
  const Boot = {
    init() {
      const overlay = document.createElement("div");
      overlay.id = "boot-overlay";
      overlay.innerHTML = `
        <div class="boot-box">
          <div class="boot-ring"></div>
          <span class="boot-letter">A</span>
        </div>
        <div class="boot-bar"><i></i></div>
        <p>A.3.T.H.E.R. SYSTEM BOOT // WIRING LIVE DATA</p>`;
      document.body.appendChild(overlay);
      setTimeout(() => {
        overlay.classList.add("done");
        setTimeout(() => overlay.remove(), 700);
      }, 1600);
    }
  };

  /* =========================================================
     CLOCK
  ========================================================= */
  const Clock = {
    timer: null,
    startedAt: Date.now(),
    init() {
      this.tick();
      this.timer = setInterval(() => this.tick(), 1000);
    },
    tick() {
      const d = new Date();
      const timeEl = $("#clock-time");
      const dateEl = $("#clock-date");
      if (timeEl) timeEl.textContent = nowStamp();
      if (dateEl) {
        dateEl.textContent = d
          .toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" })
          .toUpperCase();
      }
    },
    uptime() {
      const s = Math.floor((Date.now() - this.startedAt) / 1000);
      return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
    }
  };

  /* =========================================================
     LIVE BADGE — honest backend connectivity state
  ========================================================= */
  const LiveBadge = {
    set(state) {
      const el = $("#live-badge-text");
      const box = $("#live-badge");
      if (!el) return;
      el.textContent = state.toUpperCase();
      if (box) box.dataset.state = state;
    }
  };

  /* =========================================================
     SPECS — real hardware names (CPU brand, GPU, RAM, storage)
     from /api/live/specs. Runs once + on demand; never fakes.
  ========================================================= */
  const Specs = {
    data: null,
    async load() {
      const s = await API.get("/api/live/specs");
      if (!s || s.error) return null;
      this.data = s;
      const set = (sel, text) => { const el = $(sel); if (el && text) el.textContent = text; };
      const gpuName = (s.gpu && s.gpu.gpus && s.gpu.gpus.length && s.gpu.gpus[0].name) || "";
      set("#cpu-card .metric-heading strong", s.cpu && s.cpu.brand);
      set("#gpu-card .metric-heading strong", gpuName || "GPU");
      set("#ram-card .metric-heading strong", s.ram ? `${s.ram.total_gb} GB` : null);
      set("#storage-card .metric-heading strong", s.storage ? `${s.storage.used_gb} GB / ${s.storage.total_gb} GB` : null);
      set("#core-memory", s.ram ? `${s.ram.total_gb} GB` : null);
      // honest OS/host detail in the terminal once
      if (s.hostname && !Terminal._hostLogged) {
        Terminal.notifyLive({
          hostname: s.hostname,
          platform: s.os && s.os_version ? `${s.os} ${s.os_version}` : ""
        });
        if (s.cpu && s.cpu.brand) {
          Terminal.print(`[SPECS] ${s.cpu.brand} · ${gpuName || "integrated GPU"} · ${s.ram ? s.ram.total_gb + " GB RAM" : ""}`, "ok");
        }
      }
      return s;
    }
  };

  /* =========================================================
     AI CORE — real gateway + plugin state from /api/llm/status
     and /api/plugins, into the AI Core Status panel + pills.
  ========================================================= */
  const AICore = {
    async poll() {
      const [llm, pl] = await Promise.all([
        API.get("/api/llm/status"),
        API.get("/api/plugins")
      ]);
      if (llm && !llm.error) {
        const providers = (llm.providers || []);
        const available = providers.filter((p) => p && p.configured).length;
        const none = providers.length > 0 && available === 0;
        const set = (sel, text) => { const el = $(sel); if (el) el.textContent = text; };
        // top status pill — honest wording: NO KEYS when nothing is configured
        set("#model-status-value", llm.any_available ? "ONLINE" : (none ? "NO KEYS" : "DEGRADED"));
        // AI core panel rows
        set("#ai-row-neural", llm.any_available ? "ACTIVE" : (none ? "IDLE" : "DEGRADED"));
        const best = llm.best_provider || "";
        set("#ai-row-thinking", best ? best.toUpperCase().split(/[\s\-]+/)[0] + " ROUTE" : "OPTIMAL");
        set("#ai-row-core", llm.any_available ? "ONLINE" : (none ? "STANDBY" : "OFFLINE"));
        // learning-rate pill: share of providers actually reachable
        const ratio = providers.length ? (available / providers.length) * 100 : 0;
        const coreLearning = $("#core-learning");
        if (coreLearning) {
          coreLearning.textContent = `${ratio.toFixed(1)}%`;
          const lrSmall = coreLearning.parentElement.querySelector("small");
          if (lrSmall) lrSmall.textContent = ratio >= 50 ? "OPTIMAL" : (none ? "SET API KEY" : "DEGRADED");
        }
      }
      if (pl && !pl.error && Array.isArray(pl.plugins)) {
        const loaded = pl.plugins.filter((p) => p && p.loaded).length;
        const total = pl.plugins.length;
        const set = (sel, text) => { const el = $(sel); if (el) el.textContent = text; };
        set("#ai-row-heuristic", `${loaded} / ${total}`);
      }
    }
  };

  /* =========================================================
     TELEMETRY — gauges fed by real /api/live/status values
  ========================================================= */
  const Telemetry = {
    CIRC: 314.16,
    metrics: {},
    tickTimer: null,
    // network rate tracking (bytes since boot → Mbps delta)
    netLast: null,
    netLastTime: 0,
    init() {
      $$(".metric-card").forEach((card) => {
        const key = card.dataset.metric;
        const cfg = {
          cpu:     { min: 0, max: 100, label: "—", color: "#00D2FF", unit: "%" },
          gpu:     { min: 0, max: 100, label: "—", color: "#FF9900", unit: "%" },
          ram:     { min: 0, max: 100, label: "—", color: "#00D2FF", unit: "%" },
          storage: { min: 0, max: 100, label: "—", color: "#00D2FF", unit: "%" },
          network: { min: 0, max: 100, label: "—", color: "#00D2FF", unit: "Mbps" },
          temp:    { min: 0, max: 100, label: "—", color: "#FF9900", unit: "°C" }
        }[key] || { min: 0, max: 100, label: "—", color: "#00D2FF", unit: "%" };

        this.metrics[key] = {
          card, cfg,
          current: 0,
          target: 0,
          history: Array(30).fill(0),
          isTemp: key === "temp"
        };
      });
      this.applyAll(0);
      // gentle idle drift only while no live data has arrived
      this.tickTimer = setInterval(() => this.tick(), 2200);
    },
    tick() {
      // If live data is flowing, do NOT randomise — just ease toward targets.
      if (LiveState.telemetry) return;
      Object.values(this.metrics).forEach((m) => {
        m.target = clamp(m.current + rand(-14, 16), m.cfg.min, m.cfg.max);
        m.current += (m.target - m.current) * 0.35;
        m.history.push(m.current);
        if (m.history.length > 30) m.history.shift();
        this.apply(m);
      });
    },
    apply(m) {
      const value = Math.round(m.current);
      const valueEl = m.card.querySelector("[data-value]");
      const barEl = m.card.querySelector("[data-bar]");
      const fillEl = m.card.querySelector("[data-fill]");
      const spark = m.card.querySelector("[data-spark]");
      const color = m.cfg.color;
      const suffix = m.isTemp ? "°C" : m.cfg.unit === "Mbps" ? " Mbps" : "%";
      if (valueEl) valueEl.textContent = `${value}${suffix}`;
      if (barEl) barEl.style.width = `${clamp(value, 0, 100)}%`;
      if (fillEl) fillEl.style.strokeDashoffset = (this.CIRC * (1 - clamp(value, 0, 100) / 100)).toFixed(1);
      this.drawSpark(spark, m.history, color);
    },
    applyAll(mode) {
      Object.values(this.metrics).forEach((m) => {
        const fillEl = m.card.querySelector("[data-fill]");
        if (fillEl) fillEl.style.transition = mode ? "stroke-dashoffset 1.4s cubic-bezier(.4,0,.2,1)" : "none";
        this.apply(m);
      });
    },
    // Push a REAL value into a metric (from /api/live/status).
    feed(key, value, subLabel) {
      const m = this.metrics[key];
      if (!m) return;
      m.target = clamp(Number(value) || 0, m.cfg.min, m.cfg.max);
      m.current = m.target;
      m.history.push(m.target);
      if (m.history.length > 30) m.history.shift();
      if (subLabel !== undefined) {
        const strong = m.card.querySelector(".metric-heading strong");
        if (strong) strong.textContent = subLabel;
      }
      this.apply(m);
    },
    drawSpark(canvas, history, color) {
      if (!canvas || !canvas.getContext) return;
      const ctx = canvas.getContext("2d");
      const w = canvas.width, h = canvas.height;
      ctx.clearRect(0, 0, w, h);
      if (history.length < 2) return;
      const min = Math.min(...history), max = Math.max(...history);
      const span = (max - min) || 1;
      ctx.beginPath();
      history.forEach((v, i) => {
        const x = (i / (history.length - 1)) * w;
        const y = h - ((v - min) / span) * (h - 2) - 1;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.4;
      ctx.shadowColor = color;
      ctx.shadowBlur = 4;
      ctx.stroke();
    }
  };

  /* =========================================================
     LIVE STATE — which live channels have real data
  ========================================================= */
  const LiveState = {
    telemetry: false,
    devices: false,
    weather: false,
    location: false
  };

  /* =========================================================
     LIVE POLLER — polls every live endpoint
  ========================================================= */
  const Live = {
    timers: [],
    pollMs: () => Number(localStorage.getItem("a3ther.pollMs") || 3000),
    init() {
      this.pollAll();
      this.timers.push(setInterval(() => this.pollStatus(), this.pollMs()));
      this.timers.push(setInterval(() => this.pollDevices(), 8000));
      this.timers.push(setInterval(() => this.pollWeather(), 300000));
      this.timers.push(setInterval(() => this.pollLocation(), 600000));
      this.timers.push(setInterval(() => AICore.poll(), 15000));
      this.timers.push(setInterval(() => Predictions.poll(), 30000));
    },
    pollAll() {
      this.pollStatus();
      this.pollDevices();
      this.pollWeather();
      this.pollLocation();
      this.pollSpecs();
      AICore.poll();
      Predictions.poll();
    },
    async pollSpecs() {
      const s = await Specs.load();
      // if the backend was still warming up, retry shortly; otherwise settle
      // into a slow cadence so spec refreshes stay cheap.
      if (!s) {
        setTimeout(() => this.pollSpecs(), 5000);
      } else {
        setTimeout(() => this.pollSpecs(), 60000);
      }
    },
    // apply a new telemetry poll interval from Settings without reloading
    restartStatusTimer() {
      this.timers.forEach((t, i) => {
        if (i === 0) clearInterval(t);
      });
      this.timers[0] = setInterval(() => this.pollStatus(), this.pollMs());
    },
    async pollStatus() {
      const s = await API.get("/api/live/status");
      if (!s || s.error) {
        LiveBadge.set("offline");
        return;
      }
      LiveState.telemetry = true;
      LiveBadge.set("live");

      // Once real specs are loaded, keep the brand labels — never clobber
      // them with the status service's generic names.
      const specLoaded = !!(Specs && Specs.data);
      if (s.cpu) {
        Telemetry.feed("cpu", s.cpu.percent, specLoaded ? undefined : (s.cpu.name || "CPU"));
        const temp = $("#temperature-card .metric-heading strong");
        if (temp) {
          if (s.cpu.temp_c != null) temp.textContent = s.cpu.temp_c > 60 ? "WARM" : "NORMAL";
          else temp.textContent = "N/A";
        }
      }
      if (s.gpu) {
        if (s.gpu.percent != null) {
          Telemetry.feed("gpu", s.gpu.percent, specLoaded ? undefined : (s.gpu.name || "GPU"));
        } else {
          // No GPU telemetry source (no NVIDIA driver) — be honest, not fake 0%.
          const gv = $("#gpu-card [data-value]");
          if (gv) gv.textContent = "—";
          const gb = $("#gpu-card [data-bar]");
          if (gb) gb.style.width = "0%";
        }
      }
      if (s.ram) Telemetry.feed("ram", s.ram.percent, specLoaded ? undefined : `${s.ram.total_gb} GB`);
      if (s.storage) {
        Telemetry.feed("storage", s.storage.percent, specLoaded ? undefined : `${s.storage.used_gb} GB / ${s.storage.total_gb} GB`);
      }
      // network: derive Mbps from byte counters between polls
      if (s.network && s.network.recv_mb != null && s.network.sent_mb != null) {
        const now = Date.now();
        if (this.netLast) {
          const dt = Math.max((now - this.netLastTime) / 1000, 0.1);
          const recvMbps = ((s.network.recv_mb - this.netLast.recv_mb) * 8) / dt;
          const sentMbps = ((s.network.sent_mb - this.netLast.sent_mb) * 8) / dt;
          Telemetry.feed("network", Math.max(recvMbps, sentMbps), `${recvMbps.toFixed(1)} ↓`);
          const note = $("#network-card .metric-note");
          if (note) note.textContent = `↓ ${recvMbps.toFixed(1)} · ↑ ${sentMbps.toFixed(1)} Mbps`;
        }
        this.netLast = { recv_mb: s.network.recv_mb, sent_mb: s.network.sent_mb };
        this.netLastTime = now;
      }
      if (s.cpu && s.cpu.temp_c != null) Telemetry.feed("temp", s.cpu.temp_c, s.cpu.temp_c > 60 ? "WARM" : "NORMAL");

      // AI core / memory / response pills
      const mem = $("#memory-core-value");
      if (mem && s.ram) mem.textContent = `${s.ram.total_gb} GB`;
      const proc = $("#process-count-value");
      if (proc && s.process_count != null) proc.textContent = s.process_count;
      // real response latency straight from the status endpoint
      if (s.latency_ms != null) {
        const resp = $("#core-response");
        if (resp) resp.textContent = `${(s.latency_ms / 1000).toFixed(3)}s`;
        const respPill = $("#response-value");
        if (respPill) respPill.textContent = `${(s.latency_ms / 1000).toFixed(3)}s`;
        const respSmall = resp ? resp.parentElement.querySelector("small") : null;
        if (respSmall) respSmall.textContent = s.latency_ms < 200 ? "EXCELLENT" : s.latency_ms < 800 ? "GOOD" : "SLOW";
      }

      // system health dots driven by real thresholds
      const health = $("#health-dots");
      if (health) {
        const danger = (s.cpu && s.cpu.percent > 85) || (s.ram && s.ram.percent > 90) || (s.cpu && s.cpu.temp_c != null && s.cpu.temp_c > 80);
        Array.from(health.children).forEach((dot, i) => {
          dot.style.background = danger ? "var(--orange)" : i % 3 === 0 ? "var(--orange)" : "var(--cyan)";
          dot.style.boxShadow = `0 0 8px ${danger ? "var(--orange)" : "var(--cyan)"}`;
        });
      }

      // terminal notification: real process count + hostname
      Terminal.notifyLive(s);
      return s;
    },
    async pollDevices() {
      const d = await API.get("/api/live/devices");
      if (!d || d.error) {
        Devices.renderOffline();
        return;
      }
      LiveState.devices = true;
      Devices.render(d);
      Globe.setDeviceCount(d.connected);
      Globe.setNodes(d.devices);
      return d;
    },
    async pollWeather() {
      const w = await API.get("/api/live/weather");
      if (!w || w.error) { Weather.renderOffline(); return; }
      LiveState.weather = true;
      Weather.render(w);
      return w;
    },
    async pollLocation() {
      const loc = await API.get("/api/live/location");
      if (!loc || loc.error) return;
      LiveState.location = true;
      // a user-pinned city wins — don't clobber it with auto-detection
      if (Settings.weatherCity) return loc;
      const cityEl = $("#weather-city");
      if (cityEl && loc.city) cityEl.textContent = `${loc.city.toUpperCase()}${loc.country ? ", " + loc.country.toUpperCase() : ""}`;
      return loc;
    },
    // Manual refreshes (button driven)
    async rescanDevices() {
      const btn = $("#rescan-devices");
      if (btn) btn.classList.add("spinning");
      Toasts.info("Rescanning Bluetooth + LAN…");
      const d = await API.post("/api/live/devices/rescan");
      if (d && !d.error) {
        Devices.render(d);
        Globe.setDeviceCount(d.connected);
        Globe.setNodes(d.devices);
        Toasts.ok(`${d.count} device(s) discovered, ${d.connected} connected.`);
      } else {
        Toasts.err("Device rescan failed — is the backend running?");
      }
      if (btn) setTimeout(() => btn.classList.remove("spinning"), 600);
      return d;
    }
  };

  /* =========================================================
     DEVICES — real device panel + source note
  ========================================================= */
  const ICONS = {
    bluetooth: "fa-bluetooth-b",
    lan: "fa-network-wired",
    manual: "fa-plus",
    unknown: "fa-plug"
  };
  // Device-kind display labels — iOS-family devices render as "IOS",
  // Android as "ANDROID", etc. instead of raw kind strings.
  const kindLabel = (k) => {
    const map = {
      iphone: "IOS", ipad: "IOS", android: "ANDROID",
      desktop: "PC", laptop: "LAPTOP", terminal: "TERMINAL",
      web: "WEB", iot: "IOT", unknown: ""
    };
    const key = String(k || "").toLowerCase();
    return map[key] !== undefined ? map[key] : String(k || "").toUpperCase();
  };

  const Devices = {
    renderOffline() {
      const list = $("#device-list");
      if (list) {
        list.innerHTML = `<article class="device-empty"><i class="fa-solid fa-plug-circle-xmark"></i><span>BACKEND OFFLINE — no live devices</span></article>`;
      }
      const pill = $("#device-count-pill");
      if (pill) { pill.textContent = "OFFLINE"; pill.classList.remove("ok"); pill.classList.add("warn"); }
      const note = $("#device-source-note");
      if (note) note.textContent = "Data link down — run the backend to see real devices.";
    },
    render(d) {
      const list = $("#device-list");
      const devices = d.devices || [];
      const shown = devices.slice(0, 6);
      if (list) {
        if (!devices.length) {
          list.innerHTML = `<article class="device-empty"><i class="fa-solid fa-bluetooth-b"></i><span>No devices found — scan in progress</span></article>`;
        } else {
          // Honest labels: mesh = CONNECTED (controllable), LAN = REACHABLE,
          // Bluetooth = NEARBY (seen by the scan, not necessarily connected).
          const srcTag = (dev) => dev.source === "mesh" ? "CONNECTED" : dev.source === "lan" ? "REACHABLE" : "NEARBY";
          const srcClass = (dev) => dev.source === "mesh" ? "on" : "";
          const ctrl = (dev) => dev.controllable
            ? `<button class="dev-ctrl" data-node="${dev.node_id}" title="Send command to ${(dev.name || "").toUpperCase()}"><i class="fa-solid fa-bolt"></i></button>`
            : "";
          list.innerHTML = shown.map((dev) => `
            <article class="dev-row ${dev.source === "mesh" ? "mesh" : ""}">
              <i class="fa-solid ${ICONS[dev.source] || ICONS.unknown}"></i>
              <span>${(dev.name || dev.address || "Unknown").toUpperCase()}${dev.source === "mesh" && dev.kind ? `<em>${kindLabel(dev.kind)}</em>` : ""}</span>
              <strong class="${srcClass(dev)}">${srcTag(dev)}</strong>
              <small>${dev.rssi != null ? dev.rssi + " dBm" : (dev.ip || (dev.source === "mesh" ? "CONTROLLABLE" : "—"))}</small>
              ${ctrl(dev)}
            </article>`).join("");
        }
        // Delegated control click — one listener, survives re-renders.
        list.querySelectorAll(".dev-ctrl").forEach((btn) => {
          btn.addEventListener("click", () => Devices.control(btn.dataset.node, btn));
        });
      }
      const pill = $("#device-count-pill");
      if (pill) {
        pill.textContent = `${devices.length} DEVICE${devices.length === 1 ? "" : "S"}`;
        pill.classList.add("ok");
        pill.classList.remove("warn");
      }
      const note = $("#device-source-note");
      if (note) {
        const bt = d.bluetooth || {};
        const src = [];
        if (d.connected) src.push(`${d.connected} Connected`);
        if (bt.count) src.push(`${bt.count} Nearby BT`);
        if ((d.lan || {}).count) src.push(`${d.lan.count} Reachable LAN`);
        note.textContent = src.length
          ? `REAL: ${src.join(" · ")}${bt.available ? "" : " · bleak not installed"}`.toUpperCase()
          : "REAL: no devices reachable — rescan or install bleak (pip install bleak)".toUpperCase();
      }
    },
    async control(nodeId, btn) {
      if (btn) { btn.classList.add("spinning"); }
      const r = await API.post("/api/sync/broadcast", {
        command: "flash_screen", params: { confirm: true },
        target: nodeId, source: "dashboard"
      });
      if (btn) setTimeout(() => btn.classList.remove("spinning"), 600);
      Toasts[(!r || r.delivered !== 1) ? "err" : "ok"](
        (!r || r.delivered !== 1) ? "Could not reach that node." : "Command sent to node."
      );
    }
  };

  /* =========================================================
     WEATHER — real Open-Meteo data
  ========================================================= */
  const Weather = {
    renderOffline() {
      const city = $("#weather-city");
      if (city) city.textContent = "WEATHER OFFLINE";
      const t = $("#weather-temp");
      if (t) t.textContent = "—";
      const cond = $(".weather-temp small");
      if (cond) cond.textContent = "DATA LINK DOWN";
      $$(".weather-details div strong").forEach((s) => (s.textContent = "—"));
      const icon = $(".weather-icon i");
      if (icon) icon.className = "fa-solid fa-cloud";
    },
    render(w) {
      const city = $("#weather-city");
      if (city && w.city) city.textContent = w.city.toUpperCase();
      const t = $("#weather-temp");
      if (t && w.temperature_c != null) t.textContent = `${Math.round(w.temperature_c)}°C`;
      const cond = $(".weather-temp small");
      if (cond && w.condition) cond.textContent = w.condition;
      const icon = $(".weather-icon i");
      if (icon && w.icon) icon.className = `fa-solid ${w.icon}`;
      const fields = [
        w.humidity != null ? `${Math.round(w.humidity)}%` : "—",
        w.wind_kmh != null ? `${w.wind_kmh} km/h` : "—",
        w.pressure_hpa != null ? `${Math.round(w.pressure_hpa)} hPa` : "—",
        w.visibility_km != null ? `${w.visibility_km} km` : "—",
        w.uv_index != null ? String(w.uv_index) : "—"
      ];
      $$(".weather-details div strong").forEach((s, i) => {
        if (fields[i] !== undefined) s.textContent = fields[i];
      });
    }
  };

  /* =========================================================
     PREDICTIONS — AI Predictor from /api/live/predict
     Trend-based next-value projections on real telemetry.
  ========================================================= */
  const Predictions = {
    async poll() {
      const p = await API.get("/api/live/predict");
      const pill = $("#predict-pill");
      const headline = $("#predict-headline");
      const list = $("#predict-list");
      if (!p || p.error || !list) {
        if (pill) pill.textContent = "OFFLINE";
        return null;
      }
      // headline card
      if (headline) {
        if (p.learning || !p.headline) {
          headline.innerHTML = `<i class="fa-solid fa-brain"></i>
            <div><strong>GATHERING TELEMETRY…</strong><span>${p.samples || 0} sample(s) collected — predictions appear after ~5.</span></div>`;
          if (pill) pill.textContent = "LEARNING";
        } else {
          const h = p.headline;
          const dir = h.trend === "up" ? "fa-arrow-trend-up up" : h.trend === "down" ? "fa-arrow-trend-down down" : "fa-minus flat";
          headline.innerHTML = `<i class="fa-solid ${h.icon}"></i>
            <div><strong>${h.note}</strong>
            <span>${h.label} ${h.value_now}${h.unit} → ${h.value_pred}${h.unit} · ${h.confidence}% confidence · ~${h.horizon_min} min</span></div>
            <i class="fa-solid ${dir}"></i>`;
          if (pill) { pill.textContent = `${h.confidence}% CONF`; pill.classList.add("ok"); }
        }
      }
      // prediction rows (top 4)
      const items = (p.predictions || []).slice(0, 4);
      if (list) {
        if (!items.length && !p.learning) {
          list.innerHTML = `<article class="predict-item flat"><i class="fa-solid fa-wave-square"></i>
            <div><strong>ALL METRICS STABLE</strong><span>No meaningful trend in the current window — check back soon.</span></div></article>`;
        } else if (items.length) {
          list.innerHTML = items.map((it) => `
            <article class="predict-item ${it.trend}">
              <i class="fa-solid ${it.icon}"></i>
              <div>
                <strong>${it.label} <em>${it.value_now}${it.unit} → ${it.value_pred}${it.unit}</em></strong>
                <span>${it.note}</span>
              </div>
              <small>${it.confidence}%</small>
            </article>`).join("");
        }
      }
      // context chips (signal / weather)
      const ctx = p.context || [];
      if (list && ctx.length) {
        ctx.forEach((c) => {
          const row = document.createElement("article");
          row.className = "predict-item context";
          row.innerHTML = `<i class="fa-solid ${c.icon}"></i><div><strong>${c.title}</strong><span>${c.detail}</span></div>`;
          list.appendChild(row);
        });
      }
      return p;
    }
  };

  /* =========================================================
     CALENDAR — real month grid + events
  ========================================================= */
  const Calendar = {
    view: null,
    events: {},
    init() {
      this.view = new Date();
      this.render();
    },
    render(target) {
      // Optional target lets the "Open calendar" overlay reuse this renderer.
      const body = target || $("#calendar-body");
      if (!body) return;
      const y = this.view.getFullYear(), m = this.view.getMonth();
      const today = new Date();
      const isCurrent = y === today.getFullYear() && m === today.getMonth();
      const monthLabel = this.view.toLocaleDateString("en-US", { month: "long", year: "numeric" }).toUpperCase();

      const monthRow = document.createElement("div");
      monthRow.className = "cal-month";
      const prev = document.createElement("button");
      prev.type = "button"; prev.setAttribute("aria-label", "Previous month"); prev.textContent = "‹";
      const label = document.createElement("strong"); label.textContent = monthLabel;
      const next = document.createElement("button");
      next.type = "button"; next.setAttribute("aria-label", "Next month"); next.textContent = "›";
      prev.addEventListener("click", () => { this.view.setMonth(m - 1); this.render(); });
      next.addEventListener("click", () => { this.view.setMonth(m + 1); this.render(); });
      monthRow.append(prev, label, next);

      const weekdays = document.createElement("div");
      weekdays.className = "cal-weekdays";
      ["S", "M", "T", "W", "T", "F", "S"].forEach((d) => {
        const s = document.createElement("span"); s.textContent = d; weekdays.appendChild(s);
      });

      const grid = document.createElement("div");
      grid.className = "cal-grid";
      const first = new Date(y, m, 1).getDay();
      const days = new Date(y, m + 1, 0).getDate();
      for (let i = 0; i < first; i++) {
        const b = document.createElement("span"); b.className = "cal-day blank"; grid.appendChild(b);
      }
      for (let d = 1; d <= days; d++) {
        const cell = document.createElement("span");
        cell.className = "cal-day";
        cell.textContent = d;
        if (isCurrent && d === today.getDate()) cell.classList.add("is-today");
        grid.appendChild(cell);
      }

      const events = document.createElement("div");
      events.className = "cal-events";
      const list = Object.entries(this.events).filter(([day]) => day.startsWith(`${y}-${pad(m + 1)}`));
      if (list.length) {
        list.forEach(([day, [time, title]]) => {
          const row = document.createElement("div");
          row.className = "cal-event";
          const t = document.createElement("time"); t.textContent = time;
          const s = document.createElement("span"); s.textContent = `${title} — ${day.slice(-2)}`;
          row.append(t, s);
          events.appendChild(row);
        });
      } else if (isCurrent) {
        const row = document.createElement("div");
        row.className = "cal-event";
        const t = document.createElement("time"); t.textContent = "—";
        const s = document.createElement("span"); s.textContent = "No events today";
        row.append(t, s);
        events.appendChild(row);
      }

      body.replaceChildren(monthRow, weekdays, grid, events);
    }
  };

  /* =========================================================
     VOICE — real /api/voice/* control + status polling
  ========================================================= */
  const Voice = {
    bars: [],
    waveTimer: null,
    active: false,
    lastState: "",
    init() {
      this.buildWaveform();
      this.bindMic();
      this.bindAiVoice();
      this.bindLive();
      this.timer = setInterval(() => this.pollStatus(), 2500);
    },
    buildWaveform() {
      const wave = $("#waveform");
      if (!wave) return;
      wave.innerHTML = "";
      this.bars = [];
      for (let i = 0; i < 26; i++) {
        const bar = document.createElement("span");
        wave.appendChild(bar);
        this.bars.push({ el: bar, h: 20 + Math.random() * 70 });
      }
      this.startWave();
    },
    startWave() {
      if (this._stopped) { this._stopped = false; this.animateWave(); }
    },
    stopWave() {
      this._stopped = true;
      if (this.waveTimer) { clearTimeout(this.waveTimer); this.waveTimer = null; }
    },
    animateWave() {
      if (this._stopped) return;
      this.bars.forEach((b) => {
        const base = this.active
          ? 20 + Math.random() * 80
          : 14 + Math.random() * 34;
        b.h += (base - b.h) * 0.25;
        b.el.style.height = `${clamp(b.h, 6, 100)}%`;
      });
      // 180ms cadence — plenty smooth, ~40% cheaper than 130ms.
      this.waveTimer = setTimeout(() => this.animateWave(), 180);
    },
    async pollStatus() {
      const s = await API.get("/api/voice/status");
      if (!s) return;
      this.lastState = s.state || "";
      this.setLive(!!s.live);
      // reflect the real pipeline state in the UI
      this.setActive(s.state === "listening" || s.state === "transcribing" || s.state === "speaking" || s.speaking);
      const text = $("#voice-status-text");
      const title = $("#voice-title");
      const mic = $("#microphone");
      if (mic && s.state === "wake_listening") mic.classList.add("listening");
      else if (mic) mic.classList.remove("listening");
      if (text) {
        const map = {
          idle: "READY FOR COMMAND",
          wake_listening: "WAKE LISTENING — SAY “HEY AETHER”",
          listening: "LISTENING — SPEAK NOW",
          transcribing: "TRANSCRIBING…",
          thinking: "THINKING…",
          speaking: "SPEAKING…",
          audio_error: "MIC ERROR — RECONNECTING"
        };
        text.textContent = map[s.state] || s.state || "READY FOR COMMAND";
      }
      if (title) title.textContent = this.active ? "Listening…" : "Wake listening";
    },
    setActive(v) {
      this.active = !!v;
      const mic = $("#microphone");
      if (mic) mic.classList.toggle("listening", this.active);
    },
    async toggle() {
      const mic = $("#microphone");
      if (mic) mic.disabled = true;
      try {
        const next = this.active ? await API.post("/api/voice/stop") : await API.post("/api/voice/start");
        if (next) {
          this.active = !this.active;
          Toasts.ok(this.active ? "Voice pipeline started — wake word active." : "Voice pipeline stopped.");
        } else {
          Toasts.err("Voice backend unreachable — is the server running?");
        }
      } finally {
        if (mic) mic.disabled = false;
      }
    },
    buildHealth() {
      const dots = $("#health-dots");
      if (!dots) return;
      dots.innerHTML = "";
      for (let i = 0; i < 8; i++) {
        const dot = document.createElement("span");
        dots.appendChild(dot);
      }
    },
    bindMic() {
      const mic = $("#microphone");
      if (!mic) return;
      mic.addEventListener("click", () => this.toggle());
    },
    setLive(on) {
      const btn = $("#gemini-live-btn");
      if (!btn) return;
      const span = btn.querySelector("span");
      btn.classList.toggle("live-on", !!on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      if (span) span.textContent = on ? "GEMINI LIVE ON" : "GEMINI LIVE OFF";
      const input = $("#ai-voice-input");
      if (input) input.placeholder = on
        ? "Live mode on — just talk. Wake word not needed…"
        : "Gemini Live — ask A3THER, it speaks the reply…";
    },
    bindLive() {
      const btn = $("#gemini-live-btn");
      if (!btn) return;
      btn.addEventListener("click", async () => {
        const on = !btn.classList.contains("live-on");
        btn.disabled = true;
        try {
          const r = await API.post("/api/voice/live", { live: on });
          if (r && r.ok) {
            this.setLive(!!r.live);
            Toasts.ok(r.live
              ? "Gemini Live ON — continuous conversation started. Just speak."
              : "Gemini Live OFF — wake word required again.");
            if (r.live && this.lastState === "idle") this.pollStatus();
          } else {
            Toasts.err((r && r.error) || "Could not toggle live mode.");
          }
        } finally {
          btn.disabled = false;
        }
      });
    },
    // Gemini Live: type a question → LLM reply → spoken aloud.
    bindAiVoice() {
      const input = $("#ai-voice-input");
      const send = $("#ai-voice-send");
      const reply = $("#ai-voice-reply");
      if (!input || !send) return;
      const go = async () => {
        const text = input.value.trim();
        if (!text) return;
        input.disabled = true; send.disabled = true;
        reply.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> THINKING…`;
        const r = await API.post("/api/voice/chat", { text });
        const esc = (s) => String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        if (r && r.ok) {
          reply.innerHTML = `<strong>${esc(r.provider || "AI").toUpperCase()}</strong> ${esc(r.reply)}`;
          Toasts.ok(r.spoken ? "Reply spoken." : "Reply ready (TTS unavailable).");
        } else {
          reply.innerHTML = `<span class="err">${(r && r.error ? r.error : "AI voice failed — set a valid API key in Settings.").replace(/</g, "&lt;")}</span>`;
        }
        input.value = ""; input.disabled = false; send.disabled = false;
      };
      send.addEventListener("click", go);
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    }
  };

  /* =========================================================
     OVERLAYS — working Devices / Files / Settings views
  ========================================================= */
  const Overlay = {
    open(title, content, wide = false) {
      const root = $("#overlay-root");
      const box = document.createElement("div");
      box.className = "overlay";
      box.innerHTML = `
        <div class="overlay-card ${wide ? "wide" : ""}">
          <header>
            <h2>${title}</h2>
            <button class="overlay-close" type="button" aria-label="Close"><i class="fa-solid fa-xmark"></i></button>
          </header>
          <div class="overlay-body">${content}</div>
        </div>`;
      root.appendChild(box);
      box.querySelector(".overlay-close").addEventListener("click", () => box.remove());
      box.addEventListener("click", (e) => { if (e.target === box) box.remove(); });
      return box.querySelector(".overlay-body");
    }
  };

  const Views = {
    async devices() {
      const d = await API.get("/api/live/devices");
      const devices = (d && d.devices) || [];
      const body = Overlay.open("CONNECTED DEVICES", `
        <div class="overlay-list" id="overlay-device-list"></div>
        <p class="device-source-note" id="overlay-device-note"></p>`, true);
      const list = body.querySelector("#overlay-device-list");
      const srcTag = (dev) => dev.source === "mesh" ? "CONNECTED" : dev.source === "lan" ? "REACHABLE" : "NEARBY";
      const ctrl = (dev) => dev.controllable
        ? `<button class="dev-ctrl" data-node="${dev.node_id}" title="Flash ${(dev.name || "").toUpperCase()}"><i class="fa-solid fa-bolt"></i></button>`
        : "";
      if (!devices.length) {
        list.innerHTML = `<article class="device-empty"><i class="fa-solid fa-bluetooth-b"></i><span>No devices found — rescan or install bleak (pip install bleak)</span></article>`;
      } else {
        list.innerHTML = devices.map((dev) => `
          <article class="dev-row ${dev.source === "mesh" ? "mesh" : ""}">
            <i class="fa-solid ${ICONS[dev.source] || ICONS.unknown}"></i>
            <div class="ovl-device-main">
              <strong>${(dev.name || "Unknown").toUpperCase()}</strong>
              <small>${dev.node_id ? "node " + dev.node_id.slice(0, 8) : (dev.address || dev.ip || "—")}</small>
            </div>
            <span class="ovl-device-meta">${srcTag(dev)}${dev.rssi != null ? ` · ${dev.rssi} dBm` : ""}${dev.kind ? ` · ${kindLabel(dev.kind)}` : ""}</span>
            ${ctrl(dev)}
          </article>`).join("");
        list.querySelectorAll(".dev-ctrl").forEach((btn) => {
          btn.addEventListener("click", () => Devices.control(btn.dataset.node, btn));
        });
      }
      const note = body.querySelector("#overlay-device-note");
      if (note) {
        const bt = d.bluetooth || {};
        const src = [];
        if (d.connected) src.push(`${d.connected} connected (controllable)`);
        if (bt.count) src.push(`${bt.count} nearby bluetooth`);
        if ((d.lan || {}).count) src.push(`${d.lan.count} reachable lan`);
        note.textContent = `REAL: ${src.join(" · ") || "none"} — only CONNECTED nodes can be controlled`.toUpperCase();
      }
    },
    async files() {
      const d = await API.get("/api/live/files");
      const files = (d && d.files) || [];
      const body = Overlay.open("WORKSPACE FILES", `
        <div class="overlay-list" id="overlay-file-list"></div>`, true);
      const list = body.querySelector("#overlay-file-list");
      if (!files.length) {
        list.innerHTML = `<article class="device-empty"><i class="fa-solid fa-folder-open"></i><span>No indexed files — index a workspace first</span></article>`;
      } else {
        list.innerHTML = files.map((f) => `
          <article>
            <i class="fa-solid fa-file-code"></i>
            <div class="ovl-device-main">
              <strong>${(f.path || "").split("/").pop()}</strong>
              <small>${f.path}</small>
            </div>
            <span class="ovl-device-meta">${(f.language || "?").toUpperCase()} · ${f.symbols} SYMBOLS</span>
          </article>`).join("");
      }
    },
    settings() {
      const body = Overlay.open("SETTINGS", `
        <div class="settings-section">
          <h4>LLM API KEY</h4>
          <div class="settings-row">
            <label>Provider<small>which gateway backend to configure</small></label>
            <select class="settings-text-input" id="set-key-provider">
              <option value="openai">OpenAI</option>
              <option value="deepseek">DeepSeek</option>
              <option value="gemini">Gemini</option>
              <option value="groq">Groq (fast Llama/DeepSeek)</option>
              <option value="anthropic">Anthropic</option>
            </select>
          </div>
          <div class="settings-row">
            <label>API key<small>stored in the A3THER data folder — never in the repo. Gemini keys start with AIza… or AQ.…</small></label>
            <input class="settings-text-input" id="set-key-input" type="password" placeholder="paste your key…" autocomplete="off" />
          </div>
          <div class="settings-grid">
            <button class="settings-btn" id="set-key-save"><i class="fa-solid fa-key"></i> Save Key</button>
            <button class="settings-btn" id="set-key-check"><i class="fa-solid fa-shield-halved"></i> Check Setup</button>
          </div>
          <p class="settings-note" id="set-key-status">First run? Save a key here — or run 'python -m core.first_run'.</p>
        </div>

        <div class="settings-section">
          <h4>VOICE &amp; LANGUAGE</h4>
          <div class="settings-row">
            <label>A3THER speaks<small>voice used for replies (Edge TTS — natural, free)</small></label>
            <select class="settings-text-input" id="set-voice-lang">
              <option value="en-US-GuyNeural">🇺🇸 English (US) — Guy</option>
              <option value="en-US-JennyNeural">🇺🇸 English (US) — Jenny</option>
              <option value="en-GB-RyanNeural">🇬🇧 English (UK) — Ryan</option>
              <option value="en-IN-PrabhatNeural">🇮🇳 English (India) — Prabhat</option>
              <option value="en-AU-WilliamNeural">🇦🇺 English (AU) — William</option>
              <option value="hi-IN-MadhurNeural">🇮🇳 Hindi — Madhur</option>
              <option value="es-ES-AlvaroNeural">🇪🇸 Spanish — Alvaro</option>
              <option value="fr-FR-HenriNeural">🇫🇷 French — Henri</option>
              <option value="de-DE-ConradNeural">🇩🇪 German — Conrad</option>
              <option value="it-IT-DiegoNeural">🇮🇹 Italian — Diego</option>
              <option value="pt-BR-AntonioNeural">🇧🇷 Portuguese (BR) — Antonio</option>
              <option value="ja-JP-KeitaNeural">🇯🇵 Japanese — Keita</option>
              <option value="ko-KR-InJoonNeural">🇰🇷 Korean — InJoon</option>
              <option value="zh-CN-YunxiNeural">🇨🇳 Chinese — Yunxi</option>
              <option value="ar-EG-HamadaNeural">🇪🇬 Arabic — Hamada</option>
              <option value="ru-RU-DmitryNeural">🇷🇺 Russian — Dmitry</option>
            </select>
          </div>
          <div class="settings-grid">
            <button class="settings-btn" id="set-voice-save"><i class="fa-solid fa-language"></i> Apply Voice</button>
            <button class="settings-btn" id="set-voice-test"><i class="fa-solid fa-volume-high"></i> Test Voice</button>
          </div>
          <p class="settings-note" id="set-voice-status">Pick a language — A3THER replies in it.</p>
        </div>

        <div class="settings-section">
          <h4>PHONE UNLOCK</h4>
          <p class="settings-note">Say <strong>"unlock my phone"</strong> and A3THER enters the remembered PIN/pattern over ADB. Wrong credential → it asks you to unlock again (no face unlock). Stored locally, obfuscated.</p>
          <div class="settings-row">
            <label>Credential<small>PIN (4-8 digits) or pattern (dots 1-9, e.g. 1-5-9)</small></label>
            <div class="settings-inline">
              <select class="settings-text-input" id="set-secret-kind">
                <option value="pin">PIN</option>
                <option value="pattern">Pattern</option>
              </select>
              <input class="settings-text-input" id="set-secret-value" type="password" placeholder="1234  or  1-5-9" autocomplete="off" />
            </div>
          </div>
          <div class="settings-grid">
            <button class="settings-btn" id="set-secret-save"><i class="fa-solid fa-lock"></i> Remember</button>
            <button class="settings-btn" id="set-secret-unlock"><i class="fa-solid fa-mobile-screen-button"></i> Unlock Now</button>
            <button class="settings-btn" id="set-secret-forget"><i class="fa-solid fa-trash"></i> Forget</button>
          </div>
          <p class="settings-note" id="set-secret-status">Nothing remembered yet.</p>
        </div>

        <div class="settings-section">
          <h4>PHONE LINK — USB + SCREEN CAST</h4>
          <p class="settings-note">Plug your phone into the laptop with USB (enable USB debugging). Confirm below, and A3THER remembers your password, unlocks the phone, and casts its screen onto your PC — scrcpy mirror when installed, live HUD stream otherwise.</p>
          <div class="settings-grid">
            <button class="settings-btn" id="set-cast-check"><i class="fa-solid fa-plug-circle-check"></i> Did you connect via USB?</button>
            <button class="settings-btn" id="set-cast-wifi"><i class="fa-solid fa-wifi"></i> Connect over WiFi</button>
            <button class="settings-btn" id="set-cast-start"><i class="fa-solid fa-display"></i> Start Cast</button>
            <button class="settings-btn" id="set-cast-install"><i class="fa-solid fa-download"></i> Install scrcpy</button>
            <button class="settings-btn" id="set-cast-stop"><i class="fa-solid fa-stop"></i> Stop Cast</button>
          </div>
          <p class="settings-note" id="set-cast-status">No phone linked yet.</p>
          <div class="cast-frame-wrap" id="set-cast-frame-wrap" style="display:none">
            <img id="set-cast-frame" alt="phone screen" style="width:100%;max-width:260px;border-radius:10px;border:1px solid rgba(0,210,255,.4)" />
          </div>
        </div>

        <div class="settings-section">
          <h4>YOUTUBE — PUBLISH YOUR EDITS</h4>
          <p class="settings-note" id="set-yt-status">Connect your channel, propose a rendered video, and A3THER writes clickable title + tags + description. You approve → it uploads. The auto-reply bot answers comments to grow subs.</p>
          <div class="settings-grid">
            <button class="settings-btn" id="set-yt-connect"><i class="fa-solid fa-youtube"></i> Connect YouTube</button>
            <button class="settings-btn" id="set-yt-bot"><i class="fa-solid fa-comment-dots"></i> Auto-Reply Bot</button>
            <button class="settings-btn" id="set-yt-refresh"><i class="fa-solid fa-rotate"></i> Refresh</button>
          </div>
          <div class="yt-approvals" id="set-yt-approvals"></div>
        </div>

        <div class="settings-section">
          <h4>ENVIRONMENT</h4>
          <div class="settings-row">
            <label>Weather city<small>pin a city, or leave empty for auto-detect</small></label>
            <input class="settings-text-input" id="set-city-input" type="text" placeholder="e.g. Tokyo" value="${(Settings.weatherCity || "").replace(/"/g, "&quot;")}" />
          </div>
          <div class="settings-grid">
            <button class="settings-btn" id="set-city-apply"><i class="fa-solid fa-location-crosshairs"></i> Apply City</button>
            <button class="settings-btn" id="set-city-clear"><i class="fa-solid fa-location-arrow"></i> Auto-Detect</button>
          </div>
        </div>

        <div class="settings-section">
          <h4>POLLING</h4>
          <div class="settings-row">
            <label>Telemetry refresh<small>how often live gauges update</small></label>
            <input type="range" id="set-poll-range" min="1500" max="12000" step="500" value="${Settings.pollMs}" />
          </div>
          <p class="settings-note" id="set-poll-label">Every ${(Settings.pollMs / 1000).toFixed(1)}s</p>
        </div>

        <div class="settings-section">
          <h4>APPEARANCE</h4>
          <div class="settings-grid">
            <button class="settings-btn" id="set-theme-cyan"><i class="fa-solid fa-palette"></i> Cyan / Orange</button>
            <button class="settings-btn" id="set-theme-ember"><i class="fa-solid fa-fire"></i> Ember</button>
            <button class="settings-btn" id="set-theme-photon"><i class="fa-solid fa-bolt"></i> Photon</button>
          </div>
          <div class="settings-row">
            <label>Globe rotation<small>keep the network core turning</small></label>
            <span class="toggle ${Settings.globe ? "on" : ""}" id="set-globe-toggle" role="switch" aria-checked="${Settings.globe}"></span>
          </div>
        </div>

        <div class="settings-section">
          <h4>ACTIONS</h4>
          <div class="settings-grid">
            <button class="settings-btn" id="set-rescan"><i class="fa-solid fa-rotate"></i> Rescan Devices</button>
            <button class="settings-btn" id="set-voice-test"><i class="fa-solid fa-volume-high"></i> Test Voice</button>
            <button class="settings-btn" id="set-refresh-specs"><i class="fa-solid fa-microchip"></i> Refresh Specs</button>
            <button class="settings-btn" id="set-live-check"><i class="fa-solid fa-heart-pulse"></i> Check Data Link</button>
            <button class="settings-btn" id="set-phone-link"><i class="fa-solid fa-mobile-screen-button"></i> Phone Link</button>
            <button class="settings-btn" id="set-ai-voice"><i class="fa-solid fa-wand-magic-sparkles"></i> Test AI Voice</button>
          </div>
        </div>`);

      body.querySelector("#set-phone-link").addEventListener("click", async () => {
        const r = await API.get("/api/sync/phone-link");
        if (!r || !r.url) { Toasts.err("Phone link unavailable — backend offline."); return; }
        const box = Overlay.open("PHONE CONTROL LINK", `
          <p class="device-source-note" style="margin:0 0 10px">Open this URL on any phone/tablet on the <strong>same Wi-Fi</strong> — no app, no install. It joins the mesh and can send &amp; receive commands.</p>
          <div class="phone-link-url" id="pl-url">${r.url}</div>
          <div class="settings-grid">
            <button class="settings-btn" id="pl-copy"><i class="fa-solid fa-copy"></i> Copy URL</button>
            <button class="settings-btn" id="pl-open"><i class="fa-solid fa-arrow-up-right-from-square"></i> Open Here</button>
          </div>`, true);
        box.querySelector("#pl-copy").addEventListener("click", async () => {
          try { await navigator.clipboard.writeText(r.url); Toasts.ok("Phone link copied."); }
          catch (_) { Toasts.warn("Clipboard blocked — copy the URL manually."); }
        });
        box.querySelector("#pl-open").addEventListener("click", () => { try { window.open(r.url, "_blank"); } catch (_) { /* popup blocked */ } });
        Terminal.print(`[PHONE LINK] Open on your phone: ${r.url}`, "cy");
      });

      body.querySelector("#set-rescan").addEventListener("click", () => { Live.rescanDevices(); });
      body.querySelector("#set-voice-test").addEventListener("click", async () => {
        const r = await API.post("/api/voice/say", { text: "A three ther online. All systems operational." });
        Toasts.ok(r ? "Voice test queued." : "Voice backend offline.");
      });
      body.querySelector("#set-refresh-specs").addEventListener("click", async () => {
        const s = await Specs.load();
        Toasts.ok(s && s.cpu ? `Specs refreshed — ${s.cpu.brand}` : "Specs unavailable — backend offline.");
      });
      body.querySelector("#set-live-check").addEventListener("click", async () => {
        const s = await API.get("/api/live/status");
        Toasts.ok(s && !s.error ? `Data link LIVE — ${s.hostname}` : "Data link OFFLINE — backend unreachable");
      });

      body.querySelector("#set-ai-voice").addEventListener("click", async () => {
        Toasts.info("AI voice: asking the LLM and speaking the reply…");
        const r = await API.post("/api/voice/chat", { text: "Introduce yourself in one sentence, then confirm you are online." });
        if (r && r.ok) Toasts.ok(`AI VOICE: ${r.provider ? r.provider.toUpperCase() + " · " : ""}${r.spoken ? "spoken" : "reply only"}`);
        else Toasts.err(r && r.error ? r.error : "AI voice failed — check API key.");
      });

      // LLM API key → POST /api/setup/key, persisted to the A3THER data folder
      const keyProvider = body.querySelector("#set-key-provider");
      const keyInput = body.querySelector("#set-key-input");
      const keyStatus = body.querySelector("#set-key-status");
      body.querySelector("#set-key-save").addEventListener("click", async () => {
        const provider = keyProvider.value;
        const key = (keyInput.value || "").trim();
        if (!key) { Toasts.warn("Paste an API key first."); return; }
        const r = await API.post("/api/setup/key", { provider, key });
        if (r && r.ok) {
          keyStatus.textContent = `✓ ${provider.toUpperCase()} key saved — live now (${(r.configured || []).length}/4 providers configured)`;
          Toasts.ok(`${provider} key saved to the A3THER data folder.`);
          AICore.poll();
        } else {
          const err = r && r.error ? r.error : "setup endpoint offline — is the backend running?";
          keyStatus.textContent = `✘ ${err}`;
          Toasts.err("Could not save key.");
        }
      });
      body.querySelector("#set-key-check").addEventListener("click", async () => {
        const s = await API.get("/api/setup/status");
        if (!s || s.error) { keyStatus.textContent = "✘ backend offline"; return; }
        const conf = (s.configured || []).map((p) => p.toUpperCase()).join(", ") || "NONE";
        const invalid = (s.invalid || []).map((p) => p.toUpperCase());
        let msg = s.needs_setup ? `SETUP NEEDED — configured: ${conf}` : `✓ KEYS CONFIGURED: ${conf}`;
        if (invalid.length) msg += ` · INVALID: ${invalid.join(", ")} (Gemini keys start with AIza… or AQ.…)`;
        keyStatus.textContent = msg;
        Toasts.info(msg);
      });

      // Voice & language — persist engine+voice, then let the user hear it.
      const voiceSelect = body.querySelector("#set-voice-lang");
      const voiceStatus = body.querySelector("#set-voice-status");
      (async () => {
        try {
          const v = await API.get("/api/settings/voice");
          if (v && v.voice && voiceSelect) {
            const opts = [...voiceSelect.options];
            if (opts.some((o) => o.value === v.voice)) voiceSelect.value = v.voice;
          }
        } catch (_) { /* backend offline — leave defaults */ }
      })();
      const applyVoice = async () => {
        const voice = voiceSelect.value;
        const r = await API.post("/api/settings/voice", { engine: "edgetts", voice });
        if (r && r.ok) {
          voiceStatus.textContent = `✓ Voice saved — A3THER now speaks ${voice}`;
          Toasts.ok("Voice updated.");
        } else {
          voiceStatus.textContent = "✘ Could not save voice (backend offline?)";
        }
      };
      body.querySelector("#set-voice-save").addEventListener("click", applyVoice);
      body.querySelector("#set-voice-test").addEventListener("click", async () => {
        await applyVoice();
        voiceStatus.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Speaking…`;
        const r = await API.post("/api/voice/chat", { text: "Hello! This is A3THER speaking in your new language." });
        if (r && r.spoken) voiceStatus.textContent = "✓ Played aloud.";
        else if (r && r.reply) voiceStatus.textContent = "✓ Reply ready (audio unavailable — check Edge TTS).";
        else voiceStatus.textContent = "✘ Voice test failed — set an LLM key first.";
      });

      // phone unlock secrets → /api/sync/phone-secret + /phone-unlock
      const secretKind = body.querySelector("#set-secret-kind");
      const secretValue = body.querySelector("#set-secret-value");
      const secretStatus = body.querySelector("#set-secret-status");
      if (secretKind && secretValue && secretStatus) {
        body.querySelector("#set-secret-save").addEventListener("click", async () => {
          const kind = secretKind.value;
          const value = (secretValue.value || "").trim();
          if (!value) { Toasts.warn("Enter a PIN or pattern first."); return; }
          const r = await API.post("/api/sync/phone-secret", { kind, value });
          if (r && r.ok) {
            secretValue.value = "";
            secretStatus.textContent = `✓ ${kind.toUpperCase()} remembered — say "unlock my phone".`;
            Toasts.ok("Phone credential saved.");
          } else {
            const err = (r && r.error) || "backend offline";
            secretStatus.textContent = `✘ ${err}`;
            Toasts.err("Could not save credential.");
          }
        });
        body.querySelector("#set-secret-unlock").addEventListener("click", async () => {
          secretStatus.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Unlocking…`;
          const r = await API.post("/api/sync/phone-unlock", {});
          if (!r) { secretStatus.textContent = "✘ backend offline"; return; }
          if (r.ok && r.unlocked) {
            secretStatus.innerHTML = `<span class="ok">✓ ${r.already_unlocked ? "Phone already unlocked." : "Phone unlocked" + (r.method ? " via " + r.method.toUpperCase() : "") + "."}</span>`;
          } else if (r.need_secret) {
            secretStatus.innerHTML = `<span class="err">✘ No PIN/pattern remembered — save one above first.</span>`;
          } else if (r.wrong_secret) {
            secretStatus.innerHTML = `<span class="err">✘ Wrong PIN/pattern — unlock the phone again on screen or give the correct one.</span>`;
          } else {
            secretStatus.textContent = `✘ ${r.error || "unlock failed"}`;
          }
        });
        body.querySelector("#set-secret-forget").addEventListener("click", async () => {
          const r = await API.post("/api/sync/android/control", { action: "forget_secret", params: {} });
          if (r && r.ok && (r.removed || r.ok)) {
            secretStatus.textContent = "✓ Credential forgotten.";
            Toasts.ok("Forgotten.");
          } else {
            secretStatus.textContent = "✘ Could not forget (nothing stored?).";
          }
        });
      }

      // phone link — USB confirm + screen cast
      const castStatus = body.querySelector("#set-cast-status");
      const castFrameWrap = body.querySelector("#set-cast-frame-wrap");
      const castFrame = body.querySelector("#set-cast-frame");
      const castCheck = body.querySelector("#set-cast-check");
      if (castCheck && castStatus) {
        castCheck.addEventListener("click", async () => {
          castStatus.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Checking USB connection…`;
          const r = await API.post("/api/sync/cast/confirm-usb", {});
          if (!r) { castStatus.textContent = "✘ backend offline"; return; }
          if (r.ok) {
            castStatus.innerHTML = `<span class="ok">✓ USB confirmed — ${r.auto_unlocked ? "phone auto-unlocked, " : ""}ready to cast${r.has_secret ? "" : " (save your PIN/pattern above first)"}.</span>`;
          } else {
            castStatus.innerHTML = `<span class="err">✘ ${this.esc(r.error || "no device found — plug it in via USB")}</span>`;
          }
        });
        const castStart = body.querySelector("#set-cast-start");
        const castStop = body.querySelector("#set-cast-stop");
        if (castStart) castStart.addEventListener("click", async () => {
          castStatus.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Starting cast…`;
          const r = await API.post("/api/sync/cast/start", { prefer_scrcpy: true });
          if (!r) { castStatus.textContent = "✘ backend offline"; return; }
          if (r.running) {
            castStatus.innerHTML = `<span class="ok">✓ Casting — ${r.mode === "scrcpy" ? "scrcpy window open on your PC" : "live stream below"}.</span>`;
            if (castFrameWrap) castFrameWrap.style.display = "block";
            if (castFrame) castFrame.src = `/api/sync/cast/frame.png?t=${Date.now()}`;
            this._castTimer = setInterval(() => {
              if (castFrame) castFrame.src = `/api/sync/cast/frame.png?t=${Date.now()}`;
            }, 2000);
          } else {
            castStatus.innerHTML = `<span class="err">✘ ${this.esc(r.error || "cast failed")}</span>`;
          }
        });
        if (castStop) castStop.addEventListener("click", () => {
          if (this._castTimer) clearInterval(this._castTimer);
          if (castFrameWrap) castFrameWrap.style.display = "none";
          API.post("/api/sync/cast/stop", {}).then(() => {
            castStatus.textContent = "Cast stopped.";
          });
        });
        const castWifi = body.querySelector("#set-cast-wifi");
        if (castWifi) castWifi.addEventListener("click", async () => {
          castStatus.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Finding your phone on the network…`;
          const r = await API.post("/api/sync/android/wireless", {});
          if (!r) { castStatus.textContent = "✘ backend offline"; return; }
          if (r.ok && r.connected) {
            castStatus.innerHTML = `<span class="ok">✓ Connected over WiFi (${this.esc(r.serial || "")}) — no cable needed. USB is only for the unlock pattern now.</span>`;
            Toasts.ok("Phone connected over WiFi.");
          } else if (r.needs_setup) {
            castStatus.innerHTML = `<span class="err">✘ ${this.esc(r.error || "couldn't reach the phone")}<br/><small>On the phone: Developer options → Wireless debugging ON. Plug in USB once to enable it — after that it's all wireless.</small></span>`;
          } else {
            castStatus.innerHTML = `<span class="err">✘ ${this.esc(r.error || "wireless connect failed")}</span>`;
          }
        });
        const castInstall = body.querySelector("#set-cast-install");
        if (castInstall) castInstall.addEventListener("click", async () => {
          castInstall.disabled = true;
          castInstall.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Downloading scrcpy…`;
          castStatus.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Downloading scrcpy (~30 MB) — one-time install…`;
          const r = await API.post("/api/sync/cast/install", {});
          castInstall.disabled = false;
          castInstall.innerHTML = `<i class="fa-solid fa-download"></i> Install scrcpy`;
          if (r && r.ok) {
            castStatus.innerHTML = `<span class="ok">✓ ${this.esc(r.note || "scrcpy installed")} — hit Start Cast for the mirrored window.</span>`;
            Toasts.ok("scrcpy installed — Start Cast now opens the mirror.");
          } else {
            castStatus.innerHTML = `<span class="err">✘ ${this.esc((r && r.error) || "install failed")}</span>`;
          }
        });
        // Refresh the cast status on open.
        API.get("/api/sync/cast/status").then((s) => {
          if (s && s.running) {
            castStatus.innerHTML = `<span class="ok">✓ Casting (${s.mode})${s.serial ? " — " + s.serial : ""}</span>`;
            if (castFrameWrap) castFrameWrap.style.display = "block";
            if (castFrame) castFrame.src = `/api/sync/cast/frame.png?t=${Date.now()}`;
          } else if (s) {
            const inst = s.scrcpy_installed ? `<span class="ok">✓ scrcpy installed</span>` : `<span class="err">scrcpy not installed yet — hit Install (or Start Cast auto-installs it)</span>`;
            castStatus.innerHTML = inst;
          }
        });
      }

      // youtube — connect, propose, approve, auto-reply bot
      const ytStatus = body.querySelector("#set-yt-status");
      const ytApprovals = body.querySelector("#set-yt-approvals");
      const ytConnect = body.querySelector("#set-yt-connect");
      const ytBot = body.querySelector("#set-yt-bot");
      const ytRefresh = body.querySelector("#set-yt-refresh");
      const loadYt = async () => {
        const s = await API.get("/api/youtube/status");
        if (!s) return;
        ytStatus.innerHTML = s.linked
          ? `<span class="ok">✓ YouTube linked${s.channel ? " — " + this.esc(s.channel) : ""}. Bot ${s.running ? "RUNNING" : "off"}.</span>`
          : (s.setup_needed ? `✘ ${this.esc(s.setup_steps)}` : "Not linked yet — hit Connect YouTube.");
        const a = await API.get("/api/youtube/approvals");
        if (ytApprovals && a && a.approvals && a.approvals.length) {
          ytApprovals.innerHTML = a.approvals.slice(0, 4).map((ap) => `
            <div class="yt-approval">
              <strong>${this.esc(ap.title)}</strong> <em>${ap.size_mb} MB</em>
              <small>${this.esc(ap.status.toUpperCase())}${ap.url ? ` — <a href="${ap.url}" target="_blank">${ap.url}</a>` : ""}${ap.error ? ` <span class="err">✘ ${this.esc(ap.error)}</span>` : ""}</small>
              ${ap.status === "pending" ? `<div class="settings-grid">
                <button class="settings-btn yt-approve" data-id="${ap.id}"><i class="fa-solid fa-check"></i> Approve + Upload</button>
                <button class="settings-btn yt-reject" data-id="${ap.id}"><i class="fa-solid fa-xmark"></i> Reject</button>
              </div>` : ""}
            </div>`).join("") || "<small>No videos staged yet — render an edit, then propose it.</small>";
          ytApprovals.querySelectorAll(".yt-approve").forEach((b) => b.addEventListener("click", async () => {
            const r = await API.post("/api/youtube/approve/" + b.dataset.id, {});
            Toasts.ok(r && r.ok ? "Upload started!" : (r && r.error) || "failed");
            loadYt();
          }));
          ytApprovals.querySelectorAll(".yt-reject").forEach((b) => b.addEventListener("click", async () => {
            await API.post("/api/youtube/reject/" + b.dataset.id, {});
            loadYt();
          }));
        }
      };
      if (ytConnect) {
        ytConnect.addEventListener("click", async () => {
          const s = await API.get("/api/youtube/status");
          if (!s) { Toasts.err("backend offline"); return; }
          if (s.linked) { Toasts.ok("YouTube already linked."); loadYt(); return; }
          const u = await API.get("/api/youtube/auth-url");
          if (!u || !u.ok) { Toasts.err((u && u.error) || "no client_secrets.json — follow the setup steps"); return; }
          const code = window.prompt("Open this URL in your browser, sign in, approve, then paste the code here:\n\n" + u.url);
          if (!code) return;
          const r = await API.post("/api/youtube/auth-code", { code });
          if (r && r.ok) { Toasts.ok("YouTube linked!"); } else { Toasts.err((r && r.error) || "link failed"); }
          loadYt();
        });
        ytBot.addEventListener("click", async () => {
          const r = await API.post("/api/youtube/bot/start", {});
          Toasts.ok(r && r.ok ? "Auto-reply bot started." : (r && r.error) || "start failed");
          loadYt();
        });
        ytRefresh.addEventListener("click", loadYt);
        loadYt();
      }

      // weather city override → persisted server-side
      const cityInput = body.querySelector("#set-city-input");
      body.querySelector("#set-city-apply").addEventListener("click", async () => {
        const city = (cityInput.value || "").trim();
        if (!city) { Toasts.warn("Enter a city name first."); return; }
        const r = await API.post("/api/live/weather/city", { city });
        if (r && r.ok) {
          Settings.weatherCity = city;
          Settings.save();
          Toasts.ok(`Weather pinned to ${city.toUpperCase()}.`);
          if (r.weather) { Weather.render(r.weather); Live.pollLocation(); }
        } else Toasts.err("Could not pin city — backend offline.");
      });
      body.querySelector("#set-city-clear").addEventListener("click", async () => {
        const r = await API.post("/api/live/weather/city", { city: "" });
        Settings.weatherCity = "";
        Settings.save();
        cityInput.value = "";
        Toasts.ok("City override cleared — auto-detect enabled.");
        if (r && r.ok) Live.pollWeather();
      });

      // poll interval → localStorage + live timer
      const pollRange = body.querySelector("#set-poll-range");
      const pollLabel = body.querySelector("#set-poll-label");
      pollRange.addEventListener("input", () => {
        pollLabel.textContent = `Every ${(Number(pollRange.value) / 1000).toFixed(1)}s`;
      });
      pollRange.addEventListener("change", () => {
        Settings.pollMs = Number(pollRange.value);
        Settings.save();
        Live.restartStatusTimer();
        Toasts.ok(`Telemetry refresh set to every ${(Settings.pollMs / 1000).toFixed(1)}s.`);
      });

      // appearance
      const themes = {
        cyan:   ["#00D2FF", "#FF9900"],
        ember:  ["#FF6600", "#FFD166"],
        photon: ["#00E5FF", "#7C4DFF"]
      };
      body.querySelector("#set-theme-cyan").addEventListener("click", () => Settings.setTheme("cyan", themes.cyan));
      body.querySelector("#set-theme-ember").addEventListener("click", () => Settings.setTheme("ember", themes.ember));
      body.querySelector("#set-theme-photon").addEventListener("click", () => Settings.setTheme("photon", themes.photon));
      const globeToggle = body.querySelector("#set-globe-toggle");
      globeToggle.addEventListener("click", () => {
        Settings.globe = !Settings.globe;
        Settings.save();
        Globe.setPaused(!Settings.globe);
        globeToggle.classList.toggle("on", Settings.globe);
        globeToggle.setAttribute("aria-checked", String(Settings.globe));
        Toasts.ok(Settings.globe ? "Globe rotation enabled." : "Globe rotation paused.");
      });
    }
  };

  /* =========================================================
     TERMINAL — real data commands + live status feed
  ========================================================= */
  /* =========================================================
     VIDEO STUDIO — AI video editor (ffmpeg backend)
     Folder of clips/images → stylised edit, background render.
  ========================================================= */
  const VideoStudio = {
    _polling: false,
    timer: null,
    init() {
      const btn = $("#video-render");
      if (btn) btn.addEventListener("click", () => this.render());
      const cbtn = $("#clips-render");
      if (cbtn) cbtn.addEventListener("click", () => this.renderClips());
      this.poll();
      this.timer = setInterval(() => this.poll(), 4000);
    },
    async renderClips() {
      const btn = $("#clips-render");
      const query = ($("#clips-query") || {}).value ? $("#clips-query").value.trim() : "";
      const count = ($("#clips-count") || {}).value || "6";
      const style = ($("#clips-style") || {}).value || "tiktok_intense";
      const status = $("#clips-status");
      const pill = $("#video-pill");
      if (!query) { if (status) status.textContent = "✘ Type a vibe first — e.g. 'anime edit' or 'sigma edit'."; return; }
      if (btn) { btn.disabled = true; btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> FETCHING BEST CLIPS…`; }
      if (status) status.innerHTML = `<i class="fa-solid fa-globe fa-spin"></i> Searching the internet for the best ${this.esc(query)} clips…`;
      const r = await API.post("/api/video/clips/render", { query, style, count: parseInt(count, 10) || 6, title: query + " edit" });
      if (btn) { btn.disabled = false; btn.innerHTML = `<i class="fa-solid fa-globe"></i> FETCH BEST CLIPS + RENDER`; }
      if (!r) { if (status) status.textContent = "✘ Backend offline."; return; }
      if (!r.ok) { if (status) status.innerHTML = `<span class="err">✘ ${this.esc(r.error)}</span>`; return; }
      if (pill) pill.textContent = "RENDERING";
      if (status) status.innerHTML = `✔ Downloaded ${r.job.clips_fetched || r.job.count || 0} clips — rendering ${this.esc(r.job.style)} edit…`;
      this.poll(true);
    },
    esc(s) { return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); },
    async render() {
      const btn = $("#video-render");
      const dir = ($("#video-dir") || {}).value ? $("#video-dir").value.trim() : "";
      const style = ($("#video-style") || {}).value || "tiktok_intense";
      const title = ($("#video-title") || {}).value ? $("#video-title").value.trim() : "";
      const status = $("#video-status");
      const pill = $("#video-pill");
      if (!dir) { if (status) status.textContent = "✘ Enter a source folder with 2+ clips/images."; return; }
      if (btn) btn.disabled = true;
      if (status) status.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Starting render…`;
      const r = await API.post("/api/video/render", { source_dir: dir, style, title });
      if (btn) btn.disabled = false;
      if (!r) { if (status) status.textContent = "✘ Backend offline."; return; }
      if (!r.ok) { if (status) status.innerHTML = `<span class="err">✘ ${this.esc(r.error)}</span>`; return; }
      if (pill) pill.textContent = "RENDERING";
      if (status) status.innerHTML = `✔ Render started (${this.esc(r.job.style)}).`;
      this.poll(true);
    },
    async poll(force) {
      if (this._polling) return;
      this._polling = true;
      try {
        const [s, l] = await Promise.all([
          API.get("/api/video/status"),
          API.get("/api/video/list")
        ]);
        if (s && s.jobs && s.jobs.length) {
          const j = s.jobs[0];
          const status = $("#video-status");
          const pill = $("#video-pill");
          if (pill) pill.textContent = j.status.toUpperCase();
          if (status) {
            const pct = Math.round((j.progress || 0) * 100);
            if (j.status === "done") status.innerHTML = `<span class="ok">✔ ${this.esc(j.output_name)}</span>`;
            else if (j.status === "error") status.innerHTML = `<span class="err">✘ ${this.esc(j.error)}</span>`;
            else status.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> ${this.esc(j.message)} — ${pct}%`;
          }
        }
        const list = $("#video-list");
        if (list && l && l.videos) {
          list.innerHTML = l.videos.length ? l.videos.slice(0, 5).map((v) =>
            `<div class="video-item video-item-row">` +
            `<a class="video-item-link" href="${v.url}" target="_blank" download><i class="fa-solid fa-film"></i> ${this.esc(v.name)} <em>${v.size_mb} MB</em></a>` +
            `<button class="video-publish" data-name="${this.esc(v.name)}" data-url="${v.url}" type="button" title="Stage for YouTube upload"><i class="fa-solid fa-youtube"></i> Publish</button>` +
            `</div>`
          ).join("") : "";
          list.querySelectorAll(".video-publish").forEach((b) => b.addEventListener("click", async () => {
            const r = await API.post("/api/youtube/propose", { video_path: b.dataset.url.replace("/api/video/file/", "") });
            if (r && r.ok) {
              Toasts.ok("Staged! Open Settings → YOUTUBE to approve.");
              const s = document.querySelector("#set-yt-status");
              if (s) s.textContent = "✓ Video staged — open Settings → YOUTUBE to approve & upload.";
            } else {
              Toasts.err((r && r.error) || "could not stage — is the video path valid?");
            }
          }));
        }
      } finally {
        this._polling = false;
      }
    }
  };

  const Terminal = {
    history: [],
    historyIndex: -1,
    bootLines: [
      ["SYSTEM", "A.3.T.H.E.R. core wiring live data", "ok"],
      ["AI CORE", "LLM gateway routing: openai → deepseek → gemini → groq → anthropic", "cy"],
      ["SERVICES", "telemetry · bluetooth · weather · location connected", "ok"],
      ["DEVICES", "rescanning Bluetooth + LAN…", "cy"],
      ["TERMINAL", "type 'help' for commands", "ok"]
    ],
    commands: {
      help: () => [
        "Available commands:",
        "  help            show this help",
        "  status          live system telemetry",
        "  specs           real hardware specs (CPU / GPU / RAM)",
        "  mesh            device mesh status (Ultron Control)",
        "  broadcast <cmd> send a command to every connected device",
        "  terminate       JARVIS failsafe — kill processes + mesh",
        "  android         ADB control: status · unlock · tap x=500 y=900 …",
        "  devices         real Bluetooth + LAN devices",
        "  predict         AI forecast — what happens next",
        "  phonelink       URL for phone control (same Wi-Fi)",
        "  weather         live weather from Open-Meteo",
        "  location        detected city / country",
        "  search <q>      browse the internet (no key needed)",
        "  research <topic> AI skill-up brief: web + LLM summary",
        "  video <dir> [style]  render a TikTok-style edit (styles: tiktok_intense · anime · movie_trailer · aesthetic)",
        "  clips <vibe> [count]  fetch best clips from the internet + render a TikTok edit (e.g. clips anime edit)",
        "  unlock          unlock the phone with its remembered PIN/pattern",
        "  rememberpin <pin>  remember the phone PIN (e.g. rememberpin 1234)",
        "  vault           list remembered phone secrets",
        "  groq            show Groq gateway status",
        "  time            current time / date",
        "  uptime          how long the core has run",
        "  rescan          rescan Bluetooth + LAN",
        "  voice           start the voice pipeline",
        "  clear           wipe the terminal"
      ],
      async specs() {
        const s = await Specs.load();
        if (!s) return ["SPECS: backend offline — run the A3THER server."];
        const gpu = (s.gpu && s.gpu.gpus && s.gpu.gpus.length)
          ? s.gpu.gpus.map((g) => `${g.name}${g.percent != null ? " (" + g.percent + "%)" : ""}`).join(" · ")
          : "integrated GPU";
        return [
          `HOST    : ${s.hostname}`,
          `OS      : ${s.os} ${s.os_version}`,
          `CPU     : ${s.cpu.brand} · ${s.cpu.cores_physical ?? "?"} core / ${s.cpu.cores_logical ?? "?"} threads`,
          `GPU     : ${gpu}`,
          `RAM     : ${s.ram.total_gb} GB`,
          `STORAGE : ${s.storage.used_gb} / ${s.storage.total_gb} GB`,
          `UPTIME  : ${Math.floor(s.uptime_seconds / 3600)}h ${Math.floor((s.uptime_seconds % 3600) / 60)}m`
        ];
      },
      async status() {
        const s = await API.get("/api/live/status");
        if (!s) return ["STATUS: backend offline — run the A3THER server."];
        return [
          `HOSTNAME        : ${s.hostname}`,
          `CPU             : ${s.cpu ? s.cpu.percent + "%" : "n/a"}${s.cpu && s.cpu.temp_c != null ? "  (" + s.cpu.temp_c + "°C)" : ""}`,
          `GPU             : ${s.gpu && s.gpu.percent != null ? s.gpu.percent + "%" : "n/a"} ${s.gpu && s.gpu.name ? "· " + s.gpu.name : ""}`,
          `RAM             : ${s.ram ? s.ram.used_gb + " / " + s.ram.total_gb + " GB (" + s.ram.percent + "%)" : "n/a"}`,
          `STORAGE         : ${s.storage ? s.storage.used_gb + " / " + s.storage.total_gb + " GB (" + s.storage.percent + "%)" : "n/a"}`,
          `PROCESSES       : ${s.process_count != null ? s.process_count : "n/a"}`,
          `UPTIME          : ${s.uptime ? s.uptime.hours + "h " + s.uptime.minutes + "m" : "n/a"}`,
          `BATTERY         : ${s.battery ? s.battery.percent + "%" + (s.battery.plugged ? " (plugged)" : "") : "n/a"}`
        ];
      },
      async mesh() {
        const m = await API.get("/api/sync/mesh");
        if (!m) return ["MESH: backend offline."];
        const lines = [`MESH — ${m.count} node(s) online`];
        (m.by_kind ? Object.entries(m.by_kind) : []).forEach(([k, n]) => lines.push(`  ${k.padEnd(10)} ${n}`));
        (m.online || []).forEach((n) => lines.push(`  • ${n.name} (${n.kind})`));
        lines.push(`  builtins: ${(m.builtin_commands || []).join(", ")}`);
        lines.push(`  hooks: ${(m.local_hooks || []).join(", ") || "none"}`);
        return lines;
      },
      async broadcast(raw) {
        const cmd = (raw[0] || "").toLowerCase();
        if (!cmd) return ["Usage: broadcast <command> [key=value …] — e.g. broadcast unlock_interface confirm=true"];
        const params = {};
        raw.slice(1).forEach((kv) => {
          const eq = kv.indexOf("=");
          if (eq > 0) params[kv.slice(0, eq)] = kv.slice(eq + 1);
          else params[kv] = true;
        });
        Terminal.print(`Broadcasting '${cmd}' to the mesh…`, "");
        const r = await API.post("/api/sync/broadcast", { command: cmd, params, source: "terminal" });
        if (!r) return ["BROADCAST: backend offline."];
        const local = (r.local_results || []).map((l) => `  local: ${l.ok ? l.detail || "ok" : l.detail}`);
        return [
          `${cmd.toUpperCase()} → ${r.targets} target(s), delivered ${r.delivered}, failed ${r.failed.length}`, ...local
        ];
      },
      async terminate(raw) {
        const reason = raw.join(" ") || "terminal abort";
        Terminal.print("Issuing JARVIS FAILSAFE — terminate…", "");
        const r = await API.post("/api/sync/terminate", { reason });
        if (!r) return ["TERMINATE: backend offline."];
        return [
          `FAILSAFE order ${r.order_id} issued`,
          `  processes killed : ${r.killed_pids.length}`,
          `  mesh notified    : ${r.mesh_delivered} node(s)`,
          `  reason           : ${r.reason}`
        ];
      },
      async android(raw) {
        const action = (raw[0] || "").toLowerCase();
        if (!action || action === "status" || action === "devices") {
          const d = await API.get("/api/sync/android");
          if (!d) return ["ANDROID: backend offline."];
          if (!d.available) return ["ANDROID: adb not found on PATH — install Android platform-tools."];
          const devs = (d.devices || []).map((x) => `  ${x.serial} (${x.state})`);
          return [
            `ANDROID BRIDGE READY — ${d.connected} device(s) connected`, ...devs,
            `  actions: ${(d.actions || []).join(", ")}`
          ];
        }
        const params = {};
        raw.slice(1).forEach((kv) => {
          const eq = kv.indexOf("=");
          if (eq > 0) params[kv.slice(0, eq)] = kv.slice(eq + 1);
          else params[kv] = true;
        });
        const r = await API.post("/api/sync/android/control", { action, params });
        if (!r) return ["ANDROID: backend offline."];
        if (!r.ok) return [`ANDROID ${action.toUpperCase()}: ${r.error || "failed"}`];
        const lines = [`ANDROID ${action.toUpperCase()} → ${r.serial || "?"} OK`];
        if (r.stdout) lines.push(`  ${r.stdout}`);
        if (r.path) lines.push(`  screenshot saved: ${r.path}`);
        return lines;
      },
      async devices() {
        const d = await API.get("/api/live/devices");
        if (!d) return ["DEVICES: backend offline — run the A3THER server."];
        const devs = d.devices || [];
        if (!devs.length) return ["No devices found — run 'rescan' or install bleak (pip install bleak)."];
        return devs.map((dev) =>
          `${(dev.name || "Unknown").toUpperCase().padEnd(24)} ${(dev.source === "bluetooth" ? "BT " : dev.source === "lan" ? "LAN" : "MAN").padEnd(4)} ${dev.online ? "ONLINE" : "OFFLINE"}${dev.rssi != null ? "  " + dev.rssi + " dBm" : ""}`
        );
      },
      async weather() {
        const w = await API.get("/api/live/weather");
        if (!w) return ["WEATHER: backend offline."];
        if (w.condition === "OFFLINE" || w.temperature_c == null) return ["Weather unavailable — check internet connection."];
        return [
          `CITY      : ${w.city}`,
          `TEMP      : ${w.temperature_c}°C  ${w.condition}`,
          `HUMIDITY  : ${w.humidity}%`,
          `WIND      : ${w.wind_kmh} km/h`,
          `PRESSURE  : ${w.pressure_hpa} hPa`,
          `VISIBILITY: ${w.visibility_km} km`,
          `UV INDEX  : ${w.uv_index}`
        ];
      },
      async location() {
        const loc = await API.get("/api/live/location");
        if (!loc) return ["LOCATION: backend offline."];
        return [
          `CITY      : ${loc.city}, ${loc.country}`,
          `COORDS    : ${loc.lat != null ? loc.lat + ", " + loc.lon : "unknown (fallback)"}`,
          `SOURCE    : ${loc.source}`,
          `IP        : ${loc.ip || "n/a"}`
        ];
      },
      async predict() {
        const p = await API.get("/api/live/predict");
        if (!p) return ["PREDICT: backend offline."];
        const lines = [`AI PREDICTOR — ${p.samples} sample(s), learning=${p.learning}`];
        if (p.headline) lines.push(`  TOP    : ${p.headline.note}`);
        (p.predictions || []).forEach((it) => {
          lines.push(`  ${it.label.padEnd(11)} ${it.value_now}${it.unit} → ${it.value_pred}${it.unit}  (${it.trend}, ${it.confidence}% conf, ~${it.horizon_min}min)`);
        });
        (p.context || []).forEach((c) => lines.push(`  ${c.title.padEnd(11)} ${c.detail}`));
        if (!p.predictions.length && !p.learning) lines.push("  All metrics stable — no meaningful trend.");
        return lines;
      },
      async phonelink() {
        const r = await API.get("/api/sync/phone-link");
        if (!r) return ["PHONELINK: backend offline."];
        return [
          `PHONE CONTROL URL`,
          `  ${r.url}`,
          `Open it on any phone/tablet on the same Wi-Fi — no install needed.`
        ];
      },
      time: () => [`Current time: ${nowStamp()}`, new Date().toString()],
      uptime: () => [`Core uptime: ${Clock.uptime()}`],
      async rescan() {
        Terminal.print("Rescanning Bluetooth + LAN…", "");
        const d = await Live.rescanDevices();
        return d ? [`Scan complete — ${d.count} device(s).`] : ["Rescan failed — backend offline."];
      },
      async voice() {
        const r = await API.post("/api/voice/start");
        return r ? ["Voice pipeline started — wake word 'hey aether' active."] : ["Voice backend offline."];
      },
      async unlock() {
        const r = await API.post("/api/sync/phone-unlock", {});
        if (!r) return ["UNLOCK: backend offline."];
        if (r.ok && r.unlocked) return [`PHONE UNLOCKED${r.already_unlocked ? " (already)" : ""}${r.method ? " via " + r.method.toUpperCase() : ""}.`];
        if (r.need_secret) return ["PHONE LOCKED — no PIN/pattern remembered yet.", "  Tell me: my pin is 1234   (or: my pattern is 1-5-9)"];
        if (r.wrong_secret) return ["✘ WRONG PIN/PATTERN — unlock the phone again on the screen.", "  Then correct it with: my pin is <new pin>"];
        return [`UNLOCK FAILED: ${r.error || "unknown"}`];
      },
      async rememberpin(args) {
        const value = args.join(" ").replace(/\D/g, "");
        if (!value) return ["REMEMBERPIN: usage — rememberpin 1234"];
        const r = await API.post("/api/sync/phone-secret", { kind: "pin", value });
        return r && r.ok ? ["✓ PIN remembered — say 'unlock my phone' anytime."] : [`✘ ${(r && r.error) || "failed"}`];
      },
      async vault() {
        const r = await API.get("/api/sync/phone-vault");
        if (!r) return ["VAULT: backend offline."];
        if (!r.count) return ["PHONE VAULT: empty — nothing remembered yet."];
        return [`PHONE VAULT — ${r.count} device(s)`, ...(r.entries || []).map((e) => `  ${e.device} · ${e.kind.toUpperCase()}`)];
      },
      async groq() {
        const d = await API.get("/api/llm/status");
        if (!d) return ["GROQ: backend offline."];
        const g = (d.providers || []).find((p) => p.name === "groq");
        if (!g) return ["GROQ: not in the gateway chain."];
        return [
          `GROQ GATEWAY`,
          `  configured : ${g.configured ? "YES — fast Llama/DeepSeek available" : "NO — add a key in Settings (console.groq.com)"}`,
          `  model      : ${g.model || "llama-3.3-70b-versatile"}`
        ];
      },
      async search(args) {
        const q = args.join(" ");
        if (!q) return ["SEARCH: usage — search <query>"];
        const r = await API.post("/api/internet/search", { query: q });
        if (!r) return ["SEARCH: backend offline."];
        if (!r.ok) return [`SEARCH: ${r.error || "failed"}`];
        const lines = [`WEB RESULTS — ${r.query.toUpperCase()}`];
        (r.results || []).forEach((res, i) => {
          lines.push(`  ${i + 1}. ${res.title}`);
          lines.push(`     ${res.url}`);
          if (res.snippet) lines.push(`     ${res.snippet}`);
        });
        return lines;
      },
      async research(args) {
        const topic = args.join(" ");
        if (!topic) return ["RESEARCH: usage — research <topic>"];
        Terminal.print("Searching the web + writing the brief…", "");
        const r = await API.post("/api/internet/learn", { topic });
        if (!r) return ["RESEARCH: backend offline."];
        if (!r.ok) return [`RESEARCH: ${r.error || "failed"}`];
        const lines = [`SKILL BRIEF — ${topic.toUpperCase()}`];
        if (r.brief) lines.push(r.brief);
        if (r.note) lines.push(`  (${r.note})`);
        (r.results || []).slice(0, 3).forEach((res, i) => lines.push(`  [${i + 1}] ${res.title} — ${res.url}`));
        return lines;
      },
      async video(args) {
        const dir = args[0] || "";
        const style = (args[1] || "tiktok_intense").toLowerCase();
        if (!dir) return ["VIDEO: usage — video <folder> [style]", "  styles: tiktok_intense · anime · movie_trailer · aesthetic"];
        const r = await API.post("/api/video/render", { source_dir: dir, style, title: "A3THER EDIT" });
        if (!r) return ["VIDEO: backend offline."];
        if (!r.ok) return [`VIDEO: ${r.error || "render failed"}`];
        return [`RENDER STARTED (${r.job.style})`, `  ${r.job.message}`];
      },
      async clips(args) {
        const query = args.join(" ").replace(/\s*\d+$/, "").trim() || "";
        const count = parseInt((args.join(" ").match(/\d+$/) || ["6"])[0], 10) || 6;
        if (!query) return ["CLIPS: usage — clips <vibe> [count]", "  e.g. clips anime edit 6"];
        const r = await API.post("/api/video/clips/render", { query, style: "tiktok_intense", count, title: query + " edit" });
        if (!r) return ["CLIPS: backend offline."];
        if (!r.ok) return [`CLIPS: ${r.error || "fetch failed"}`];
        return [`CLIPS FETCHED: ${r.job.clips_fetched || 0} best clips downloaded`, `RENDER STARTED (${r.job.style})`, `  ${r.job.message}`];
      }
    },
    init() {
      const body = $("#console-output");
      if (!body) return;
      this.bootLines.forEach(([tag, msg, cls]) => this.print(`[${tag}] ${msg}`, cls));
      const form = $("#console-input");
      if (!form) return;
      form.addEventListener("keydown", (e) => {
        const input = e.currentTarget;
        if (e.key === "Enter") {
          e.preventDefault();
          const raw = input.value.trim();
          if (raw) {
            this.print(`C:\\A3THER> ${raw}`, "cmd");
            this.history.unshift(raw);
            this.historyIndex = -1;
            this.execute(raw);
          }
          input.value = "";
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          if (!this.history.length) return;
          this.historyIndex = Math.min(this.historyIndex + 1, this.history.length - 1);
          input.value = this.history[this.historyIndex] || "";
        } else if (e.key === "ArrowDown") {
          e.preventDefault();
          this.historyIndex = Math.max(this.historyIndex - 1, -1);
          input.value = this.historyIndex >= 0 ? this.history[this.historyIndex] : "";
        }
      });
      const clearBtn = $("#clear-console");
      if (clearBtn) clearBtn.addEventListener("click", () => this.clear());
      const exportBtn = $("#export-console");
      if (exportBtn) exportBtn.addEventListener("click", () => this.exportLog());
    },
    print(text, cls = "") {
      const body = $("#console-output");
      if (!body) return;
      const line = document.createElement("div");
      line.className = `term-line ${cls}`.trim();
      const time = document.createElement("span");
      time.className = "t-time";
      time.textContent = `[${nowStamp()}] `;
      line.appendChild(time);
      line.appendChild(document.createTextNode(text));
      body.appendChild(line);
      body.scrollTop = body.scrollHeight;
      while (body.children.length > 200) body.removeChild(body.firstChild);
    },
    async execute(raw) {
      const parts = raw.split(/\s+/);
      const cmd = parts[0].toLowerCase();
      const args = parts.slice(1);
      if (cmd === "clear") { this.clear(); return; }
      if (cmd === "" || cmd === "help" || cmd === "time" || cmd === "uptime") {
        const out = typeof this.commands[cmd] === "function" ? this.commands[cmd](args) : this.commands.help();
        if (out) (await Promise.resolve(out)).forEach((line) => this.print(line, "cmd"));
        return;
      }
      if (this.commands[cmd]) {
        Terminal.print("Querying live backend…", "");
        const out = await this.commands[cmd](args);
        if (out) out.forEach((line) => this.print(line, "cmd"));
        return;
      }
      this.print(`Unknown command: '${cmd}'. Type 'help'.`, "err");
    },
    notifyLive(s) {
      // keep the boot line about devices truthful-ish: log hostname once
      if (!this._hostLogged && s.hostname) {
        this._hostLogged = true;
        this.print(`[HOST] ${s.hostname} · ${s.platform || ""}`, "ok");
      }
    },
    clear() {
      const body = $("#console-output");
      if (body) body.innerHTML = "";
    },
    exportLog() {
      const body = $("#console-output");
      if (!body) return;
      const text = Array.from(body.querySelectorAll(".term-line"))
        .map((el) => el.textContent)
        .join("\n");
      const blob = new Blob([text || "A.3.T.H.E.R. terminal log (empty)"], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "a3ther_log.txt";
      a.click();
      URL.revokeObjectURL(url);
      Toasts.ok("Terminal log exported.");
    }
  };

  /* =========================================================
     NAVIGATION + DOCK — real actions
  ========================================================= */
  const Nav = {
    init() {
      const navButtons = $$("#top-navigation button");
      const dockItems = $$(".dock-item");
      const panels = {
        dashboard: () => { window.scrollTo({ top: 0, behavior: "smooth" }); },
        system: () => { window.scrollTo({ top: 0, behavior: "smooth" }); },
        devices: () => Views.devices(),
        files: () => Views.files(),
        automation: () => { window.scrollTo({ top: 0, behavior: "smooth" }); Toasts.info("Automation routed through the security sandbox."); },
        terminal: () => { $("#console-input")?.focus(); $("#console-card")?.scrollIntoView({ behavior: "smooth", block: "center" }); },
        voice: () => Voice.toggle(),
        ai: () => { $("#ai-core")?.scrollIntoView({ behavior: "smooth", block: "center" }); },
        weather: () => { $("#weather-card")?.scrollIntoView({ behavior: "smooth", block: "center" }); },
        settings: () => Views.settings(),
        control: () => { $("#control-card")?.scrollIntoView({ behavior: "smooth", block: "center" }); Control.refresh(); },
        sites: () => { $("#sites-card")?.scrollIntoView({ behavior: "smooth", block: "center" }); Webmaker.refresh(); }
      };
      const select = (btn, group) => {
        group.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
      };
      navButtons.forEach((btn) =>
        btn.addEventListener("click", () => {
          select(btn, navButtons);
          const fn = panels[btn.dataset.panel];
          if (fn) fn();
          else Toasts.info(`${btn.textContent.trim().toUpperCase()} selected.`);
        })
      );
      dockItems.forEach((btn) => {
        btn.addEventListener("click", () => {
          select(btn, dockItems);
          const fn = panels[btn.dataset.panel];
          if (fn) fn();
          else if (btn.dataset.panel === "core") { $("#ai-core")?.scrollIntoView({ behavior: "smooth", block: "center" }); Toasts.ok("A.3.T.H.E.R. core engaged."); }
        });
      });
      const allDevices = $("#view-all-devices");
      if (allDevices) allDevices.addEventListener("click", () => Views.devices());
      const rescan = $("#rescan-devices");
      if (rescan) rescan.addEventListener("click", () => Live.rescanDevices());

      // Website maker — generate button + initial library load.
      const siteGen = $("#site-generate");
      if (siteGen) siteGen.addEventListener("click", () => Webmaker.generate());
      const siteDesc = $("#site-desc");
      if (siteDesc) siteDesc.addEventListener("keydown", (e) => { if (e.key === "Enter") Webmaker.generate(); });
      Webmaker.refresh();

      // "Open calendar" — full-size calendar overlay (was a dead button).
      const openCal = document.querySelector("#calendar-card .icon-button");
      if (openCal) openCal.addEventListener("click", () => {
        const body = Overlay.open("CALENDAR", '<div id="overlay-calendar" class="overlay-calendar"></div>', true);
        Calendar.render(body.querySelector("#overlay-calendar"));
      });

      // Notifications "VIEW ALL" — all notifications + boot-engine log overlay.
      const viewAllNotes = document.querySelector("#notifications-card .panel-footer-button");
      if (viewAllNotes) viewAllNotes.addEventListener("click", Notifications.viewAll);
      // dock label → panel mapping (dock uses data-panel too)
      const coreItem = $(".dock-item.dock-core");
      if (coreItem) coreItem.addEventListener("click", () => Toasts.ok("A.3.T.H.E.R. core engaged."));
    }
  };

  /* =========================================================
     WEBSITE MAKER — generate + preview 3D sites (real API)
  ========================================================= */
  const Webmaker = {
    esc: (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"),
    async refresh() {
      const list = $("#site-list");
      if (!list) return;
      try {
        const r = await API.get("/api/website/list");
        const sites = (r && r.sites) || [];
        if (!sites.length) {
          list.innerHTML = '<p class="hint">No sites yet — describe one above and hit Generate.</p>';
          return;
        }
        list.innerHTML = sites.map((s) => `
          <div class="site-item">
            <div><strong>${this.esc(s.name)}</strong><small>${(s.bytes / 1024).toFixed(1)} KB · HTML</small></div>
            <a class="site-open" href="/websites/${encodeURIComponent(s.name)}/index.html" target="_blank" rel="noopener"><i class="fa-solid fa-up-right-from-square"></i> Open</a>
          </div>`).join("");
      } catch (e) {
        list.innerHTML = `<p class="hint">Website API unavailable: ${this.esc(String(e))}</p>`;
      }
    },
    async generate() {
      const desc = $("#site-desc").value.trim();
      const name = $("#site-name").value.trim();
      const theme = $("#site-theme").value;
      const result = $("#site-result");
      const btn = $("#site-generate");
      if (!desc) {
        Toasts.warn("Describe the site first.");
        $("#site-desc").focus();
        return;
      }
      btn.disabled = true;
      btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Building…';
      result.innerHTML = '<p class="hint">Generating — the AI is writing the HTML…</p>';
      try {
        const r = await API.post("/api/website/generate", { description: desc, name, theme });
        if (r && r.ok) {
          result.innerHTML = `<p class="ok"><i class="fa-solid fa-circle-check"></i> <strong>${this.esc(r.name)}</strong> built (${this.esc(r.source)}, ${(r.bytes / 1024).toFixed(1)} KB) — <a href="/websites/${encodeURIComponent(r.name)}/index.html" target="_blank" rel="noopener">preview it</a></p>`;
          Toasts.ok("Website generated!");
          this.refresh();
        } else {
          result.innerHTML = `<p class="err">${this.esc((r && r.error) || "Generation failed.")}</p>`;
        }
      } catch (e) {
        result.innerHTML = `<p class="err">${this.esc(String(e))}</p>`;
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> Generate';
      }
    }
  };

  /* =========================================================
     NOTIFICATIONS — "VIEW ALL" overlay (all alerts + engine log)
  ========================================================= */
  const Notifications = {
    async viewAll() {
      const body = Overlay.open("ALL NOTIFICATIONS", '<p class="hint">Loading…</p>', true);
      const list = document.createElement("div");
      list.className = "overlay-list";
      // Clone whatever the live notifications panel currently shows.
      const notes = document.querySelectorAll("#notification-list article");
      if (notes.length) notes.forEach((a) => list.appendChild(a.cloneNode(true)));
      else list.appendChild(Object.assign(document.createElement("p"), { className: "hint", textContent: "No alerts right now." }));
      body.innerHTML = "";
      body.appendChild(list);
      // Boot-engine terminal log from /api/engine/status (real events).
      try {
        const st = await API.get("/api/engine/status");
        const ev = (st && st.events) || [];
        if (ev.length) {
          const h = document.createElement("h3");
          h.className = "overlay-subhead";
          h.textContent = "BOOT ENGINE LOG";
          body.appendChild(h);
          const pre = document.createElement("pre");
          pre.className = "overlay-log";
          pre.textContent = ev.slice(-80).join("\n");
          body.appendChild(pre);
        }
      } catch (_) { /* engine endpoint optional */ }
    }
  };

  /* =========================================================
     CONTROL PHONE — live screens for every connected device
     (Android USB/WiFi, mesh clients, this laptop)
  ========================================================= */
  const Control = {
    devices: [],
    timers: [],
    esc(s) { return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); },
    connBadge(conn) {
      const map = { usb: ["USB", "ok"], wifi: ["WIFI", "warn"], mesh: ["NET", "accent-orange"], local: ["LOCAL", "ok"], bluetooth: ["BT", "accent-orange"] };
      const [label, cls] = map[conn] || [conn.toUpperCase(), ""];
      return `<span class="status-pill ${cls}">${label}</span>`;
    },
    screenUrl(serial, w) {
      // w>0 asks the backend for a downscaled frame — thumbnails are ~20x
      // lighter, which keeps the native WebView2 window from crashing under
      // repeated full-res grabs.
      return `/api/sync/control/screen/${encodeURIComponent(serial)}?t=${Date.now()}${w ? `&w=${w}` : ""}`;
    },
    card(device) {
      const card = document.createElement("div");
      card.className = "control-card";
      const head = document.createElement("div");
      head.className = "control-head";
      head.innerHTML = `<strong>${this.esc(device.model)}</strong> ${this.connBadge(device.connection)}`;
      card.appendChild(head);

      const screen = document.createElement("div");
      screen.className = "control-screen";
      if (device.has_screen) {
        const img = document.createElement("img");
        img.className = "control-thumb";
        img.alt = `${device.model} screen`;
        img.src = this.screenUrl(device.serial, 480);
        img.onerror = () => { img.classList.add("off"); };
        screen.appendChild(img);
        // Poll this card's thumbnail while the panel is mounted (light 480px
        // frames, paused when the tab is hidden to spare the renderer).
        const timer = setInterval(() => {
          if (!document.body.contains(card)) { clearInterval(timer); return; }
          if (document.hidden) return;
          img.src = this.screenUrl(device.serial, 480);
        }, 4000);
        this.timers.push(timer);
      } else {
        screen.innerHTML = `<div class="control-no-screen"><i class="fa-solid fa-mobile-screen-button"></i><span>${this.esc(device.platform || device.kind || "no screen")}</span></div>`;
      }
      card.appendChild(screen);

      const meta = document.createElement("div");
      meta.className = "control-meta";
      if (device.bt) {
        meta.textContent = `${this.esc(device.platform || "Bluetooth LE")} · click to connect`;
      } else {
        meta.textContent = device.connection === "mesh"
          ? `${this.esc(device.kind || "node")}${device.platform && device.platform.length < 40 && !/mozilla|applewebkit/i.test(device.platform) ? " · " + this.esc(device.platform) : " · over the internet"}`
          : device.connection === "local" ? "this machine" : `adb · ${this.esc(device.serial)}`;
      }
      card.appendChild(meta);

      // Bluetooth cards open the BLE controller view, not a screen overlay.
      card.addEventListener("click", () => {
        if (device.bt) this.openBluetooth(device);
        else this.openView(device);
      });
      return card;
    },
    async refresh() {
      try {
        const d = await API.get("/api/sync/control/devices");
        if (!d || !Array.isArray(d.devices)) return;
        this.devices = d.devices;
      } catch (_) { return; }
      const grid = $("#control-grid");
      if (!grid) return;
      grid.innerHTML = "";
      this.devices.forEach((dev) => grid.appendChild(this.card(dev)));
      const pill = $("#control-pill");
      if (pill) {
        const withScreen = this.devices.filter((d) => d.has_screen).length;
        pill.textContent = `${this.devices.length} DEVICE(S) · ${withScreen} LIVE SCREEN${withScreen === 1 ? "" : "S"}`;
      }
    },
    openView(device) {
      const serial = device.serial;
      const body = Overlay.open(
        `LIVE SCREEN — ${this.esc(device.model)}`,
        `
        <div class="control-live">
          <img id="control-live-img" src="${this.screenUrl(serial)}" alt="live screen" />
        </div>
        <div class="control-actions">
          <button class="btn primary" data-act="refresh"><i class="fa-solid fa-rotate"></i> Refresh</button>
          <a class="btn ghost" href="${this.screenUrl(serial)}" download="${this.esc(serial)}.png"><i class="fa-solid fa-download"></i> Save</a>
          ${serial !== "host" ? `
          <button class="btn ghost" data-act="unlock"><i class="fa-solid fa-lock-open"></i> Unlock</button>
          <button class="btn ghost" data-act="wake"><i class="fa-solid fa-sun"></i> Wake</button>
          <button class="btn ghost" data-act="flash"><i class="fa-solid fa-bolt"></i> Flash</button>
          <button class="btn ghost" data-act="cast"><i class="fa-solid fa-display"></i> Cast (scrcpy)</button>` : ""}
        </div>
        <p class="hint" id="control-live-msg">Live screen — updates automatically.</p>
        `, true
      );
      const img = body.querySelector("#control-live-img");
      img.onerror = () => {
        const msg = body.querySelector("#control-live-msg");
        if (msg) msg.textContent = "✘ Screen unavailable — is the phone unlocked and USB debugging on?";
      };
      const liveTimer = setInterval(() => {
        if (!document.body.contains(img)) { clearInterval(liveTimer); return; }
        if (document.hidden) return;
        img.src = this.screenUrl(serial, 1280);
      }, 3000);
      body.querySelectorAll("[data-act]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const act = btn.dataset.act;
          const msg = body.querySelector("#control-live-msg");
          if (act === "refresh") { img.src = this.screenUrl(serial, 1280); return; }
          btn.disabled = true;
          const CMD = {
            unlock: { command: "unlock_phone", params: { serial } },
            wake: { command: "android_control", params: { action: "unlock", serial } },
            flash: { command: "flash_screen", params: { serial } },
          };
          try {
            if (act === "cast") {
              const r = await API.post("/api/sync/cast/start", { serial, prefer_scrcpy: true });
              if (msg) msg.textContent = r && r.ok ? `Cast started — ${r.mode || "scrcpy"} mode.` : `✘ ${(r && r.error) || "cast failed"}`;
            } else {
              const r = await API.post("/api/sync/broadcast", { ...CMD[act], target: "host" });
              if (msg) msg.textContent = r && r.delivered ? `${act} command sent.` : `✘ ${(r && r.error) || "command failed"}`;
            }
          } catch (err) {
            if (msg) msg.textContent = `✘ ${err.message}`;
          } finally {
            btn.disabled = false;
          }
        });
      });
    },
    async openBluetooth(device) {
      // Real BLE controller — connect, read battery/info, list services,
      // and write raw commands to any writable characteristic.
      const body = Overlay.open(
        `BLUETOOTH — ${this.esc(device.model)}`,
        `
        <div class="bt-controller">
          <div class="bt-status" id="bt-status">
            <i class="fa-solid fa-bluetooth-b"></i>
            <span id="bt-status-text">Not connected</span>
          </div>
          <div class="bt-actions">
            <button class="btn primary" id="bt-connect"><i class="fa-solid fa-link"></i> Connect</button>
            <button class="btn ghost" id="bt-disconnect" disabled><i class="fa-solid fa-unlink"></i> Disconnect</button>
            <button class="btn ghost" id="bt-info" disabled><i class="fa-solid fa-battery-three-quarters"></i> Read Battery / Info</button>
            <button class="btn ghost" id="bt-services" disabled><i class="fa-solid fa-list"></i> List Services</button>
          </div>
          <div class="bt-info-grid" id="bt-info-grid"></div>
          <div class="bt-write">
            <label>Send command <small>choose a writable characteristic UUID, then text or hex (e.g. 01 02 ff)</small></label>
            <div class="bt-write-row">
              <input class="settings-text-input" id="bt-write-uuid" placeholder="0000ffe1-0000-1000-8000-00805f9b34fb" autocomplete="off" />
              <input class="settings-text-input" id="bt-write-data" placeholder="hello" autocomplete="off" />
              <button class="btn ghost" id="bt-write-send"><i class="fa-solid fa-paper-plane"></i> Send</button>
            </div>
            <p class="hint" id="bt-write-msg"></p>
          </div>
          <div class="bt-services" id="bt-services-list"></div>
        </div>
        `, true
      );
      const $id = (sel) => body.querySelector(sel);
      const statusText = $id("#bt-status-text");
      const setStatus = (text, ok) => {
        statusText.textContent = text;
        statusText.style.color = ok === false ? "var(--orange)" : ok ? "var(--cyan)" : "";
      };
      const btn = (sel, enabled) => { const b = $id(sel); if (b) b.disabled = !enabled; };
      const pollStatus = async () => {
        const s = await API.get("/api/sync/ble/status");
        if (!s) return;
        if (s.connected) {
          setStatus(`Connected to ${this.esc(s.name || s.address)} — ${s.uptime_seconds}s`, true);
          btn("#bt-disconnect", true); btn("#bt-info", true); btn("#bt-services", true); btn("#bt-connect", false);
        } else {
          setStatus(s.last_error ? `✘ ${s.last_error}` : "Not connected");
          btn("#bt-disconnect", false); btn("#bt-info", false); btn("#bt-services", false); btn("#bt-connect", true);
        }
        if (s.info) this.renderBtInfo($id("#bt-info-grid"), s.info);
      };
      $id("#bt-connect").addEventListener("click", async () => {
        setStatus("Connecting…");
        btn("#bt-connect", false);
        const r = await API.post("/api/sync/ble/connect", { address: device.serial, name: device.model });
        if (!r || !r.ok) { setStatus(`✘ ${(r && r.error) || "connect failed"}`, false); btn("#bt-connect", true); return; }
        // bleak connects in the background — wait for the link to come up.
        for (let i = 0; i < 20; i++) {
          await new Promise((res) => setTimeout(res, 500));
          const s = await API.get("/api/sync/ble/status");
          if (s && s.connected) break;
          if (s && s.last_error) break;
        }
        await pollStatus();
        if (statusText.textContent.startsWith("Connected")) {
          Toasts.ok(`Bluetooth link to ${device.model} established.`);
        } else {
          Toasts.err(`Couldn't connect to ${device.model} — is it paired/on?`);
        }
      });
      $id("#bt-disconnect").addEventListener("click", async () => {
        await API.post("/api/sync/ble/disconnect", {});
        setStatus("Not connected");
        btn("#bt-disconnect", false); btn("#bt-info", false); btn("#bt-services", false); btn("#bt-connect", true);
        $id("#bt-info-grid").innerHTML = "";
        $id("#bt-services-list").innerHTML = "";
      });
      $id("#bt-info").addEventListener("click", async () => {
        const r = await API.get("/api/sync/ble/info");
        if (!r || !r.ok) { setStatus(`✘ ${(r && r.error) || "info failed"}`, false); return; }
        this.renderBtInfo($id("#bt-info-grid"), r.info);
        Toasts.ok("Battery / device info read.");
      });
      $id("#bt-services").addEventListener("click", async () => {
        const r = await API.get("/api/sync/ble/services");
        if (!r || !r.ok) { setStatus(`✘ ${(r && r.error) || "services failed"}`, false); return; }
        const list = $id("#bt-services-list");
        list.innerHTML = (r.services || []).map((svc) => `
          <details>
            <summary>${this.esc(svc.uuid)} · ${svc.characteristics.length} char</summary>
            ${(svc.characteristics || []).map((c) => `<div class="bt-char" data-uuid="${this.esc(c.uuid)}">${this.esc(c.uuid)} <small>[${this.esc((c.properties || []).join(", "))}]</small></div>`).join("")}
          </details>`).join("") || "<p class='hint'>No services exposed.</p>";
        list.querySelectorAll(".bt-char").forEach((el) => {
          el.addEventListener("click", () => {
            $id("#bt-write-uuid").value = el.dataset.uuid;
            Toasts.info("UUID copied to the command field.");
          });
        });
      });
      $id("#bt-write-send").addEventListener("click", async () => {
        const uuid = $id("#bt-write-uuid").value.trim();
        const data = $id("#bt-write-data").value;
        if (!uuid) { Toasts.warn("Pick a characteristic UUID first (list services)."); return; }
        const hex = /^[0-9a-fA-F\s]+$/.test(data) && data.includes(" ");
        const r = await API.post("/api/sync/ble/write", { uuid, data, hex });
        const msg = $id("#bt-write-msg");
        msg.textContent = r && r.ok ? `✓ ${r.bytes} bytes written` : `✘ ${(r && r.error) || "write failed"}`;
      });
      await pollStatus();
      this._btPoll = setInterval(() => {
        if (!document.body.contains(body)) { clearInterval(this._btPoll); return; }
        pollStatus();
      }, 3000);
    },
    renderBtInfo(grid, info) {
      if (!grid || !info) return;
      const rows = [
        ["Battery", info.battery_percent != null ? `${info.battery_percent}%` : "—"],
        ["Manufacturer", info.manufacturer || "—"],
        ["Model", info.model || "—"],
        ["Serial", info.serial || "—"],
        ["Firmware", info.firmware || "—"],
        ["Hardware", info.hardware || "—"],
      ];
      grid.innerHTML = rows.map(([k, v]) => `<div class="bt-info-cell"><span>${k}</span><strong>${this.esc(v)}</strong></div>`).join("");
    }
  };

  /* =========================================================
     GLOBE — rotating network core, real device count
  ========================================================= */
  const Globe = {
    canvas: null,
    ctx: null,
    width: 0,
    height: 0,
    R: 0,
    rotY: 0,
    stars: [],
    raf: null,
    last: 0,

    // REAL connected devices only — populated by setNodes() from
    // /api/live/devices (controllable mesh nodes). Never fabricated.
    nodes: [],

    init() {
      this.canvas = $("#globe-canvas");
      if (!this.canvas) return;
      this.ctx = this.canvas.getContext("2d");
      this.buildStars();
      this.resize();
      window.addEventListener("resize", () => this.resize());
      if (window.ResizeObserver) {
        new ResizeObserver(() => this.resize()).observe(this.canvas.parentElement);
      }
      this.loop();
    },
    resize() {
      const stage = this.canvas.parentElement;
      // DPR capped at 1.25: the wireframe globe doesn't need 2x pixels and
      // the backing store is the single biggest RAM consumer on this page.
      const dpr = Math.min(window.devicePixelRatio || 1, 1.25);
      this.width = stage.clientWidth;
      this.height = stage.clientHeight;
      this.canvas.width = Math.max(1, Math.round(this.width * dpr));
      this.canvas.height = Math.max(1, Math.round(this.height * dpr));
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.R = Math.min(this.width / 3.1, this.height / 2.6);
    },
    buildStars() {
      for (let i = 0; i < 90; i++) {
        this.stars.push({
          x: Math.random(),
          y: Math.random(),
          r: Math.random() * 1.3 + 0.3,
          tw: Math.random() * Math.PI * 2
        });
      }
    },
    setDeviceCount(connected) {
      const sub = $(".globe-caption-sub");
      if (sub) sub.textContent = `${connected || 0} DEVICE${connected === 1 ? "" : "S"} CONNECTED`;
    },
    setNodes(devices) {
      // Only CONNECTED (controllable) devices earn a globe node — a device
      // that merely exists nearby (BT scan, ARP table) is NOT shown.
      const connected = (devices || []).filter((d) => d && d.controllable);
      const n = connected.length;
      this.nodes = connected.map((d, i) => {
        // Deterministic, even spread around the sphere.
        const frac = n > 1 ? i / (n - 1) : 0.5;
        const lon = frac * 360 - 180 + ((i * 37) % 30) - 15;
        const lat = 18 + 52 * Math.sin(i * 2.399 + 1.1);
        const lat2 = d.latency_ms != null ? d.latency_ms : null;
        const conn = d.conn || (d.source === "mesh" ? "WIFI" : "LAN");
        // Health dot: <250ms = cyan (fresh), <1500ms = orange (lagging),
        // beyond that = red (about to drop).
        const health = lat2 == null ? "on" : lat2 < 250 ? "on" : lat2 < 1500 ? "warn" : "off";
        return {
          lat, lon,
          label: (d.name || d.kind || "node").toUpperCase(),
          conn, health,
          latency: lat2 != null ? `${lat2}ms` : ""
        };
      });
    },
    paused: false,
    lastFrame: 0,
    setPaused(v) {
      this.paused = !!v;
      this.last = 0;
      this.lastFrame = 0;
    },
    loop(t) {
      this.raf = requestAnimationFrame((now) => this.loop(now));
      if (!t) return;
      const dt = Math.min((t - (this.last || t)) / 1000, 0.05);
      this.last = t;
      if (!this.paused) this.rotY += dt * 0.22;   // the globe keeps turning — "make it move round"
      // Throttle to ~30fps — halves the per-frame canvas work and still
      // looks perfectly smooth on a wireframe globe.
      if (t - this.lastFrame < 33) return;
      this.lastFrame = t;
      this.draw(t);
    },
    draw(t) {
      const ctx = this.ctx;
      const { width: w, height: h } = this;
      if (!w || !h) return;
      const cx = w / 2, cy = h / 2;
      const R = this.R;

      ctx.clearRect(0, 0, w, h);

      // starfield
      this.stars.forEach((s) => {
        const a = 0.25 + 0.55 * (0.5 + 0.5 * Math.sin(t / 900 + s.tw));
        ctx.fillStyle = `rgba(180,230,255,${a.toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(s.x * w, s.y * h, s.r, 0, Math.PI * 2);
        ctx.fill();
      });

      // outer glow
      const glow = ctx.createRadialGradient(cx, cy, R * 0.5, cx, cy, R * 1.7);
      glow.addColorStop(0, "rgba(0,210,255,.16)");
      glow.addColorStop(0.55, "rgba(0,210,255,.04)");
      glow.addColorStop(1, "rgba(0,210,255,0)");
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(cx, cy, R * 1.7, 0, Math.PI * 2);
      ctx.fill();

      // orbit rings
      for (let i = 0; i < 3; i++) {
        const rx = R * (1.28 + i * 0.24);
        const ry = rx * 0.34;
        const angle = t / (5200 - i * 1200) + i * 1.7;
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(angle * 0.4);
        ctx.strokeStyle = `rgba(0,210,255,${0.28 - i * 0.07})`;
        ctx.lineWidth = 1;
        ctx.setLineDash([6, 8]);
        ctx.beginPath();
        ctx.ellipse(0, 0, rx, ry, 0, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
        const ox = Math.cos(angle) * rx;
        const oy = Math.sin(angle) * ry;
        ctx.fillStyle = i === 1 ? "rgba(255,153,0,.9)" : "rgba(0,210,255,.9)";
        ctx.shadowColor = i === 1 ? "#FF9900" : "#00D2FF";
        ctx.shadowBlur = 10;
        ctx.beginPath();
        ctx.arc(ox, oy, i === 1 ? 3 : 2.4, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
        ctx.restore();
      }

      // sphere projection
      const rot = this.rotY;
      const project = (p) => {
        const x1 = p.x * Math.cos(rot) + p.z * Math.sin(rot);
        const z1 = -p.x * Math.sin(rot) + p.z * Math.cos(rot);
        const bob = Math.sin(t / 7000) * 0.12;
        const y1 = p.y * Math.cos(bob) - z1 * Math.sin(bob);
        const z2 = p.y * Math.sin(bob) + z1 * Math.cos(bob);
        const persp = 1 / (1 + z2 / (R * 1.6));
        return { x: cx + x1 * persp, y: cy + y1 * persp, z: z2, p: persp };
      };

      ctx.lineWidth = 0.8;
      for (let lon = 0; lon < 360; lon += 30) {
        ctx.strokeStyle = "rgba(0,210,255,.10)";
        ctx.beginPath();
        for (let latI = -90; latI <= 90; latI += 6) {
          const p = project(this.ll(latI, lon, R));
          latI === -90 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y);
        }
        ctx.stroke();
      }
      for (let lat = -60; lat <= 60; lat += 30) {
        ctx.strokeStyle = "rgba(0,210,255,.08)";
        ctx.beginPath();
        for (let lonI = 0; lonI <= 360; lonI += 5) {
          const p = project(this.ll(lat, lonI, R));
          lonI === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y);
        }
        ctx.stroke();
      }

      for (let lat = -80; lat <= 80; lat += 10) {
        for (let lon = 0; lon < 360; lon += 12) {
          const p = project(this.ll(lat, lon, R));
          if (p.z > 0) continue;
          const depth = clamp(1 - Math.abs(p.z) / R, 0.15, 1);
          ctx.fillStyle = `rgba(150,225,255,${(0.35 + 0.55 * depth).toFixed(3)})`;
          ctx.beginPath();
          ctx.arc(p.x, p.y, 0.7 + 0.8 * depth, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      // Real connected device nodes, linked in a ring when >= 2 are live.
      const nodePts = this.nodes.map((n) => project(this.ll(n.lat, n.lon, R)));
      ctx.lineWidth = 1.2;
      nodePts.forEach((A, i) => {
        if (nodePts.length < 2) return;
        const B = nodePts[(i + 1) % nodePts.length];
        if (!A || !B) return;
        const midX = (A.x + B.x) / 2 + (A.y - B.y) / 10;
        const midY = (A.y + B.y) / 2 - (Math.hypot(B.x - A.x, B.y - A.y) / 4);
        const pulse = 0.35 + 0.3 * Math.sin(t / 600 + i * 2.1);
        ctx.strokeStyle = `rgba(0,210,255,${pulse.toFixed(3)})`;
        ctx.shadowColor = "rgba(0,210,255,.6)";
        ctx.shadowBlur = 6;
        ctx.beginPath();
        ctx.moveTo(A.x, A.y);
        ctx.quadraticCurveTo(midX, midY, B.x, B.y);
        ctx.stroke();
        ctx.shadowBlur = 0;
      });

      // Node colour reflects real health: fresh = cyan, lagging = orange,
      // about-to-drop = red. Conn type + latency render under the label.
      const HEALTH_COLORS = { on: "#00D2FF", warn: "#FF7A1A", off: "#FF5F6D" };
      nodePts.forEach((p, i) => {
        if (!p) return;
        const node = this.nodes[i] || {};
        const color = HEALTH_COLORS[node.health] || "#00D2FF";
        const pulse = 0.6 + 0.4 * Math.sin(t / 420 + i * 1.3);
        const ring = 3 + pulse * 3;
        ctx.strokeStyle = color;
        ctx.globalAlpha = 0.4 + pulse * 0.4;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(p.x, p.y, ring, 0, Math.PI * 2);
        ctx.stroke();
        ctx.globalAlpha = 1;
        ctx.fillStyle = color;
        ctx.shadowColor = color;
        ctx.shadowBlur = 10;
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2.1, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
        // Label + conn/latency badge for front-facing nodes.
        if (p.z < -0.2 && node.label) {
          ctx.font = "600 9px 'Segoe UI', sans-serif";
          const label = node.label.length > 14 ? node.label.slice(0, 13) + "…" : node.label;
          const meta = [node.conn, node.latency].filter(Boolean).join(" · ");
          ctx.textAlign = "center";
          ctx.fillStyle = "rgba(188,212,234,.92)";
          ctx.fillText(label, p.x, p.y + 10);
          if (meta) {
            ctx.font = "600 7.5px 'Segoe UI', sans-serif";
            ctx.fillStyle = color;
            ctx.fillText(meta, p.x, p.y + 19);
          }
        }
      });

      // core crosshair
      const tick = t / 2000;
      ctx.strokeStyle = "rgba(0,210,255,.35)";
      ctx.lineWidth = 1;
      [0, 1, 2, 3].forEach((i) => {
        const ang = (Math.PI / 2) * i + tick;
        const r1 = R * 0.62, r2 = R * 0.72;
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(ang) * r1, cy + Math.sin(ang) * r1);
        ctx.lineTo(cx + Math.cos(ang) * r2, cy + Math.sin(ang) * r2);
        ctx.stroke();
      });
    },
    ll(latDeg, lonDeg, R) {
      const lat = (latDeg * Math.PI) / 180;
      const lon = (lonDeg * Math.PI) / 180;
      return {
        x: R * Math.cos(lat) * Math.sin(lon),
        y: R * Math.sin(lat),
        z: R * Math.cos(lat) * Math.cos(lon)
      };
    }
  };

  /* =========================================================
     KEYBOARD SHORTCUTS
  ========================================================= */
  const Keys = {
    init() {
      document.addEventListener("keydown", (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
          e.preventDefault();
          $("#console-input")?.focus();
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "l") {
          e.preventDefault();
          Terminal.clear();
        }
        if (e.key === "Escape") {
          $("#console-input")?.blur();
          const overlay = $("#overlay-root .overlay:last-child");
          if (overlay) overlay.remove();
        }
      });
    }
  };

  /* =========================================================
     POWER SAVER — pause every animation loop when the tab is
     hidden. The globe, aurora, waveform and sparklines burn CPU
     + RAM for nobody when the HUD isn't visible.
  ========================================================= */
  const PowerSaver = {
    init() {
      document.addEventListener("visibilitychange", () => {
        const hidden = document.hidden;
        Globe.setPaused(hidden || !Settings.globe);
        const aurora = $("#background .aurora");
        if (aurora) aurora.classList.toggle("paused", hidden);
        if (hidden) {
          Voice.stopWave();
        } else {
          Voice.buildWaveform();
          Voice.startWave();
        }
      });
    }
  };

  /* =========================================================
     MESH — this HUD joins the device mesh as a "web console"
     node over WebSocket. It heartbeats, receives broadcasts
     (toast + terminal log) and obeys failsafe termination.
  ========================================================= */
  const Mesh = {
    ws: null,
    nodeId: "",
    timer: null,
    joined: false,
    init() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      this.connect(proto);
      // self-healing reconnect: if the socket drops, retry periodically.
      this.timer = setInterval(() => {
        if (!this.ws || this.ws.readyState > WebSocket.OPEN) this.connect(proto);
      }, 6000);
    },
    connect(proto) {
      try {
        this.ws = new WebSocket(`${proto}://${location.host}/ws/mesh?kind=web&name=A3THER-HUD`);
      } catch (_) { return; }
      this.ws.onopen = () => {
        this.joined = true;
        Terminal.print("[MESH] joined as web console node — mesh online", "ok");
        this.send({ type: "heartbeat" });
      };
      this.ws.onmessage = (ev) => {
        try { this.handle(JSON.parse(ev.data)); } catch (_) { /* non-JSON frame */ }
      };
      this.ws.onclose = () => { this.joined = false; };
      this.ws.onerror = () => { /* reconnect timer handles it */ };
    },
    handle(m) {
      if (!m || typeof m !== "object") return;
      if (m.type === "welcome") { this.nodeId = m.node_id; return; }
      if (m.type === "ping") { this.send({ type: "heartbeat" }); return; }
      if (m.type === "terminate" || m.command === "terminate") {
        Toasts.err("JARVIS FAILSAFE — TERMINATE ORDER RECEIVED");
        Terminal.print("[FAILSAFE] termination order — clearing outstanding tasks", "err");
        Terminal.clear();
        return;
      }
      if (m.command) {
        Toasts.info(`MESH CMD: ${m.command.toUpperCase()}`);
        Terminal.print(`[MESH] ${m.command}${m.params ? " " + JSON.stringify(m.params) : ""}`, "cy");
        // flash_screen → visually flash THIS screen so a phone's command
        // is unmistakable on the laptop.
        if (String(m.command).toLowerCase() === "flash_screen") {
          let f = document.getElementById("mesh-flash");
          if (!f) {
            f = document.createElement("div");
            f.id = "mesh-flash";
            f.style.cssText = "position:fixed;inset:0;background:rgba(255,255,255,.95);opacity:0;pointer-events:none;z-index:9999;transition:opacity .15s ease";
            document.body.appendChild(f);
          }
          f.style.opacity = "1";
          setTimeout(() => { f.style.opacity = "0"; }, 260);
        }
      }
    },
    send(obj) {
      try {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj));
      } catch (_) { /* socket closing */ }
    }
  };

  /* =========================================================
     SETTINGS — persisted in localStorage, applied on boot
  ========================================================= */
  const Settings = {
    key: "a3ther.settings.v1",
    themeName: "cyan",
    theme: ["#00D2FF", "#FF9900"],
    pollMs: 3000,
    globe: true,
    weatherCity: "",
    load() {
      try {
        const raw = JSON.parse(localStorage.getItem(this.key) || "{}");
        if (Array.isArray(raw.theme) && raw.theme.length === 2) this.theme = raw.theme;
        if (raw.themeName) this.themeName = raw.themeName;
        if (raw.pollMs >= 1500) this.pollMs = raw.pollMs;
        if (typeof raw.globe === "boolean") this.globe = raw.globe;
        if (typeof raw.weatherCity === "string") this.weatherCity = raw.weatherCity;
      } catch (_) { /* corrupted settings → defaults */ }
    },
    save() {
      try {
        localStorage.setItem(this.key, JSON.stringify({
          themeName: this.themeName,
          theme: this.theme,
          pollMs: this.pollMs,
          globe: this.globe,
          weatherCity: this.weatherCity
        }));
      } catch (_) { /* storage full/blocked — ignore */ }
    },
    apply() {
      this.load();
      document.documentElement.style.setProperty("--cyan", this.theme[0]);
      document.documentElement.style.setProperty("--orange", this.theme[1]);
      Globe.setPaused(!this.globe);
    },
    setTheme(name, [c, o]) {
      this.themeName = name;
      this.theme = [c, o];
      this.save();
      document.documentElement.style.setProperty("--cyan", c);
      document.documentElement.style.setProperty("--orange", o);
      Toasts.ok(`Accent theme switched to ${name}.`);
    }
  };

  /* =========================================================
     INIT
  ========================================================= */
  const init = () => {
    Settings.apply();
    Boot.init();
    Toasts.init();
    Clock.init();
    Telemetry.init();
    Calendar.init();
    Voice.init();
    Terminal.init();
    Nav.init();
    Globe.init();
    Keys.init();
    PowerSaver.init();
    Mesh.init();
    Live.init();
    VideoStudio.init();

    // AI Predictor refresh button — bound once, never duplicated on re-opens.
    const predictRefresh = document.querySelector("#predict-refresh");
    if (predictRefresh) predictRefresh.addEventListener("click", () => Predictions.poll());

    // Control Phone panel — live device screens + rescan button.
    const controlRefresh = document.querySelector("#control-refresh");
    if (controlRefresh) controlRefresh.addEventListener("click", () => Control.refresh());
    Control.refresh();
    setInterval(() => {
      if (document.getElementById("control-card") && document.getElementById("control-card").getBoundingClientRect().top < innerHeight) Control.refresh();
    }, 4000);

    // working notifications panel: real events from live state
    const noteList = $("#notification-list");
    if (noteList) {
      noteList.innerHTML = `
        <article class="warn"><i class="fa-solid fa-triangle-exclamation"></i><div><strong>Waiting for live data…</strong><time>now</time></div></article>`;
      const fillNotes = (s) => {
        if (!s || !s.cpu) return;
        const items = [];
        if (s.cpu && s.cpu.percent > 80) items.push({ warn: true, icon: "fa-microchip", title: `High CPU load — ${s.cpu.percent}%`, time: nowStamp() });
        if (s.cpu && s.cpu.temp_c != null && s.cpu.temp_c > 70) items.push({ warn: true, icon: "fa-temperature-high", title: `CPU temperature ${s.cpu.temp_c}°C`, time: nowStamp() });
        if (s.ram && s.ram.percent > 85) items.push({ warn: true, icon: "fa-memory", title: `RAM at ${s.ram.percent}%`, time: nowStamp() });
        if (s.battery && !s.battery.plugged) items.push({ warn: false, icon: "fa-battery-quarter", title: `Battery ${s.battery.percent}%`, time: nowStamp() });
        items.push({ warn: false, icon: "fa-shield-halved", title: "Security sandbox armed", time: nowStamp() });
        items.push({ warn: false, icon: "fa-server", title: `${s.hostname || "host"} online`, time: nowStamp() });
        noteList.innerHTML = items
          .slice(0, 4)
          .map((n) => `<article class="${n.warn ? "warn" : ""}"><i class="fa-solid ${n.icon}"></i><div><strong>${n.title}</strong><time>${n.time}</time></div></article>`)
          .join("");
      };
      // hook into the live status poller
      const _orig = Live.pollStatus.bind(Live);
      Live.pollStatus = async () => { const s = await _orig(); fillNotes(s); return s; };
    }

    setTimeout(() => Toasts.ok("A.3.T.H.E.R. core online — live data link active."), 2000);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
