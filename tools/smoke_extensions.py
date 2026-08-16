"""Smoke test for A3THER extensions (gateway/autopilot/mcp/plugins/remote_dev)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures = []


def check(name, fn):
    try:
        result = fn()
        print(f"[OK] {name}: {result}")
    except Exception as exc:  # noqa: BLE001
        failures.append((name, exc))
        print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")


# 1. config env-first helpers
def _config():
    from config import base_dir, get_env
    assert (base_dir() / "config").exists(), "base_dir wrong"
    assert get_env("A3THER_SSH_PORT", "22") == "22"
    return f"base_dir={base_dir().name}"


check("config helpers", _config)


# 2. gateway builds with no keys and reports status
def _gateway():
    from gateway.router import get_gateway
    gw = get_gateway()
    assert gw.any_available() is False or gw.get_status(), "status broken"
    order = [s["name"] for s in gw.get_status()]
    assert order, "no providers listed"
    return f"providers={order} any_available={gw.any_available()}"


check("gateway status", _gateway)


# 3. autopilot executor + repair parsing
def _autopilot():
    from autopilot.executor import ProcessRunner
    from autopilot.repair import classify_error, parse_traceback, parse_fenced_code

    runner = ProcessRunner()
    ok = runner.run([sys.executable, "-c", "print('hi')"])
    assert ok.ok() and ok.stdout.strip() == "hi", "runner failed basic exec"
    bad = runner.run([sys.executable, "-c", "import does_not_exist_xyz"])
    assert bad.exit_code != 0 and "ModuleNotFoundError" in bad.combined

    tb = parse_traceback(bad.combined)
    assert tb, "traceback not parsed"
    assert classify_error(bad.combined) == "dependency_error"
    assert parse_fenced_code("```python\nx = 1\n```") == "x = 1"
    return f"exit_code={bad.exit_code} frames={len(tb)} type={classify_error(bad.combined)}"


check("autopilot executor+repair", _autopilot)


# 4. MCP registry loads template config
def _mcp_registry():
    from mcp.registry import MCPRegistry
    reg = MCPRegistry()
    names = [s.name for s in reg.discover(force=True)]
    assert "example-filesystem" in names
    return f"servers={names}"


check("mcp registry", _mcp_registry)


# 5. Plugin manager discovers examples; python plugin loads
def _plugins():
    from plugins.manager import get_plugin_manager
    mgr = get_plugin_manager()
    mgr.discover(force=True)
    plugins = mgr.list_plugins()
    names = [p.name for p in plugins]
    probe = mgr.get("system-probe")
    if probe is not None and probe.loaded:
        result = mgr.execute("probe_cpu")
        assert "CPU" in result
        return f"plugins={names} probe={result!r}"
    return f"plugins={names} (system-probe not loaded: {probe.error if probe else 'missing'})"


check("plugin manager + python plugin", _plugins)


# 6. JS bridge end-to-end via the same transport the MCP host uses
def _js_bridge():
    import json
    from plugins.loader import JsPluginBridge

    bridge = JsPluginBridge(ROOT / "plugins" / "examples" / "web_fetch")
    bridge.connect()
    caps = bridge.capabilities()
    names = [c.get("name") for c in caps]
    # invalid URL -> graceful error via PluginError path
    from plugins.plugin_base import PluginError
    try:
        bridge.call("fetch_page", {"url": "not-a-url"})
        err = None
    except PluginError as exc:
        err = str(exc)
    bridge.close()
    return f"caps={names} invalid-url-error={'yes' if err else 'no'}"


check("js plugin bridge", _js_bridge)


# 7. reasoning intents
def _reasoning():
    from backend.ai.reasoning import Analyze
    assert Analyze("a3ther act as a dev on prod-web")["intent"] == "REMOTE_DEV"
    assert Analyze("list installed plugins")["intent"] == "PLUGIN"
    assert Analyze("open notepad")["intent"] == "OPEN_APP"
    return "REMOTE_DEV/PLUGIN/OPEN_APP ok"


check("reasoning intents", _reasoning)


# 8. remote dev profile loading (template + env fallback)
def _remote():
    from remote_dev.config import load_profiles
    profiles = load_profiles()
    names = [p.name for p in profiles]
    assert "example-prod" in names
    assert all(not hasattr(p, "password") or isinstance(p.password, (str, type(None))) for p in profiles)
    return f"profiles={names}"


check("remote profiles", _remote)


print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILURES"))
for name, exc in failures:
    print(f"  - {name}: {exc}")
sys.exit(1 if failures else 0)
