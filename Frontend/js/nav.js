/* ===========================================================
   A.3.T.H.E.R. — nav.js
   Top navigation + dock button wiring
=========================================================== */
(() => {
  "use strict";

  const A3 = window.A3THER;
  const { $, $$ } = A3.Utils;

  const Nav = {
    name: "Nav",
    init() {
      const navButtons = $$("#top-navigation button");
      const dockItems = $$(".dock-item");

      const select = (btn, group) => {
        group.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
      };

      navButtons.forEach((btn) =>
        btn.addEventListener("click", () => {
          select(btn, navButtons);
          A3.Toasts.info(`${btn.textContent.trim().toUpperCase()} panel selected.`);
          dockItems.forEach((d) => {
            const label = (d.textContent || "").trim().toLowerCase();
            const navLabel = btn.textContent.trim().toLowerCase();
            if (label === navLabel) d.classList.add("dock-active");
            else d.classList.remove("dock-active");
          });
        })
      );

      dockItems.forEach((btn) => {
        btn.addEventListener("click", () => {
          select(btn, dockItems);
          const panel = btn.dataset.panel;
          if (panel === "core") {
            $("#ai-core")?.scrollIntoView({ behavior: "smooth", block: "center" });
          }
          A3.Toasts.info(`${(btn.textContent || "").trim().toUpperCase()} dock selected.`);
        });
      });

      const coreItem = $(".dock-item.dock-core");
      if (coreItem) coreItem.addEventListener("click", () => A3.Toasts.ok("A.3.T.H.E.R. core engaged."));
    }
  };

  A3.Nav = Nav;
})();
