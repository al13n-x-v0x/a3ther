"""
remote_dev/viewer.py — the remote screen-viewer page (self-contained HTML).

A single-page client for the A3THER remote stream: shows the laptop screen
live and turns taps / drags / scrolls / keystrokes into REAL input on the
laptop via POST /remote/input.

Design rules:
- Zero external assets (no CDN) — works on a phone anywhere (LAN or Tailnet).
- Coordinates are sent NORMALIZED (0..1), computed from the displayed image
  rect, so the server scales them to the real screen.
- Touch: tap = click · drag = mouse move · long-press (600 ms) = right-click
  · pinch/double-tap = double-click. Mouse: hover = move, wheel = scroll.
- A bottom bar types text and sends special keys.
"""

VIEWER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
<title>A.3.T.H.E.R. — Remote Screen</title>
<style>
  :root { --cyan: #00d2ff; --bg: #05070d; --panel: #0c1220; --line: #1c2b44; --txt: #dbe9f5; --dim: #7a94b8; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { height: 100%; background: var(--bg); color: var(--txt);
    font-family: "Segoe UI", system-ui, sans-serif; overflow: hidden; }
  #bar { display: flex; align-items: center; gap: 10px; padding: 8px 12px;
    background: var(--panel); border-bottom: 1px solid var(--line); }
  #bar .dot { width: 10px; height: 10px; border-radius: 50%; background: #666; flex: none; }
  #bar .dot.on { background: #2bd97c; box-shadow: 0 0 8px #2bd97c; }
  #bar .dot.off { background: #e5484d; box-shadow: 0 0 8px #e5484d; }
  #bar .name { font-weight: 700; letter-spacing: .04em; }
  #bar .sub { color: var(--dim); font-size: 12px; flex: 1; }
  #bar .tip { color: var(--dim); font-size: 11px; }
  #stage { position: relative; height: calc(100% - 96px); background: #000; touch-action: none; }
  #stream { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain;
    image-rendering: auto; user-select: none; -webkit-user-drag: none; }
  #keys { position: absolute; right: 12px; top: 12px; display: flex; flex-direction: column; gap: 6px; }
  #keys button { background: var(--panel); color: var(--txt); border: 1px solid var(--line);
    border-radius: 6px; padding: 6px 10px; font-size: 12px; cursor: pointer; }
  #keys button:active { background: #16263f; }
  #foot { position: absolute; left: 0; right: 0; bottom: 0; display: flex; gap: 8px;
    padding: 10px 12px; background: var(--panel); border-top: 1px solid var(--line); }
  #type { flex: 1; background: #070b14; color: var(--txt); border: 1px solid var(--line);
    border-radius: 6px; padding: 8px 10px; font-size: 15px; outline: none; }
  #type:focus { border-color: var(--cyan); }
  #send { background: var(--cyan); color: #021018; border: 0; border-radius: 6px;
    padding: 8px 16px; font-weight: 700; font-size: 13px; cursor: pointer; }
  #toast { position: absolute; left: 50%; top: 14px; transform: translateX(-50%);
    background: rgba(229,72,77,.92); color: #fff; padding: 6px 14px; border-radius: 8px;
    font-size: 12px; opacity: 0; transition: opacity .25s; pointer-events: none; }
</style>
</head>
<body>
  <div id="bar">
    <span class="dot" id="dot"></span>
    <span class="name">A.3.T.H.E.R.</span>
    <span class="sub" id="status">connecting…</span>
    <span class="tip">tap = click · hold = right-click · drag = move · scroll = wheel</span>
  </div>
  <div id="stage">
    <img id="stream" alt="live screen" />
    <div id="keys">
      <button data-key="esc">Esc</button>
      <button data-key="win">Win</button>
      <button data-key="tab">Tab</button>
      <button data-key="enter">↵</button>
      <button data-key="backspace">⌫</button>
      <button data-key="up">↑</button>
      <button data-key="down">↓</button>
      <button data-key="left">←</button>
      <button data-key="right">→</button>
      <button data-key="ctrl">Ctrl</button>
      <button data-key="alt">Alt</button>
      <button data-key="shift">Shift</button>
    </div>
    <div id="toast"></div>
  </div>
  <div id="foot">
    <input id="type" placeholder="Type on the laptop…" autocomplete="off" />
    <button id="send">SEND</button>
  </div>
<script>
(function () {
  var TOKEN = new URLSearchParams(location.search).get("token") || "";
  var img = document.getElementById("stream");
  var dot = document.getElementById("dot");
  var statusEl = document.getElementById("status");
  var type = document.getElementById("type");
  var toastEl = document.getElementById("toast");

  img.src = "/remote/stream?token=" + encodeURIComponent(TOKEN);
  img.onload = function () { dot.className = "dot on"; statusEl.textContent = "streaming live"; };
  img.onerror = function () {
    dot.className = "dot off";
    statusEl.textContent = "stream offline — is the laptop server running?";
  };

  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.style.opacity = 1;
    setTimeout(function () { toastEl.style.opacity = 0; }, 1800);
  }
  function norm(clientX, clientY) {
    var r = img.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    var x = (clientX - r.left) / r.width, y = (clientY - r.top) / r.height;
    return { x: Math.max(0, Math.min(1, x)), y: Math.max(0, Math.min(1, y)) };
  }
  function sendInput(body, silent) {
    fetch("/remote/input?token=" + encodeURIComponent(TOKEN), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j && j.ok === false && !silent) toast(j.result && j.result.error || j.error || "input failed");
    }).catch(function () {});
  }

  // --- touch / mouse ---
  var down = false, downAt = 0, downX = 0, downY = 0, moved = false, longSent = false, longTimer = null;
  var THRESHOLD = 0.02, LONG_MS = 600;

  img.addEventListener("pointerdown", function (e) {
    e.preventDefault();
    var p = norm(e.clientX, e.clientY); if (!p) return;
    down = true; downAt = Date.now(); downX = p.x; downY = p.y; moved = false; longSent = false;
    clearTimeout(longTimer);
    longTimer = setTimeout(function () {
      if (down && !moved) { sendInput({ type: "button", button: "right", down: true }); longSent = true; }
    }, LONG_MS);
  });
  img.addEventListener("pointermove", function (e) {
    var p = norm(e.clientX, e.clientY); if (!p) return;
    if (down) {
      var dx = Math.abs(p.x - downX), dy = Math.abs(p.y - downY);
      if (dx > THRESHOLD || dy > THRESHOLD) moved = true;
    }
    sendInput({ type: "move", x: p.x, y: p.y });
  });
  function endPointer(e) {
    var p = norm(e.clientX, e.clientY);
    clearTimeout(longTimer);
    if (down && !moved && p) {
      if (longSent) { sendInput({ type: "button", button: "right", down: false }); }
      else sendInput({ type: "click", x: p.x, y: p.y });
    }
    down = false;
  }
  img.addEventListener("pointerup", endPointer);
  img.addEventListener("pointercancel", endPointer);
  img.addEventListener("dblclick", function (e) {
    var p = norm(e.clientX, e.clientY); if (!p) return;
    sendInput({ type: "double", x: p.x, y: p.y });
  });
  img.addEventListener("wheel", function (e) {
    e.preventDefault();
    sendInput({ type: "wheel", dy: -Math.sign(e.deltaY) }, true);
  }, { passive: false });

  // --- keyboard bar ---
  document.querySelectorAll("#keys button").forEach(function (b) {
    b.addEventListener("click", function () { sendInput({ type: "key", key: b.dataset.key }); });
  });
  function sendText() {
    var v = type.value;
    if (!v) return;
    sendInput({ type: "text", text: v });
    type.value = "";
  }
  document.getElementById("send").addEventListener("click", sendText);
  type.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); sendText(); }
  });

  // keep the viewport awake-ish and pinch-proof
  document.addEventListener("gesturestart", function (e) { e.preventDefault(); });
  window.setInterval(function () { img.onload && img.complete; }, 1000);
})();
</script>
</body>
</html>
"""
