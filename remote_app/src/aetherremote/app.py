"""
AetherRemote — control your A3THER laptop from your phone over the LAN.

Phone half of the Phase-1 remote link. Discovers the laptop (UDP beacon),
pairs with the 6-digit code shown on the laptop, then sends actions:
status, open apps, lock the PC.
"""
from __future__ import annotations

import asyncio
import threading
import uuid

import toga
from toga.style.pack import COLUMN, ROW, Pack

from . import client


class AetherRemote(toga.App):
    def startup(self):
        self.token = ""
        self.base = ""
        self.phone_id = str(uuid.uuid4())

        # -- discovery row -------------------------------------------------- #
        self.device_sel = toga.Selection(items=[], style=Pack(flex=1, padding=5))
        self.discover_btn = toga.Button(
            "Find laptop", on_press=self.do_discover, style=Pack(padding=5)
        )
        disc_row = toga.Box(style=Pack(direction=ROW))
        disc_row.add(self.device_sel)
        disc_row.add(self.discover_btn)

        # -- pairing row ---------------------------------------------------- #
        self.addr_input = toga.TextInput(
            placeholder="or laptop IP (e.g. 192.168.1.50)",
            style=Pack(flex=1, padding=5),
        )
        self.code_input = toga.TextInput(
            placeholder="6-digit code", style=Pack(padding=5, width=110)
        )
        self.pair_btn = toga.Button("Pair", on_press=self.do_pair, style=Pack(padding=5))
        pair_row = toga.Box(style=Pack(direction=ROW))
        pair_row.add(self.addr_input)
        pair_row.add(self.code_input)
        pair_row.add(self.pair_btn)

        # -- status line ---------------------------------------------------- #
        self.status = toga.Label("Not paired.", style=Pack(padding=5))

        # -- command buttons ------------------------------------------------ #
        self.console = toga.MultilineTextInput(
            readonly=True, placeholder="Output appears here.", style=Pack(flex=1, padding=5)
        )
        btn_row1 = toga.Box(style=Pack(direction=ROW))
        btn_row1.add(toga.Button("Status", on_press=lambda w: self.do_command("status"), style=Pack(padding=5)))
        btn_row1.add(toga.Button("Open Chrome", on_press=lambda w: self.do_command("open chrome"), style=Pack(padding=5)))
        btn_row1.add(toga.Button("Open Notepad", on_press=lambda w: self.do_command("open notepad"), style=Pack(padding=5)))
        btn_row2 = toga.Box(style=Pack(direction=ROW))
        btn_row2.add(toga.Button("Lock PC", on_press=lambda w: self.do_command("lock"), style=Pack(padding=5)))
        btn_row2.add(toga.Button("Unpair", on_press=self.do_unpair, style=Pack(padding=5)))

        # -- layout --------------------------------------------------------- #
        main = toga.Box(style=Pack(direction=COLUMN, padding=5))
        main.add(toga.Label("AetherRemote", style=Pack(padding=(10, 5, 5, 5))))
        main.add(disc_row)
        main.add(pair_row)
        main.add(self.status)
        main.add(btn_row1)
        main.add(btn_row2)
        main.add(self.console)

        self.main_window = toga.MainWindow(title=self.formal_name, size=(440, 680))
        self.main_window.content = main
        self.main_window.show()

        # Kick off a background discovery so the laptop shows up right away.
        threading.Thread(target=self._discover_sync, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def _log(self, text: str) -> None:
        self.console.value = (self.console.value + "\n" + text).strip()

    def _discover_sync(self) -> None:
        try:
            devices = client.discover(timeout=2.5)
        except Exception as exc:  # noqa: BLE001
            self._log(f"Discovery error: {exc}")
            return
        items = [f"{d.get('name')}  ({d.get('addr')})" for d in devices]
        if items:
            self.device_sel.items = items
            self.status.text = f"Found {len(devices)} A3THER device(s)."
        else:
            self.status.text = "No A3THER laptop found — enter its IP manually."

    async def do_discover(self, widget=None):
        self.status.text = "Scanning the LAN…"
        await asyncio.to_thread(self._discover_sync)

    def _selected_addr(self) -> str:
        if self.device_sel.value:
            # "A3THER-X  (192.168.1.50)"
            return self.device_sel.value.rsplit("(", 1)[-1].rstrip(")")
        return self.addr_input.value.strip()

    async def do_pair(self, widget=None):
        base = self._selected_addr()
        code = self.code_input.value.strip()
        if not base or not code:
            self.status.text = "Enter the laptop address and the 6-digit code from the laptop."
            return
        self.pair_btn.enabled = False
        self.status.text = "Pairing…"
        try:
            token = await asyncio.to_thread(
                client.confirm, base, code, name="AetherRemote", device_id=self.phone_id
            )
        except Exception as exc:  # noqa: BLE001
            self.status.text = f"Pairing failed: {exc}"
            self.pair_btn.enabled = True
            return
        self.token, self.base = token, client._base(base)
        self.status.text = f"Paired with {base}. Token saved for this session."
        self._log("Pairing successful — you can control the laptop now.")
        self.pair_btn.enabled = True

    def _require_paired(self) -> bool:
        if not self.token or not self.base:
            self.status.text = "Pair first: select the laptop and enter its code."
            return False
        return True

    async def do_command(self, action: str, widget=None):
        if not self._require_paired():
            return
        self.status.text = f"Running: {action}"
        try:
            resp = await asyncio.to_thread(client.command, self.base, self.token, action)
        except Exception as exc:  # noqa: BLE001
            self.status.text = f"Command failed: {exc}"
            return
        result = resp.get("result", {})
        ok = resp.get("ok") or result.get("ok")
        self.status.text = f"{'Done' if ok else 'Failed'}: {action}"
        self._log(f"> {action}")
        if result.get("status"):
            info = result["status"]
            line = ", ".join(f"{k}={v}" for k, v in info.items() if k != "note")
            self._log(f"  {line}")
        elif result.get("stdout"):
            self._log(f"  {result['stdout'].strip()}")
        elif result.get("error"):
            self._log(f"  error: {result['error']}")
        elif result.get("png_base64"):
            self._log("  screenshot captured (%d bytes png)" % len(result["png_base64"]))
        else:
            self._log(f"  {result}")

    def do_unpair(self, widget=None):
        self.token, self.base = "", ""
        self.status.text = "Unpaired. Token cleared."
        self._log("Unpaired.")


def main():
    return AetherRemote("AetherRemote", "com.aether.aetherremote")
