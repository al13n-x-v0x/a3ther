/* ===========================================================
   A.3.T.H.E.R. — telemetry.js
   Animated gauges + sparklines for the left metric cards
=========================================================== */
(() => {
  "use strict";

  const A3 = window.A3THER;
  const { $, $$, clamp, rand } = A3.Utils;

  const METRIC_CONFIG = {
    cpu:     { min: 18, max: 74, label: "23%", color: "#00D2FF" },
    gpu:     { min: 28, max: 86, label: "67%", color: "#FF9900" },
    ram:     { min: 24, max: 78, label: "45%", color: "#00D2FF" },
    storage: { min: 48, max: 56, label: "52%", color: "#00D2FF" },
    network: { min: 30, max: 84, label: "52%", color: "#00D2FF" },
    temp:    { min: 42, max: 70, label: "61°C", color: "#FF9900" }
  };

  const DEFAULT_CONFIG = { min: 20, max: 80, label: "50%", color: "#00D2FF" };

  const Telemetry = {
    name: "Telemetry",
    CIRC: 314.16,
    metrics: {},
    tickTimer: null,
    init() {
      this.metrics = {};
      $$(".metric-card").forEach((card) => {
        const key = card.dataset.metric;
        const cfg = METRIC_CONFIG[key] || { ...DEFAULT_CONFIG, label: card.querySelector("[data-value]")?.textContent || "50%" };
        const base = parseInt(cfg.label, 10) || 50;
        this.metrics[key] = {
          card,
          cfg,
          current: base,
          target: base,
          history: Array(30).fill(base),
          isTemp: key === "temp"
        };
      });
      this.applyAll(0);
      this.tickTimer = setInterval(() => this.tick(), 2200);
    },
    destroy() {
      if (this.tickTimer) clearInterval(this.tickTimer);
      this.tickTimer = null;
    },
    tick() {
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

      if (valueEl) valueEl.textContent = m.isTemp ? `${value}°C` : `${value}%`;
      if (barEl) barEl.style.width = `${value}%`;
      if (fillEl) fillEl.style.strokeDashoffset = (this.CIRC * (1 - value / 100)).toFixed(1);
      this.drawSpark(spark, m.history, color);
    },
    applyAll(mode) {
      Object.values(this.metrics).forEach((m) => {
        const value = Math.round(mode ? m.current : m.target);
        const fillEl = m.card.querySelector("[data-fill]");
        if (fillEl) fillEl.style.transition = mode ? "stroke-dashoffset 1.4s cubic-bezier(.4,0,.2,1)" : "none";
        this.apply(m);
      });
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

  A3.Telemetry = Telemetry;
})();
