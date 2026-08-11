/* ===========================================================
   A.3.T.H.E.R. — globe.js
   2D canvas globe renderer with visibility-aware pausing
=========================================================== */
(() => {
  "use strict";

  const A3 = window.A3THER;
  const { $, clamp } = A3.Utils;

  const Globe = {
    name: "Globe",
    canvas: null,
    ctx: null,
    width: 0,
    height: 0,
    R: 0,
    rotY: 0,
    stars: [],
    raf: null,
    last: 0,
    visible: true,

    nodeSphere: [
      { lat: 26,  lon: -10,  label: "laptop" },
      { lat: 14,  lon: 38,   label: "mobile" },
      { lat: -12, lon: -24,  label: "workstation" },
      { lat: -34, lon: 12,   label: "pi" },
      { lat: 30,  lon: 56,   label: "esp32" },
      { lat: -6,  lon: -78,  label: "server" },
      { lat: 8,   lon: 104,  label: "tv" },
      { lat: -22, lon: 140,  label: "light" }
    ],
    arcs: [[0, 1], [0, 2], [1, 3], [2, 5], [3, 6], [4, 7], [5, 0], [6, 2]],

    init() {
      this.canvas = $("#globe-canvas");
      if (!this.canvas) return;
      this.ctx = this.canvas.getContext("2d");
      this.buildStars();
      this.resize();
      window.addEventListener("resize", this._onResize = () => this.resize());
      if (window.ResizeObserver) {
        this._ro = new ResizeObserver(() => this.resize());
        this._ro.observe(this.canvas.parentElement);
      }

      // Pause the animation loop when the tab is hidden or the canvas scrolls out of view.
      document.addEventListener("visibilitychange", this._onVis = () => {
        this.setVisible(!document.hidden);
      });
      if ("IntersectionObserver" in window) {
        this._io = new IntersectionObserver((entries) => {
          this.setVisible(entries[0]?.isIntersecting ?? true);
        }, { threshold: 0 });
        this._io.observe(this.canvas);
      } else {
        this.setVisible(true);
      }

      this.loop();
    },
    destroy() {
      if (this.raf) cancelAnimationFrame(this.raf);
      this.raf = null;
      window.removeEventListener("resize", this._onResize);
      document.removeEventListener("visibilitychange", this._onVis);
      if (this._ro) this._ro.disconnect();
      if (this._io) this._io.disconnect();
    },
    setVisible(v) {
      this.visible = !!v;
      if (this.visible) {
        this.last = 0;
        if (!this.raf) this.loop();
      }
    },
    resize() {
      if (!this.canvas || !this.canvas.parentElement) return;
      const stage = this.canvas.parentElement;
      if (!stage.clientWidth || !stage.clientHeight) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.width = stage.clientWidth;
      this.height = stage.clientHeight;
      this.canvas.width = this.width * dpr;
      this.canvas.height = this.height * dpr;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.R = Math.min(this.width / 3.1, this.height / 2.6);
    },
    buildStars() {
      this.stars = [];
      for (let i = 0; i < 90; i++) {
        this.stars.push({
          x: Math.random(),
          y: Math.random(),
          r: Math.random() * 1.3 + 0.3,
          tw: Math.random() * Math.PI * 2
        });
      }
    },
    loop(t) {
      if (!this.visible) return;
      this.raf = requestAnimationFrame((now) => this.loop(now));
      if (!t) return;
      const dt = Math.min((t - (this.last || t)) / 1000, 0.05);
      this.last = t;
      this.rotY += dt * 0.22;
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
        // orbiting dot
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

      // --- sphere: rotate node/point data by rotY ---
      const rot = this.rotY;
      const project = (p) => {
        // rotate around Y
        const x1 = p.x * Math.cos(rot) + p.z * Math.sin(rot);
        const z1 = -p.x * Math.sin(rot) + p.z * Math.cos(rot);
        // slight bob around X
        const bob = Math.sin(t / 7000) * 0.12;
        const y1 = p.y * Math.cos(bob) - z1 * Math.sin(bob);
        const z2 = p.y * Math.sin(bob) + z1 * Math.cos(bob);
        const persp = 1 / (1 + z2 / (R * 1.6));
        return { x: cx + x1 * persp, y: cy + y1 * persp, z: z2, p: persp };
      };

      // sphere wireframe (meridians + parallels)
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

      // dotted surface points
      for (let lat = -80; lat <= 80; lat += 10) {
        for (let lon = 0; lon < 360; lon += 12) {
          const p = project(this.ll(lat, lon, R));
          if (p.z > 0) continue; // back side
          const depth = clamp(1 - Math.abs(p.z) / R, 0.15, 1);
          ctx.fillStyle = `rgba(150,225,255,${(0.35 + 0.55 * depth).toFixed(3)})`;
          ctx.beginPath();
          ctx.arc(p.x, p.y, 0.7 + 0.8 * depth, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      // arc connections between nodes
      const nodePts = this.nodeSphere.map((n) => project(this.ll(n.lat, n.lon, R)));
      ctx.lineWidth = 1.2;
      this.arcs.forEach(([a, b]) => {
        const A = nodePts[a], B = nodePts[b];
        if (!A || !B) return;
        const midX = (A.x + B.x) / 2 + (A.y - B.y) / 10;
        const midY = (A.y + B.y) / 2 - (Math.hypot(B.x - A.x, B.y - A.y) / 4);
        const pulse = 0.35 + 0.3 * Math.sin(t / 600 + a * 2.1);
        ctx.strokeStyle = `rgba(0,210,255,${pulse.toFixed(3)})`;
        ctx.shadowColor = "rgba(0,210,255,.6)";
        ctx.shadowBlur = 6;
        ctx.beginPath();
        ctx.moveTo(A.x, A.y);
        ctx.quadraticCurveTo(midX, midY, B.x, B.y);
        ctx.stroke();
        ctx.shadowBlur = 0;
      });

      // node markers
      nodePts.forEach((p, i) => {
        if (!p) return;
        const pulse = 0.6 + 0.4 * Math.sin(t / 420 + i * 1.3);
        const ring = 3 + pulse * 3;
        ctx.strokeStyle = `rgba(0,210,255,${(0.4 + pulse * 0.4).toFixed(3)})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(p.x, p.y, ring, 0, Math.PI * 2);
        ctx.stroke();
        ctx.fillStyle = "rgba(150,240,255,.95)";
        ctx.shadowColor = "#00D2FF";
        ctx.shadowBlur = 10;
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2.1, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
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

  A3.Globe = Globe;
})();
