/* ===========================================================
   A.3.T.H.E.R. — HoloCommand v2
   core.js — namespace, shared utilities, toasts, boot overlay
=========================================================== */
window.A3THER = window.A3THER || {};

(() => {
  "use strict";

  const A3 = window.A3THER;

  /* ---------- utilities ---------- */
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const clamp = (v, min, max) => Math.min(Math.max(v, min), max);
  const rand = (min, max) => Math.random() * (max - min) + min;
  const pad = (n) => String(n).padStart(2, "0");

  const nowStamp = () => {
    const d = new Date();
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  };

  /* Run a function and, if it throws, queue the failure so the
     self-heal system can replay + repair it once bootstrap is done. */
  const callSafe = (fn, label = "module") => {
    try {
      fn();
      return true;
    } catch (err) {
      (window.__a3herBootErrors = window.__a3herBootErrors || []).push({ label, err });
      console.error(`[A3THER] ${label} failed to init:`, err);
      return false;
    }
  };

  const safeInit = (module) => {
    const label = (module && module.name) ? module.name : "module";
    return callSafe(() => {
      if (module && typeof module.init === "function") module.init();
    }, label);
  };

  /* ---------- toasts ---------- */
  const Toasts = {
    container: null,
    init() {
      if (this.container) return;
      this.container = document.createElement("div");
      this.container.id = "toasts";
      this.container.setAttribute("role", "status");
      this.container.setAttribute("aria-live", "polite");
      document.body.appendChild(this.container);
    },
    show(message, type = "") {
      const toast = document.createElement("div");
      toast.className = `toast ${type}`.trim();
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

  /* ---------- boot overlay ---------- */
  const Boot = {
    init() {
      if ($("#boot-overlay")) return;
      const overlay = document.createElement("div");
      overlay.id = "boot-overlay";
      overlay.setAttribute("role", "status");
      overlay.innerHTML = `
        <div class="boot-box">
          <div class="boot-ring"></div>
          <span class="boot-letter">A</span>
        </div>
        <div class="boot-bar"><i></i></div>
        <p>A.3.T.H.E.R. SYSTEM BOOT // ALL SYSTEMS NOMINAL</p>`;
      document.body.appendChild(overlay);
      setTimeout(() => {
        overlay.classList.add("done");
        setTimeout(() => overlay.remove(), 700);
      }, 2100);
    }
  };

  A3.Utils = { $, $$, clamp, rand, pad, nowStamp, callSafe, safeInit };
  A3.Toasts = Toasts;
  A3.Boot = Boot;
})();
