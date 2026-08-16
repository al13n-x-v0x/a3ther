"""
manifest.py — the ``a3ther-plugin.json`` manifest system.

A plugin folder is recognised by the presence of this manifest file:

.. code-block:: json

    {
      "name": "system-probe",
      "version": "1.0.0",
      "description": "Live system telemetry.",
      "author": "AL13N Industries",
      "type": "python",
      "entry": "plugin.py",
      "capabilities": [
        {"name": "probe_system", "description": "CPU/RAM/disk/battery stats"}
      ],
      "dependencies": [],
      "enabled": true
    }

``type`` is one of ``python`` | ``javascript`` | ``mcp``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_FILENAME = "a3ther-plugin.json"
VALID_TYPES = ("python", "javascript", "mcp")


@dataclass
class Manifest:
    """Parsed plugin manifest."""

    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    plugin_type: str = "python"
    entry: str = "plugin.py"
    capabilities: list[dict] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    enabled: bool = True
    extra: dict = field(default_factory=dict)
    path: Path | None = None

    def safe_module_name(self) -> str:
        """A filesystem/sys.modules-safe identifier derived from ``name``."""
        base = re.sub(r"\W+", "_", self.name).strip("_").lower()
        return base or "plugin"


def load_manifest(path: str | Path) -> Manifest:
    """Parse and validate an a3ther-plugin.json file."""
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))

    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValueError(f"Plugin manifest {path} is missing 'name'")

    plugin_type = str(raw.get("type", "python")).lower()
    if plugin_type not in VALID_TYPES:
        raise ValueError(
            f"Plugin {name!r}: 'type' must be one of {', '.join(VALID_TYPES)}"
        )

    entry = str(raw.get("entry", "")).strip()
    if not entry:
        entry = "plugin.py" if plugin_type == "python" else "plugin.js"

    return Manifest(
        name=name,
        version=str(raw.get("version", "1.0.0")),
        description=str(raw.get("description", "")),
        author=str(raw.get("author", "")),
        plugin_type=plugin_type,
        entry=entry,
        capabilities=list(raw.get("capabilities") or []),
        dependencies=list(raw.get("dependencies") or []),
        enabled=bool(raw.get("enabled", True)),
        extra={k: v for k, v in raw.items() if k not in {
            "name", "version", "description", "author", "type",
            "entry", "capabilities", "dependencies", "enabled",
        }},
        path=path,
    )


def write_manifest(path: str | Path, manifest: Manifest) -> None:
    """Serialize a manifest back to disk."""
    path = Path(path)
    payload = {
        "name": manifest.name,
        "version": manifest.version,
        "description": manifest.description,
        "author": manifest.author,
        "type": manifest.plugin_type,
        "entry": manifest.entry,
        "capabilities": manifest.capabilities,
        "dependencies": manifest.dependencies,
        "enabled": manifest.enabled,
        **manifest.extra,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def discover_manifests(root: str | Path) -> list[Manifest]:
    """Recursively find every a3ther-plugin.json under ``root``."""
    root = Path(root)
    manifests: list[Manifest] = []
    if not root.exists():
        return manifests
    for path in root.rglob(MANIFEST_FILENAME):
        try:
            manifests.append(load_manifest(path))
        except Exception:
            continue  # skip invalid manifests; report via the manager scan
    return manifests
