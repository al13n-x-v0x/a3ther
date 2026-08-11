/* ===========================================================
   A.3.T.H.E.R. — voice.js
   Waveform, health dots, microphone toggle
=========================================================== */
(() => {
  "use strict";

  const A3 = window.A3THER;
  const { $, clamp } = A3.Utils;

  const Voice = {
    name: "Voice",
    bars: [],
    waveTimer: null,
    listening: false,
    init() {
      this.bars = [];
      this.buildWaveform();
      this.buildHealth();
      this.bindMic();
    },
    destroy() {
      if (this.waveTimer) clearTimeout(this.waveTimer);
      this.waveTimer = null;
    },
    buildWaveform() {
      const wave = $("#waveform");
      if (!wave) return;
      wave.innerHTML = "";
      for (let i = 0; i < 26; i++) {
        const bar = document.createElement("span");
        wave.appendChild(bar);
        this.bars.push({ el: bar, h: 20 + Math.random() * 70 });
      }
      this.animateWave();
    },
    animateWave() {
      this.bars.forEach((b) => {
        const base = this.listening
          ? 20 + Math.random() * 80
          : 14 + Math.random() * 34;
        b.h += (base - b.h) * 0.25;
        b.el.style.height = `${clamp(b.h, 6, 100)}%`;
      });
      this.waveTimer = setTimeout(() => this.animateWave(), 130);
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
      this._onMicClick = () => this.toggle();
      mic.addEventListener("click", this._onMicClick);
    },
    toggle() {
      this.listening = !this.listening;
      const mic = $("#microphone");
      if (mic) {
        mic.classList.toggle("listening", this.listening);
        mic.setAttribute("aria-pressed", String(this.listening));
      }
      const text = $("#voice-status-text");
      if (text) text.textContent = this.listening ? "LISTENING — SPEAK NOW" : "READY FOR COMMAND";
      const title = $("#voice-title");
      if (title) title.textContent = this.listening ? "Listening…" : "Listening";
      if (this.listening) {
        A3.Toasts.info("Voice listening started. Speak clearly.");
      } else {
        A3.Toasts.ok("Voice listening stopped.");
      }
    },
    stopListening() {
      if (!this.listening) return;
      this.listening = false;
      const mic = $("#microphone");
      if (mic) {
        mic.classList.remove("listening");
        mic.setAttribute("aria-pressed", "false");
      }
      const text = $("#voice-status-text");
      if (text) text.textContent = "READY FOR COMMAND";
      const title = $("#voice-title");
      if (title) title.textContent = "Listening";
    }
  };

  A3.Voice = Voice;
})();
