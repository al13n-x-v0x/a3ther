"""
tools/smoke_features.py — offline validation of the six A3THER feature subsystems.

Covers: voice pipeline, security sandbox/HITL, hybrid memory, codebase
indexer + self-heal loop, multi-agent swarm, website maker, and the
features API router. No hardware, no network, no external model calls —
every LLM dependency is stubbed or gracefully degrades.

Run:  python tools/smoke_features.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0
CHECKS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """Record one assertion result."""
    global PASS, FAIL
    CHECKS.append(name)
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# --------------------------------------------------------------------------- #
# 1. VOICE
# --------------------------------------------------------------------------- #
def test_voice() -> None:
    section("VOICE")

    from voice.audio_io import frames_to_bytes, frames_to_float32
    from voice.tts_stream import StreamingSpeaker, _split_sentences
    from voice.pipeline import VoicePipeline, STATES
    from voice.wake_word import create_wake_word, BaseWakeWord

    # Frame conversion helpers.
    frames = [np.full(480, 1000, dtype=np.int16), np.full(480, -1000, dtype=np.int16)]
    raw = frames_to_bytes(frames)
    check("voice.frames_to_bytes", len(raw) == 2 * 480 * 2 and len(raw) > 0, f"len={len(raw)}")
    f32 = frames_to_float32(frames)
    check("voice.frames_to_float32", f32.shape == (960,) and float(np.abs(f32).max()) <= 1.0)

    # Sentence splitting for streaming TTS.
    sentences = _split_sentences("Hello there. This is A3THER speaking! How are you?")
    check("voice.sentence_split", len(sentences) == 3, f"{sentences}")

    # Streaming speaker with a fake player (no audio hardware).
    spoken: list[str] = []

    class FakePlayer:
        def speak(self, text: str) -> None:
            spoken.append(text)

        def stop(self) -> None:
            pass

    speaker = StreamingSpeaker(player=FakePlayer())
    speaker.say("First sentence. Second sentence!")
    time.sleep(0.3)  # let the consumer thread drain the queue
    speaker.stop()
    check("voice.speaker_streams", len(spoken) >= 1, f"spoken={spoken}")

    # Wake-word factory: default engine is vosk (lazy — no model load here).
    try:
        wake = create_wake_word({})
        check("voice.wake_factory", isinstance(wake, BaseWakeWord) and wake.name in ("vosk", "porcupine"), wake.name)
    except Exception as exc:  # pragma: no cover
        check("voice.wake_factory", False, str(exc))

    # Pipeline state machine with fully injected fakes.
    states: list[str] = []
    commands: list[str] = []

    class FakeAudio:
        def __init__(self) -> None:
            self.blocks = 3  # enough audio for one utterance

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def read_block(self, timeout: float = 1.0):
            if self.blocks > 0:
                self.blocks -= 1
                return np.zeros(480, dtype=np.int16)
            return None

        def is_dead(self) -> bool:
            return False

    class FakeWake(BaseWakeWord):
        name = "fake"

        def __init__(self) -> None:
            self.fired = False

        def consume(self, frame) -> bool:
            if not self.fired:
                self.fired = True
                return True
            return False

        def reset(self) -> None:
            self.fired = False

    class FakeTranscriber:
        def __init__(self) -> None:
            self.n = 0

        def feed(self, frame) -> tuple[str, bool]:
            self.n += 1
            if self.n >= 2:
                return "open the terminal", True
            return "", False

        def reset(self) -> None:
            self.n = 0

    class FakeSpeaker2:
        def __init__(self) -> None:
            self.texts: list[str] = []
            self.speaking = False

        @property
        def is_speaking(self) -> bool:
            return self.speaking

        def start(self) -> None:
            pass

        def say(self, text: str) -> None:
            self.texts.append(text)

        def say_now(self, text: str) -> None:
            self.texts.append(text)

        def interrupt(self) -> None:
            pass

        def stop(self) -> None:
            pass

    def process_command(text: str) -> str:
        commands.append(text)
        return "Terminal opened."

    pipeline = VoicePipeline(
        on_state=lambda s, p: states.append(s),
        process_command=process_command,
        audio=FakeAudio(),
        wake=FakeWake(),
        transcriber=FakeTranscriber(),
        speaker=FakeSpeaker2(),
    )
    pipeline.start()
    time.sleep(0.6)
    pipeline.stop()

    check("voice.states_valid", set(states) <= set(STATES) | {"audio_error"}, f"{states}")
    check("voice.wake_to_listen", "wake_listening" in states and "listening" in states, f"{states}")
    check("voice.speaking_happened", "speaking" in states, f"{states}")
    check("voice.command_received", commands == ["open the terminal"], f"{commands}")


# --------------------------------------------------------------------------- #
# 2. SECURITY
# --------------------------------------------------------------------------- #
def test_security() -> None:
    section("SECURITY")

    from security.guard import ApprovalGate, CommandGuard
    from security.sandbox import SandboxedExecutor

    guard = CommandGuard()

    safe = guard.validate("echo hello world")
    check("security.safe_allowed", safe.allowed and not safe.requires_approval, safe.to_dict())

    blocked = guard.validate("rm -rf /")
    check("security.blocked", not blocked.allowed and blocked.risk.value == "blocked", blocked.to_dict())

    dangerous = guard.validate("git push --force origin main")
    check(
        "security.dangerous_needs_approval",
        not dangerous.allowed and dangerous.requires_approval,
        dangerous.to_dict(),
    )

    # Sandboxed executor runs safe commands.
    result = SandboxedExecutor().run("echo hello from sandbox", interactive=False)
    check(
        "security.sandbox_runs",
        result.get("ok") is True and result.get("exit_code") == 0,
        str(result),
    )

    # Blocked command never executes.
    blocked_run = SandboxedExecutor().run("rm -rf /", interactive=False)
    reason = blocked_run.get("reason", "")
    check(
        "security.sandbox_blocks",
        blocked_run.get("blocked") is True and ("Denied" in reason or "Blocked" in reason),
        str(blocked_run),
    )

    # HITL gate: approve path.
    gate = ApprovalGate(use_gui=False, )
    result_box: list[bool] = []

    def ask() -> None:
        result_box.append(gate.request_approval("git push --force", "test", timeout=5))

    t = threading.Thread(target=ask)
    t.start()
    time.sleep(0.2)
    pending = gate.pending()
    check("security.approval_pending", len(pending) == 1, str(pending))
    gate.decide(pending[0]["id"], True)
    t.join(timeout=5)
    check("security.approval_approved", bool(result_box) and result_box[0] is True, str(result_box))

    # HITL gate: deny path.
    gate2 = ApprovalGate(use_gui=False)
    result_box2: list[bool] = []

    def ask2() -> None:
        result_box2.append(gate2.request_approval("drop table users", "test", timeout=5))

    t2 = threading.Thread(target=ask2)
    t2.start()
    time.sleep(0.2)
    pending2 = gate2.pending()
    gate2.decide(pending2[0]["id"], False)
    t2.join(timeout=5)
    check("security.approval_denied", bool(result_box2) and result_box2[0] is False, str(result_box2))


# --------------------------------------------------------------------------- #
# 3. MEMORY
# --------------------------------------------------------------------------- #
def test_memory() -> None:
    section("MEMORY")

    # Force the deterministic lexical embedder (no sentence-transformers model
    # download, no network) so the smoke test is instant and fully offline.
    from memory.embeddings import Embedder

    Embedder._ensure = lambda self: setattr(self, "_model", False)

    from memory.importance import heuristic_score, ImportanceScorer
    from memory.knowledge_graph import KnowledgeGraph
    from memory.vector_store import MemoryUnit, VectorStore
    from memory.orchestrator import MemoryOrchestrator

    tmp = Path(tempfile.mkdtemp(prefix="a3ther_mem_"))
    vector = VectorStore(path=tmp / "episodic.json")
    graph = KnowledgeGraph(path=tmp / "kg.json")

    # Importance scorer.
    check("memory.heuristic_score", heuristic_score("my name is Alice and I work on a3ther") > 0.45,
          str(heuristic_score("my name is Alice and I work on a3ther")))
    check("memory.fluff_filtered", heuristic_score("ok cool") < 0.45,
          str(heuristic_score("ok cool")))

    scorer = ImportanceScorer(gateway=None)
    check("memory.scorer_remembers", scorer.should_remember("I prefer dark mode over light mode"))

    # Vector store upsert + search + dedupe.
    vector.upsert(MemoryUnit(id="a", text="I prefer dark mode over light mode", created=time.time(), updated=time.time(), importance=0.8, category="preference"))
    vector.upsert(MemoryUnit(id="b", text="I like watching sci-fi movies", created=time.time(), updated=time.time(), importance=0.7, category="preference"))
    hits = vector.search("dark mode", k=3)
    check("memory.vector_search", bool(hits) and hits[0][0].id == "a", f"{[(h.id, round(s, 3)) for h, s in hits]}")
    count_before = vector.count()
    vector.upsert(MemoryUnit(id="a", text="I prefer dark mode over light mode", created=time.time(), updated=time.time(), importance=0.9, category="preference"))
    check("memory.vector_dedupe", vector.count() == count_before == 2, f"{count_before} -> {vector.count()}")

    # Knowledge graph dedupe: same triple twice → weight bumps, one edge.
    graph.add_edge("user", "WORKS_ON", "a3ther")
    graph.add_edge("user", "WORKS_ON", "a3ther")
    stats = graph.stats()
    check("memory.graph_dedupe", stats["edges"] == 1, str(stats))
    nbr = graph.neighbors("user")
    check("memory.graph_neighbors", any(e["entity"] == "a3ther" and e["weight"] >= 2.0 for e in nbr), str(nbr))
    block = graph.to_prompt_block(["user"])
    check("memory.graph_prompt_block", "A3THER" in block or "a3ther" in block.lower(), block[:80])

    # Orchestrator background worker (rule-based extraction; no LLM).
    orch = MemoryOrchestrator(vector_store=vector, graph=graph, scorer=scorer, gateway=None)
    orch.observe("I work on the a3ther project")
    deadline = time.time() + 5
    while time.time() < deadline and orch.stats["stored"] == 0:
        time.sleep(0.1)
    check("memory.observe_stored", orch.stats["stored"] > 0, str(orch.stats))
    ctx = orch.build_context("a3ther project")
    check("memory.context_retrieval", "a3ther" in ctx.lower(), ctx[:120])
    check("memory.status", isinstance(orch.status(), dict))


# --------------------------------------------------------------------------- #
# 4. CODEBASE
# --------------------------------------------------------------------------- #
def test_codebase() -> None:
    section("CODEBASE")

    from codebase.agent import CodeWorkspaceAgent
    from codebase.context import assemble_context
    from codebase.indexer import CodeIndexer
    from codebase.tools import TOOL_SCHEMAS, execute_tool

    ws = Path(tempfile.mkdtemp(prefix="a3ther_ws_"))
    demo = ws / "demo.py"
    demo.write_text(
        "def greet(name):\n    return f'hello {name}'\n\n\nclass Robot:\n    def speak(self):\n        return 'beep'\n",
        encoding="utf-8",
    )

    indexer = CodeIndexer(index_path=ws / "code_index.json")
    updated = indexer.index_directory(ws)
    check("codebase.index", updated >= 1, f"updated={updated}")
    hits = indexer.search_symbols("greet")
    check("codebase.symbol_search", any(h.name == "greet" for h in hits), f"{[(h.name, h.file) for h in hits]}")

    # Tool schemas present for function calling.
    names = {s["function"]["name"] for s in TOOL_SCHEMAS}
    check("codebase.tool_schemas", names == {"index_directory", "read_code_file", "search_symbols", "replace_code_block", "create_new_file"}, str(names))

    # Tools execute within scope (repo root).
    new_file = "Output/smoke_created.txt"
    out = execute_tool("create_new_file", {"path": new_file, "content": "hello"})
    check("codebase.create_file", out.startswith("Created"), out[:60])
    created = ROOT / new_file
    check("codebase.create_file_exists", created.exists())
    created.unlink(missing_ok=True)

    # Scope escape is rejected.
    outside = Path(tempfile.mkdtemp()) / "secret.txt"
    outside.write_text("top secret", encoding="utf-8")
    esc = execute_tool("read_code_file", {"path": str(outside)})
    check("codebase.scope_guard", "Security error" in esc or "outside workspace" in esc, esc[:80])

    # Self-heal loop: broken script + stub gateway that knows the fix.
    broken = ws / "broken.py"
    broken.write_text("def compute():\n    return 1 / 0\n\nprint(compute())\n", encoding="utf-8")

    class StubGateway:
        def complete_text(self, prompt: str, **kwargs) -> str:
            # Pretend the LLM produces the fixed file (change 1 / 0 -> 1 / 2).
            return "def compute():\n    return 1 / 2\n\nprint(compute())\n"

        def best_provider(self):
            return None

    agent = CodeWorkspaceAgent(gateway=StubGateway(), scope_root=ws, max_attempts=3, run_timeout=30, indexer=indexer)
    report = agent.run("python broken.py", cwd=ws)
    check("codebase.self_heal_success", report.success, report.summary())
    check("codebase.self_heal_patched", len(report.patched_files) >= 1, str(report.patched_files))
    check("codebase.self_heal_wrote_fix", "1 / 2" in broken.read_text(encoding="utf-8"))

    # Snippet context assembler returns bounded, line-numbered snippets.
    ctx = assemble_context("greet", root=str(ws), max_chars=2000)
    check("codebase.snippet_context", "greet" in ctx and "|" in ctx and len(ctx) <= 2500, f"len={len(ctx)}")


# --------------------------------------------------------------------------- #
# 5. SWARM
# --------------------------------------------------------------------------- #
def test_swarm() -> None:
    section("SWARM")

    # Keep the research agent offline: stub the network-backed search tool.
    import actions.web_search as ws_mod

    ws_mod.web_search = lambda parameters, **kw: (
        f"[stubbed search] {parameters.get('query', '')}"
    )

    from swarm.agents import AGENT_CLASSES, route_to_agent
    from swarm.events import get_event_log
    from swarm.queue import AgentMailbox
    from swarm.state import AgentState
    from swarm.supervisor import SupervisorAgent

    check("swarm.agent_classes", set(AGENT_CLASSES) == {"code", "research", "automation", "mail"}, str(set(AGENT_CLASSES)))
    check("swarm.routing_code", route_to_agent("write a python script to test") == "code", route_to_agent("write a python script to test"))
    check("swarm.routing_research", route_to_agent("research the top email libraries") == "research", route_to_agent("research the top email libraries"))
    check("swarm.routing_automation", route_to_agent("open the terminal and run ls") == "automation", route_to_agent("open the terminal and run ls"))

    # State canvas + hand-off.
    state = AgentState(task="test task")
    state.set("project", "a3ther", by="supervisor")
    check("swarm.state_set_get", state.get("project") == "a3ther")
    check("swarm.state_events", any("set project" in e["message"] for e in state.log))

    mailbox = AgentMailbox(max_workers=2)
    mailbox.send("code", {"task": "refactor", "from": "research"})
    msg = mailbox.receive("code", timeout=1.0)
    check("swarm.mailbox", msg is not None and msg["task"] == "refactor", str(msg))

    # Supervisor end-to-end (rule-based plan, no gateway → graceful messages).
    log_before = len(get_event_log().last(1000))
    sup = SupervisorAgent(gateway=None, mailbox=AgentMailbox(max_workers=2))
    result = sup.run("write a python script and then research alternatives")
    check("swarm.supervisor_ok", result.get("ok") is True, str(result)[:200])
    check("swarm.supervisor_steps", len(result.get("steps", [])) >= 2, str(result.get("steps")))
    check("swarm.supervisor_results", len(result.get("results", [])) == len(result.get("steps", [])), str(result)[:200])
    events = get_event_log().last(1000)
    check("swarm.events_emitted", len(events) > log_before, f"{log_before} -> {len(events)}")
    kinds = {e["kind"] for e in events[log_before:]}
    check("swarm.event_kinds", {"plan", "start", "result", "done"} <= kinds, str(kinds))

    # Hand-off simulation.
    from swarm.agents import BaseAgent

    class FakeAgent(BaseAgent):
        name = "code"

        def handle(self, task: str, state: AgentState) -> str:
            state.set("handoff_done", True, by=self.name)
            return self.transfer_to("research", "investigate output", state)

    st = AgentState(task="x")
    fake = FakeAgent(mailbox=mailbox)
    fake.handle("do it", st)
    check("swarm.transfer_recorded", st.get("handoff_done") is True and any(e["kind"] == "transfer" for e in st.log))


# --------------------------------------------------------------------------- #
# 6. WEBSITE MAKER
# --------------------------------------------------------------------------- #
def test_website() -> None:
    section("WEBSITE MAKER")

    from website_maker.generator import generate_website, list_websites

    result = generate_website(
        "A neon-themed portfolio for a robotics startup that builds humanoid assistants.",
        name="smoke_robo_site",
        theme="neon",
    )
    check("website.generate_ok", result.get("ok") is True, str(result)[:120])
    index = Path(result["path"])
    check("website.file_written", index.exists() and index.stat().st_size > 500, f"bytes={index.stat().st_size if index.exists() else 0}")
    html = index.read_text(encoding="utf-8")
    check("website.html_valid", "<html" in html and "<body" in html)
    check("website.threejs_scene", "three.min.js" in html and "THREE." in html)
    check("website.theme_applied", "NEON" in html.upper() or "0x7df9ff" in html, "theme vars present")

    sites = list_websites()
    check("website.list", any(s["name"] == "smoke_robo_site" for s in sites), str(sites)[:150])


# --------------------------------------------------------------------------- #
# 7. FEATURES API
# --------------------------------------------------------------------------- #
def test_api() -> None:
    section("FEATURES API")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.features import features_router

    app = FastAPI()
    app.include_router(features_router)
    client = TestClient(app)

    r = client.get("/api/security/policy")
    check("api.security_policy", r.status_code == 200 and "blocked_rules" in r.json(), f"{r.status_code}")

    r = client.post("/api/security/check", json={"command": "echo hi"})
    check("api.security_check", r.status_code == 200 and r.json()["decision"]["allowed"] is True, f"{r.status_code} {r.text[:100]}")

    r = client.post("/api/security/check", json={"command": "rm -rf /"})
    check("api.security_check_blocked", r.status_code == 200 and r.json()["decision"]["allowed"] is False, f"{r.status_code} {r.text[:100]}")

    r = client.get("/api/memory/status")
    check("api.memory_status", r.status_code == 200, f"{r.status_code}")

    r = client.get("/api/agents/status")
    check("api.agents_status", r.status_code == 200 and len(r.json()["agents"]) == 4, f"{r.status_code} {r.text[:100]}")

    r = client.get("/api/voice/status")
    check("api.voice_status", r.status_code == 200, f"{r.status_code} {r.text[:100]}")

    r = client.get("/api/website/list")
    check("api.website_list", r.status_code == 200, f"{r.status_code}")

    r = client.post("/api/website/generate", json={"description": "An API smoke site", "name": "smoke_api_site"})
    check("api.website_generate", r.status_code == 200 and r.json().get("ok") is True, f"{r.status_code} {r.text[:120]}")

    r = client.get("/api/codebase/status")
    check("api.codebase_status", r.status_code == 200, f"{r.status_code}")

    # Agents console page is served.
    from backend.api.features import features_ui_router
    ui_app = FastAPI()
    ui_app.include_router(features_ui_router)
    ui_client = TestClient(ui_app)
    r = ui_client.get("/agents")
    check("api.agents_ui", r.status_code == 200 and "swarm" in r.text.lower(), f"{r.status_code} len={len(r.text)}")


# --------------------------------------------------------------------------- #
def main() -> None:
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

    print("A3THER feature smoke test — offline")
    tests = [test_voice, test_security, test_memory, test_codebase, test_swarm, test_website, test_api]
    pool = ThreadPoolExecutor(max_workers=len(tests))
    for test in tests:
        global FAIL
        future = pool.submit(test)
        try:
            future.result(timeout=60)
        except FutureTimeout:
            section(test.__name__)
            print(f"  [TIMEOUT] {test.__name__} exceeded 60s — aborting run")
            FAIL += 1
        except Exception as exc:  # noqa: BLE001
            section(test.__name__)
            print(f"  [ERROR] {test.__name__} raised: {exc}")
            traceback.print_exc()
            FAIL += 1
    pool.shutdown(wait=False)

    print(f"\n{'=' * 46}")
    print(f"RESULT: {PASS} passed, {FAIL} failed ({len(CHECKS)} checks)")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
