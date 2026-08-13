"""ScanGuard — find double-extension masquerades and magic-byte mismatches on Android."""

import asyncio

import toga
from toga.style.pack import COLUMN, ROW, Pack

from .scanner import scan

DEFAULT_PATH = "/storage/emulated/0/Download"


class ScanGuard(toga.App):
    def startup(self):
        """Construct and show the application."""
        self.path_input = toga.TextInput(
            value=DEFAULT_PATH,
            placeholder="Folder path to scan",
            style=Pack(flex=1, padding=(5, 5)),
        )
        self.scan_button = toga.Button(
            "Scan", on_press=self.do_scan, style=Pack(padding=5)
        )
        self.recursive_switch = toga.Switch(
            "Scan subfolders", value=True, style=Pack(padding=5)
        )
        self.output = toga.MultilineTextInput(
            readonly=True,
            placeholder="Results appear here.",
            style=Pack(flex=1, padding=5),
        )
        hint = toga.Label(
            "Tip: on Android 11+, also allow \"Files and media\" (All files "
            "access) in Settings > Apps > ScanGuard > Permissions to read "
            "shared storage. Only the first 16 bytes of each file are read.",
            style=Pack(padding=5),
        )

        input_row = toga.Box(style=Pack(direction=ROW))
        input_row.add(self.path_input)
        input_row.add(self.scan_button)

        main_box = toga.Box(style=Pack(direction=COLUMN))
        main_box.add(toga.Label("ScanGuard", style=Pack(padding=(10, 5, 5, 5))))
        main_box.add(input_row)
        main_box.add(self.recursive_switch)
        main_box.add(self.output)
        main_box.add(hint)

        self.main_window = toga.MainWindow(title=self.formal_name, size=(520, 720))
        self.main_window.content = main_box
        self.main_window.show()

    async def do_scan(self, widget):
        """Scan the entered folder; results go in the output box."""
        path = self.path_input.value.strip()
        recursive = self.recursive_switch.value
        if not path:
            self.output.value = "Enter a folder path to scan."
            return

        self.scan_button.enabled = False
        self.scan_button.text = "Scanning..."
        self.output.value = f"Scanning {path} ...\n"
        try:
            results = await asyncio.to_thread(scan, path, recursive)
        except Exception as exc:  # noqa: BLE001
            self.output.value = (
                f"Error: {exc}\n\n"
                "Make sure the path exists and storage access is granted "
                "(Settings > Apps > ScanGuard > Permissions)."
            )
            return
        finally:
            self.scan_button.enabled = True
            self.scan_button.text = "Scan"

        if not results:
            self.output.value = f"No suspicious files found in {path}."
            return

        lines = []
        for r in results:
            lines.append(f"[{r['severity']}] {r['path']}")
            for note in r["findings"]:
                lines.append(f"    - {note}")
        lines.append(f"\n{len(results)} suspicious file(s) found.")
        self.output.value = "\n".join(lines)


def main():
    return ScanGuard("ScanGuard", "com.scanguard")
