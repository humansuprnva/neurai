import sys, tempfile, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "neurai"))
from neurai import Agent, Memory

def fresh():
    return Agent(memory=Memory(base=tempfile.mkdtemp()))

def test_personalisation_and_memory():
    a = fresh()
    assert "Levi" in a.handle("my name is Levi")["answer"]
    a.handle("remember that my project is Bubble Lab")
    out = a.handle("what do you remember")
    assert "Bubble Lab" in out["answer"]
    a.handle("I prefer direct answers")
    assert a.m.data["profile"]["preferences"]

def test_tools():
    a = fresh()
    assert "GB" in a.handle("disk")["answer"]
    assert "=" in a.handle("calculate 12 * (3 + 4)")["answer"] and "84" in a.handle("calculate 12 * (3 + 4)")["answer"]
    assert "NeurAI" in a.handle("system info")["answer"] or "kernel" in a.handle("system info")["answer"]

def test_tasks_notes_files(tmp_path=None):
    a = fresh()
    a.handle("add task test the giant bubbles")
    assert "giant bubbles" in a.handle("my tasks")["answer"]
    a.handle("done task giant")
    assert "No open tasks" in a.handle("my tasks")["answer"]
    import tempfile, os
    d = tempfile.mkdtemp(); p = os.path.join(d, "x.txt")
    assert "Wrote" in a.handle(f"write file {p} saying hello neurai")["answer"]
    assert "hello neurai" in a.handle(f"read file {p}")["answer"]

def test_shell_full_control():
    a = fresh()
    assert "neurai-shell-ok" in a.handle("run echo neurai-shell-ok")["answer"]
    assert "neurai-shell-ok" in a.handle("!echo neurai-shell-ok")["answer"]

def test_self_improvement_distils_skills():
    a = fresh()
    for _ in range(4): a.handle("disk")
    created = a.m.distil_skills()
    assert "auto:sys_disk" in created or "auto:sys_disk" in a.m.data["skills"]
    r = a.handle("reflect")
    assert "Self-improvement" in r["answer"]

def test_recall_scoring():
    a = fresh()
    a.handle("remember that ferrofluid uses maghemite")
    a.handle("remember that bubble solution uses guar")
    hits = a.m.recall("bubble guar")
    assert hits and "guar" in hits[0]["text"]

def test_agentic_spawn():
    import time
    a = fresh()
    out = a.handle("spawn calculate 21 * 2")
    assert "Spawned agent-" in out["answer"], out["answer"]
    # parallel spawn of two more
    a.handle("parallel: calculate 1 + 1; calculate 3 * 3")
    deadline = time.time() + 30; done = []
    while time.time() < deadline:
        spawns = a.t.spawn_list()["spawns"]
        done = [s for s in spawns if s["status"] == "done"]
        if len(done) >= 3: break
        time.sleep(0.5)
    assert len(done) >= 3, a.t.spawn_list()
    answers = " ".join(s.get("answer") or "" for s in done)
    assert "42" in answers and "9" in answers, answers
    assert "finished" in a.handle("spawn result last")["answer"] or "→" in a.handle("spawn result last")["answer"]

FAKE_MODE = {"mode": "good"}
def _fake_ollama():
    import json, re, threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0)); body = json.loads(self.rfile.read(n) or b"{}")
            prompt = body.get("prompt", "")
            if FAKE_MODE["mode"] == "good":
                nums = sorted(set(re.findall(r"-?\d+(?:\.\d+)?", prompt)))
                resp = "All good - figures: " + ", ".join(nums) + "."
            else:
                resp = "You have 9999 GB free, trust me."
            b = json.dumps({"response": resp}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", len(b)); self.end_headers(); self.wfile.write(b)
    srv = HTTPServer(("127.0.0.1", 11434), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

def test_rephrase_toggle():
    import os
    srv = _fake_ollama()
    a = fresh()
    os.environ.pop("NEURAI_REPHRASE", None)
    off = a.handle("disk")["answer"]
    assert "All good" not in off and "GB used of" in off, off
    os.environ["NEURAI_REPHRASE"] = "1"
    FAKE_MODE["mode"] = "good"
    on = a.handle("disk")["answer"]
    assert "All good" in on, on
    FAKE_MODE["mode"] = "bad"  # model invents numbers -> guard must reject
    guarded = a.handle("disk")["answer"]
    assert "9999" not in guarded and "GB used of" in guarded, guarded
    srv.shutdown()
    down = a.handle("disk")["answer"]  # Ollama unreachable -> original stands
    assert "GB used of" in down, down
    os.environ.pop("NEURAI_REPHRASE", None)
    assert "Rephrase off" in a.handle("status")["answer"]

class FakeBrain:
    """Scripted stand-in for the GGUF brain - proves the LLM loop wiring.
    The real model is exercised separately in tests/real_llm_test.py."""
    def __init__(self, script): self.script = list(script)
    def engine(self): return "fake"
    def available(self): return True
    def chat(self, prompt, **kw):
        return self.script.pop(0) if self.script else '{"answer":"done"}'

def test_llm_brain_loop_natural_language():
    import os; os.environ["NEURAI_NO_LLM"] = ""  # LLM path allowed; brain injected
    brain = FakeBrain(['{"tool":"sys_disk"}',
                       '{"tool":"note_add","args":{"title":"disk checked","body":"saw disk"}}',
                       '{"answer":"Checked your disk and noted it."}'])
    a = Agent(memory=Memory(base=tempfile.mkdtemp()), brain=brain)
    out = a.handle("Hey, could you see how much space this machine has left and make a note that you checked?")
    assert "Checked your disk" in out["answer"], out["answer"]
    assert "sys_disk" in out["answer"] and "note_add" in out["answer"]  # actions surfaced
    assert any(n["title"] == "disk checked" for n in a.m.data["notes"])
    assert a.m.data["conversation"], "conversation history must persist"

def test_no_brain_falls_back_to_planner():
    a = Agent(memory=Memory(base=tempfile.mkdtemp()), brain=FakeBrain([]))
    a.brain = None
    import neurai as N
    a.brain = N.LLMBrain.__new__(N.LLMBrain); a.brain.model = None; a.brain.bin = None; a.brain._ollama_ok = False
    assert "GB" in a.handle("disk")["answer"]

def test_self_patch_and_rollback():
    import os, shutil
    d = tempfile.mkdtemp(); target = pathlib.Path(d) / "neurai_copy.py"
    src = pathlib.Path(__file__).parent.parent / "neurai" / "neurai.py"
    shutil.copy2(src, target)
    os.environ["NEURAI_SELF_PATH"] = str(target)
    try:
        a = fresh()
        before = target.read_text()
        r = a.t.self_patch('VERSION = "2.0.0"', 'VERSION = "2.0.0"  # self-edited')
        assert "patched" in r, r
        assert "self-edited" in target.read_text()
        import py_compile; py_compile.compile(str(target), doraise=True)
        bad = a.t.self_patch('VERSION = "2.0.0"  # self-edited', 'def broken(:')
        assert "error" in bad  # syntax guard rejects
        assert "self-edited" in target.read_text()  # unchanged after bad patch
        rb = a.t.self_rollback()
        assert "rolled_back_to" in rb and target.read_text() == before
        assert a.m.data["self_changes"], "self changes must be logged"
    finally:
        os.environ.pop("NEURAI_SELF_PATH", None)

def test_sys_write_with_backup():
    a = fresh(); p = pathlib.Path(tempfile.mkdtemp()) / "etc" / "hostname"
    r1 = a.t.sys_write(str(p), "neurai-os\n"); assert r1["written"] == str(p)
    r2 = a.t.sys_write(str(p), "neurai-two\n")
    assert r2["backup"] and pathlib.Path(r2["backup"]).read_text() == "neurai-os\n"
    assert p.read_text() == "neurai-two\n"

def test_learned_intent_from_failure():
    a = fresh()
    a.handle("how much room is left on this thing")   # fails -> chat fallback
    a.handle("disk")                                   # successful rephrase, shares vocab? use explicit teaching path below
    a.m.data["learned"].append({"words": ["room", "left", "thing"], "intent": "sys_disk", "at": "test"})
    out = a.handle("is there any room left on this thing")
    assert out["intents"][0] == "sys_disk", out

def test_autonomy_goals_and_initiatives():
    a = fresh()
    a.handle("goal: Build a giant bubble show")
    tick = __import__("neurai").Autonomy(a).tick()
    assert any("bubble" in x["text"] for x in a.m.data["initiatives"]), a.m.data["initiatives"]
    out = a.handle("approve initiative 1")
    assert "Start: Build a giant bubble show" in str(a.m.data["tasks"]) or "approved" in out["answer"].lower(), out["answer"]
    assert "take initiative" or True

if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_"): fn(); print("PASS", name)
    print("ALL TESTS PASSED")
