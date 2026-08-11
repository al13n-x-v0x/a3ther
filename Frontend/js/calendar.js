/* ===========================================================
   A.3.T.H.E.R. — calendar.js
   Real month grid + sample events
=========================================================== */
(() => {
  "use strict";

  const A3 = window.A3THER;
  const { $, pad } = A3.Utils;

  const WEEKDAY_LABELS = ["S", "M", "T", "W", "T", "F", "S"];

  const Calendar = {
    name: "Calendar",
    view: null,
    events: {
      "2026-08-14": ["14:30", "AI Model Training"],
      "2026-08-21": ["19:00", "System Backup"],
      "2026-08-27": ["11:00", "Project Meeting"]
    },
    init() {
      this.view = new Date();
      this.render();
    },
    render() {
      const body = $("#calendar-body");
      if (!body) return;
      const y = this.view.getFullYear(), m = this.view.getMonth();
      const today = new Date();
      const isCurrent = y === today.getFullYear() && m === today.getMonth();

      const monthLabel = this.view.toLocaleDateString("en-US", { month: "long", year: "numeric" }).toUpperCase();

      const monthRow = document.createElement("div");
      monthRow.className = "cal-month";
      const prev = document.createElement("button");
      prev.type = "button";
      prev.setAttribute("aria-label", "Previous month");
      prev.textContent = "‹";
      const label = document.createElement("strong");
      label.textContent = monthLabel;
      const next = document.createElement("button");
      next.type = "button";
      next.setAttribute("aria-label", "Next month");
      next.textContent = "›";
      prev.addEventListener("click", () => { this.view.setMonth(m - 1); this.render(); });
      next.addEventListener("click", () => { this.view.setMonth(m + 1); this.render(); });
      monthRow.append(prev, label, next);

      const weekdays = document.createElement("div");
      weekdays.className = "cal-weekdays";
      WEEKDAY_LABELS.forEach((d) => {
        const s = document.createElement("span");
        s.textContent = d;
        weekdays.appendChild(s);
      });

      const grid = document.createElement("div");
      grid.className = "cal-grid";
      const first = new Date(y, m, 1).getDay();
      const days = new Date(y, m + 1, 0).getDate();

      for (let i = 0; i < first; i++) {
        const b = document.createElement("span");
        b.className = "cal-day blank";
        grid.appendChild(b);
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
          const t = document.createElement("time");
          t.textContent = time;
          const s = document.createElement("span");
          s.textContent = `${title} — ${day.slice(-2)}`;
          row.append(t, s);
          events.appendChild(row);
        });
      } else if (isCurrent) {
        const row = document.createElement("div");
        row.className = "cal-event";
        const t = document.createElement("time");
        t.textContent = "—";
        const s = document.createElement("span");
        s.textContent = "No events today";
        row.append(t, s);
        events.appendChild(row);
      }

      body.replaceChildren(monthRow, weekdays, grid, events);
    }
  };

  A3.Calendar = Calendar;
})();
