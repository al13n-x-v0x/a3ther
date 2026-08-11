/* ===========================================================
   A.3.T.H.E.R. — terminal.js
   Live terminal: history, commands, export
=========================================================== */
(() => {
  "use strict";

  const A3 = window.A3THER;
  const { $, pad } = A3.Utils;

  const Terminal = {
    name: "Terminal",
    history: [],
    historyIndex: -1,
    bootLines: [
      ["[10:42:35]", "SYSTEM", "A.3.T.H.E.R. System Initialized", "ok"],
      ["[10:42:36]", "AI CORE", "Online", "cy"],
      ["[10:42:36]", "NEURAL NETWORK", "Active", "ok"],
      ["[10:42:37]", "THINKING ENGINE", "Optimal", "cy"],
      ["[10:42:37]", "DATA SYSTEMS", "Connected", "ok"],
      ["[10:42:38]", "DEVICES", "14 Online", "ok"],
      ["[10:42:38]", "ALL SYSTEMS", "Nominal", "ok"]
    ],
    commands: {
      help: () => [
        "Available commands:",
        "  help            show this help",
        "  status          system + AI status",
        "  devices         list connected nodes",
        "  weather         environment snapshot",
        "  time            current time / date",
        "  uptime          how long core has run",
        "  scan            run a simulated security sweep",
        "  repair          scan + auto-fix the interface",
        "  errors          show recent self-heal reports",
        "  simulate        inject a fake error to test self-heal",
        "  theme cyan|orange|green   switch accent",
        "  echo <text>     repeat a message",
        "  clear           wipe the terminal"
      ],
      status: () => [
        "A.3.T.H.E.R. CORE   : ONLINE",
        "NEURAL NETWORK      : ACTIVE",
        "HEURISTIC MODULES   : 128 / 128",
        "MEMORY CORE         : 128 TB",
        "LEARNING RATE       : 98.7%",
        "LATENCY             : 12 ms",
        "UPTIME              : " + A3.Clock.uptime()
      ],
      devices: () => [
        "AL13N-LAPTOP      WINDOWS 11   ONLINE  ",
        "AL13N-MOBILE      ANDROID 14   ONLINE",
        "STARK-WORKSTATION UBUNTU 24.04 ONLINE",
        "RASPBERRY-PI-5    LINUX ARM    ONLINE",
        "ESP32-NODE-01     IOT DEVICE   ONLINE",
        "STARK-SERVER      192.168.1.10 ONLINE",
        "SMART-TV          LIVING ROOM  ONLINE",
        "SMART-LIGHT       BEDROOM      ONLINE",
        "— 14 total devices synced —"
      ],
      weather: () => [
        "LOCATION  : NEW YORK, USA",
        "TEMP      : 22°C  PARTLY CLOUDY",
        "HUMIDITY  : 62%",
        "WIND      : 12 km/h",
        "PRESSURE  : 1012 hPa",
        "UV INDEX  : 3"
      ],
      time: () => [`Current time: ${nowStamp()}`],
      uptime: () => [`Core uptime: ${A3.Clock.uptime()}`],
      echo: (args) => [args.join(" ") || "(echo nothing)"],
      theme: (args) => {
        const name = (args[0] || "").toLowerCase();
        const themes = {
          cyan:   { c: "#00D2FF", o: "#FF9900" },
          orange: { c: "#FF9900", o: "#00D2FF" },
          green:  { c: "#00FF88", o: "#00D2FF" },
          red:    { c: "#FF4466", o: "#00D2FF" }
        };
        const t = themes[name];
        if (!t) return ["Usage: theme cyan | orange | green | red"];
        document.documentElement.style.setProperty("--cyan", t.c);
        document.documentElement.style.setProperty("--orange", t.o);
        A3.Toasts.ok(`Accent theme switched to ${name}`);
        return [`Accent theme set to ${name}.`];
      },
      scan: () => {
        const steps = [
          "Scanning system files ............ OK",
          "Scanning network interfaces ...... OK",
          "Scanning open ports .............. OK",
          "Verifying process integrity ...... OK",
          "Checking for intrusion ........... NONE DETECTED"
        ];
        return { steps, done: "Security sweep complete.", toast: "Security scan complete — 0 threats.", cls: "ok" };
      },
      repair: () => {
        return { steps: null, done: null, toast: null, run: () => A3.SelfHeal.repairAll(true) };
      },
      errors: () => {
        const reports = A3.SelfHeal ? A3.SelfHeal.reports() : [];
        if (!reports.length) return ["No incidents logged. All systems nominal."];
        return reports.slice(0, 8).map((r) => `[${r.time}] ${r.level.toUpperCase()} ${r.source || "runtime"}: ${r.message}`);
      },
      simulate: () => {
        try {
          throw new Error("SIMULATED_FAULT: telemetry gauge failed to update");
        } catch (err) {
          A3.SelfHeal.ingest(err, "simulated");
        }
        return ["Injected simulated fault. Run 'repair' to fix."];
      }
    },
    init() {
      const body = $("#console-output");
      if (!body) return;
      this.bootLines.forEach(([t, tag, msg, cls]) => this.print(`${t} [${tag}] ${msg}`, cls));

      const input = $("#console-input");
      if (!input) return;
      input.disabled = false;
      this._onKeydown = (e) => this.handleKey(e);
      input.addEventListener("keydown", this._onKeydown);

      const clearBtn = $("#clear-console");
      if (clearBtn) clearBtn.addEventListener("click", () => this.clear());
      const exportBtn = $("#export-console");
      if (exportBtn) exportBtn.addEventListener("click", () => this.exportLog());
    },
    handleKey(e) {
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
    println(text, cls = "cmd") { this.print(text, cls); },
    execute(raw) {
      const parts = raw.split(/\s+/);
      const cmd = parts[0].toLowerCase();
      const args = parts.slice(1);

      if (cmd === "clear") { this.clear(); return; }

      const handler = this.commands[cmd];
      if (!handler) {
        this.print(`Unknown command: '${cmd}'. Type 'help'.`, "err");
        return;
      }

      // scan is a multi-step scripted command
      if (cmd === "scan") return this.runScripted(handler());

      // repair triggers a live self-heal pass
      if (cmd === "repair") {
        const todo = handler();
        if (todo.run) { todo.run(); return; }
      }

      const out = handler(args);
      if (out) out.forEach((line) => this.println(line, cmd === "status" || cmd === "devices" || cmd === "weather" || cmd === "errors" ? "cmd" : "cmd"));
    },
    runScripted(spec) {
      this.print("Initiating security sweep…", "");
      let i = 0;
      const iv = setInterval(() => {
        this.println(spec.steps[i], spec.cls || "ok");
        i++;
        if (i >= spec.steps.length) {
          clearInterval(iv);
          this.println(spec.done, "cy");
          A3.Toasts.ok(spec.toast);
        }
      }, 640);
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
      A3.Toasts.ok("Terminal log exported.");
    },
    log(message) {
      this.init();
      this.print(message, "cy");
    }
  };

  A3.Terminal = Terminal;
})();
