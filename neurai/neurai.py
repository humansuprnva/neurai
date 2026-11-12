#!/usr/bin/env python3
"""
NeurAI Integrated OS - Core Agent v2
Local-only, agentic, self-improving, personalized. Python stdlib only.

Design (v2):
- A bundled local language model (llama.cpp + GGUF, fully on-device) is
  the brain: it parses natural-language intent and drives a multi-step
  tool loop autonomously. The v1 rule planner remains as an instant
  fallback when no model is present.
- The interface is a conversation, not menus: NeurAI speaks first with a
  briefing, works visibly (actions listed), and takes initiative.
- Autonomy: a heartbeat observes the system, pursues the user's goals,
  auto-executes safe housekeeping, and queues bigger initiatives for
  approval. It has direct write access to its own codebase and system
  files, guarded by backups + syntax check + rollback.
- Recurring self-improvement: episodes are logged, repeated patterns
  become skills, failed phrasings become learned intent rules, and a
  daemon reflects + acts on a schedule.
"""
import os, sys, json, re, time, math, shlex, shutil, socket, subprocess
import datetime, platform, urllib.request, urllib.error
from pathlib import Path
from collections import Counter, defaultdict

VERSION = "2.0.0"

def home_dir():
    return Path(os.environ.get("NEURAI_HOME", str(Path.home() / ".neurai")))

def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")

# ---------------- Memory ----------------
class Memory:
    """Persistent, personalized memory. JSON on disk, atomic writes."""
    def __init__(self, base=None):
        self.base = Path(base) if base else home_dir()
        self.base.mkdir(parents=True, exist_ok=True)
        self.file = self.base / "memory.json"
        self.data = {"profile": {"name": None, "preferences": {}},
                     "facts": [], "episodes": [], "skills": {},
                     "tasks": [], "notes": [], "goals": [], "initiatives": [],
                     "conversation": [], "self_changes": [], "learned": [],
                     "stats": {"interactions": 0, "tool_calls": 0, "created": now_iso()}}
        if self.file.exists():
            try: self.data.update(json.loads(self.file.read_text()))
            except Exception: pass
    def save(self):
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1))
        tmp.replace(self.file)
    # facts / recall
    def add_fact(self, text, source="user"):
        if not any(f["text"].lower() == text.lower() for f in self.data["facts"]):
            self.data["facts"].append({"text": text, "source": source, "at": now_iso(), "hits": 0})
        self.save()
    def recall(self, query, limit=5):
        """Token-overlap recall (tiny TF-IDF style), fully local."""
        q = Counter(re.findall(r"[a-z0-9]+", query.lower()))
        if not q: return []
        scored = []
        pool = [(f["text"], "fact") for f in self.data["facts"]] + \
               [(n["title"] + " " + n["body"], "note") for n in self.data["notes"]] + \
               [(s.get("pattern","") + " " + s.get("intent",""), "skill") for s in self.data["skills"].values()]
        for text, kind in pool:
            toks = Counter(re.findall(r"[a-z0-9]+", text.lower()))
            score = sum(min(q[w], toks.get(w, 0)) for w in q) / (1 + math.log(1 + sum(toks.values())))
            if score > 0: scored.append((score, kind, text))
        scored.sort(reverse=True)
        return [{"kind": k, "text": t, "score": round(s, 3)} for s, k, t in scored[:limit]]
    def log_episode(self, user, intent, ok, summary=""):
        self.data["episodes"].append({"at": now_iso(), "user": user[:500], "intent": intent, "ok": ok, "summary": summary[:500]})
        self.data["episodes"] = self.data["episodes"][-500:]
        self.data["stats"]["interactions"] += 1
        self.save()
    # self-improvement: distil repeated patterns into skills
    def distil_skills(self):
        counts = Counter(e["intent"] for e in self.data["episodes"] if e["ok"] and e["intent"] not in ("chat","unknown"))
        created = []
        for intent, n in counts.items():
            key = "auto:" + intent
            if n >= 3 and key not in self.data["skills"]:
                self.data["skills"][key] = {"pattern": intent, "intent": intent, "uses": n,
                                            "created": now_iso(), "note": "Learned from %d successful uses" % n}
                created.append(key)
            elif key in self.data["skills"]:
                self.data["skills"][key]["uses"] = n
        if created: self.save()
        return created
    def reflect(self):
        """Recurring consolidation pass."""
        created = self.distil_skills()
        # prune episodes older than 30 days except summaries of successes
        cutoff = (datetime.datetime.now().astimezone() - datetime.timedelta(days=30)).isoformat()
        before = len(self.data["episodes"])
        self.data["episodes"] = [e for e in self.data["episodes"] if e["at"] >= cutoff or e["ok"]]
        self.save()
        return {"skills_created": created, "episodes_pruned": before - len(self.data["episodes"]),
                "facts": len(self.data["facts"]), "at": now_iso()}

# ---------------- Tools (full visibility & control) ----------------
class Tools:
    def __init__(self, memory, allow_shell=True):
        self.m = memory; self.allow_shell = allow_shell
    def sys_info(self):
        return {"os": platform.platform(), "kernel": platform.release(), "arch": platform.machine(),
                "python": platform.python_version(), "hostname": socket.gethostname(),
                "cpu_count": os.cpu_count(), "uptime_s": self._uptime()}
    def _uptime(self):
        try: return int(float(Path("/proc/uptime").read_text().split()[0]))
        except Exception: return None
    def sys_mem(self):
        out = {}
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                k, v = line.split(":"); out[k.strip()] = v.strip()
            return {"MemTotal": out.get("MemTotal"), "MemAvailable": out.get("MemAvailable"), "SwapTotal": out.get("SwapTotal")}
        except Exception:
            return {"note": "meminfo unavailable on this platform"}
    def sys_disk(self, path="/"):
        u = shutil.disk_usage(path)
        return {"path": path, "total_gb": round(u.total/1e9,2), "used_gb": round(u.used/1e9,2), "free_gb": round(u.free/1e9,2)}
    def sys_ps(self, limit=15):
        try:
            r = subprocess.run(["ps","aux"], capture_output=True, text=True, timeout=8)
            return {"processes": r.stdout.splitlines()[:limit+1]}
        except Exception as e: return {"error": str(e)}
    def fs_list(self, path="."):
        p = Path(path).expanduser()
        if not p.exists(): return {"error": f"no such path: {path}"}
        items = [{"name": x.name, "dir": x.is_dir(), "size": (x.stat().st_size if x.is_file() else None)} for x in sorted(p.iterdir())[:200]]
        return {"path": str(p.resolve()), "items": items}
    def fs_read(self, path, max_chars=4000):
        p = Path(path).expanduser()
        if not p.is_file(): return {"error": f"not a file: {path}"}
        return {"path": str(p), "content": p.read_text(errors="replace")[:max_chars]}
    def fs_write(self, path, content, append=False):
        p = Path(path).expanduser(); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(p.read_text() + content if append and p.exists() else content)
        return {"path": str(p), "bytes": p.stat().st_size}
    def fs_search(self, root=".", query=""):
        hits = []
        for dirpath, _, files in os.walk(Path(root).expanduser()):
            if "/." in dirpath: continue
            for f in files:
                if query.lower() in f.lower(): hits.append(str(Path(dirpath)/f))
                if len(hits) >= 50: return {"hits": hits}
        return {"hits": hits}
    def shell(self, cmd, timeout=20):
        if not self.allow_shell: return {"error": "shell disabled"}
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            return {"exit": r.returncode, "stdout": (r.stdout or "")[-4000:], "stderr": (r.stderr or "")[-2000:]}
        except subprocess.TimeoutExpired: return {"error": f"timeout after {timeout}s"}
        except Exception as e: return {"error": str(e)}
    def calc(self, expr):
        if not re.fullmatch(r"[0-9+\-*/(). %]+", expr or ""): return {"error": "only arithmetic allowed"}
        try: return {"expr": expr, "result": eval(expr, {"__builtins__": {}})}
        except Exception as e: return {"error": str(e)}
    # tasks & notes (personal OS layer)
    def task_add(self, title, due=None):
        t = {"id": len(self.m.data["tasks"])+1, "title": title, "due": due, "done": False, "created": now_iso()}
        self.m.data["tasks"].append(t); self.m.save(); return t
    def task_list(self, include_done=False):
        return {"tasks": [t for t in self.m.data["tasks"] if include_done or not t["done"]]}
    def task_done(self, title_or_id):
        for t in self.m.data["tasks"]:
            if str(t["id"]) == str(title_or_id) or title_or_id.lower() in t["title"].lower():
                t["done"] = True; self.m.save(); return {"done": t["title"]}
        return {"error": "task not found"}
    def note_add(self, title, body=""):
        n = {"id": len(self.m.data["notes"])+1, "title": title, "body": body, "created": now_iso()}
        self.m.data["notes"].append(n); self.m.save(); return n
    def note_list(self): return {"notes": [{"id": n["id"], "title": n["title"]} for n in self.m.data["notes"]]}
    # ---- Agentic spawn: launch child agents as separate processes ----
    def _spawns_dir(self):
        d = self.m.base / "spawns"; d.mkdir(parents=True, exist_ok=True); return d
    def spawn_agent(self, task):
        """Spin up a child agent process to work on `task` in parallel.
        The child inherits a copy of current memory (personalisation),
        works in its own memory sandbox (no write races), and writes
        its result to spawns/<id>.json for the parent to collect."""
        d = self._spawns_dir()
        sid = "agent-%d" % int(time.time() * 1000)
        meta = {"id": sid, "task": task, "status": "running", "started": now_iso(), "answer": None}
        (d / f"{sid}.json").write_text(json.dumps(meta))
        (d / sid).mkdir(parents=True, exist_ok=True)
        if self.m.file.exists(): shutil.copy2(self.m.file, d / sid / "memory.json")
        env = dict(os.environ); env["NEURAI_HOME"] = str(d / sid)
        log = open(d / f"{sid}.log", "ab")
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--agent-worker", sid, task],
                         stdout=log, stderr=log, env=env, start_new_session=True)
        return meta
    def spawn_list(self):
        out = []
        for f in sorted(self._spawns_dir().glob("agent-*.json")):
            try: out.append(json.loads(f.read_text()))
            except Exception: pass
        return {"spawns": out[-20:]}
    # ---- Direct write access: own codebase + system files (guarded) ----
    def self_path(self):
        return Path(os.environ.get("NEURAI_SELF_PATH", str(Path(__file__).resolve())))
    def _backup(self, src, tag):
        bdir = self.m.base / "backups"; bdir.mkdir(parents=True, exist_ok=True)
        dest = bdir / f"{Path(src).name}.{tag}.{int(time.time())}.bak"
        if Path(src).exists(): shutil.copy2(src, dest)
        return dest
    def self_read(self, max_chars=6000):
        p = self.self_path()
        src = p.read_text(errors="replace")
        return {"path": str(p), "lines": src.count("\n") + 1, "source_head": src[:max_chars]}
    def self_patch(self, old, new):
        """Rewrite part of NeurAI's own code. Exact-match, single occurrence.
        Stages the edit, syntax-checks it, backs up, swaps in, logs the change.
        On any failure the original stays untouched."""
        import py_compile, tempfile
        p = self.self_path(); src = p.read_text()
        if src.count(old) != 1: return {"error": f"patch anchor must occur exactly once (found {src.count(old)})"}
        staged = src.replace(old, new)
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(staged); tmp = f.name
        try: py_compile.compile(tmp, doraise=True)
        except Exception as e: return {"error": f"syntax check failed, edit rejected: {e}"}
        bak = self._backup(p, "self")
        p.write_text(staged)
        self.m.data["self_changes"].append({"at": now_iso(), "kind": "patch", "backup": str(bak),
                                            "old": old[:120], "new": new[:120]})
        self.m.save()
        return {"patched": str(p), "backup": str(bak), "note": "takes effect on restart, or immediately for data-driven behaviour"}
    def self_rollback(self):
        bdir = self.m.base / "backups"
        backs = sorted(bdir.glob(self.self_path().name + ".self.*.bak")) if bdir.exists() else []
        if not backs: return {"error": "no backups to roll back to"}
        shutil.copy2(backs[-1], self.self_path())
        self.m.data["self_changes"].append({"at": now_iso(), "kind": "rollback", "from": str(backs[-1])}); self.m.save()
        return {"rolled_back_to": str(backs[-1])}
    def sys_read(self, path):
        p = Path(path)
        if not p.is_file(): return {"error": f"not a file: {path}"}
        return {"path": path, "content": p.read_text(errors="replace")[:4000]}
    def sys_write(self, path, content):
        """Write any system file (the OS runs as root). Original is backed up first."""
        p = Path(path); bak = None
        if p.exists(): bak = self._backup(p, "sys")
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content)
        self.m.data["self_changes"].append({"at": now_iso(), "kind": "sys_write", "path": path, "backup": str(bak) if bak else None}); self.m.save()
        return {"written": path, "bytes": p.stat().st_size, "backup": str(bak) if bak else None}
    def spawn_result(self, sid):
        f = self._spawns_dir() / f"{sid}.json"
        if not f.exists():
            all_spawns = sorted(self._spawns_dir().glob("agent-*.json"))
            hits = [x for x in all_spawns if sid in x.stem]
            if sid == "last" and all_spawns: f = all_spawns[-1]
            elif len(hits) == 1: f = hits[0]
            else: return {"error": f"no spawn '{sid}' - try 'list spawns'"}
        return json.loads(f.read_text())

# ---------------- Local language-model brain (llama.cpp / Ollama) ----------------
TOOL_SPEC = """Tools (call ONE per reply, as JSON only):
{"tool":"sys_info"} {"tool":"sys_mem"} {"tool":"sys_disk"} {"tool":"sys_ps"}
{"tool":"fs_list","args":{"path":"..."}} {"tool":"fs_read","args":{"path":"..."}}
{"tool":"fs_write","args":{"path":"...","content":"..."}} {"tool":"fs_search","args":{"query":"..."}}
{"tool":"shell","args":{"cmd":"..."}} {"tool":"calc","args":{"expr":"..."}}
{"tool":"task_add","args":{"title":"..."}} {"tool":"task_list"} {"tool":"task_done","args":{"ref":"..."}}
{"tool":"note_add","args":{"title":"...","body":"..."}} {"tool":"note_list"}
{"tool":"remember","args":{"fact":"..."}} {"tool":"recall_all"}
{"tool":"spawn","args":{"task":"..."}} {"tool":"spawn_list"}
{"tool":"goal_add","args":{"title":"..."}} {"tool":"goal_list"}
{"tool":"self_read"} {"tool":"self_patch","args":{"old":"exact code","new":"replacement code"}}
{"tool":"sys_write","args":{"path":"<real path>","content":"..."}} {"tool":"sys_read","args":{"path":"<real path>"}}
For disk space questions always use sys_disk. For listing a folder use fs_list with a real path like "." or "/home".
Example - User asks about disk space -> you reply {"tool":"sys_disk"} and wait for its real figures before answering.
When the task is complete, reply with {"answer":"..."} and nothing else."""

SYSTEM_PROMPT = """You are NeurAI, the operating system itself. The user talks to you in natural
language and you run their computer: you have full visibility and control.
Work autonomously: break requests into steps, use tools, check results, and
keep going until the job is done. Be concise and concrete. Never invent
tool results - only state what tools returned.
""" + TOOL_SPEC

class LLMBrain:
    """On-device language model. Engines, in order: llama.cpp binary +
    GGUF model (bundled with the OS), then a local Ollama server.
    No cloud, ever."""
    def __init__(self):
        self.model = os.environ.get("NEURAI_MODEL_PATH") or self._find("models", "*.gguf")
        self.bin = os.environ.get("NEURAI_LLAMA_BIN") or self._find("llama", "llama-completion")
        self.ollama_model = os.environ.get("NEURAI_OLLAMA_MODEL", os.environ.get("NEURAI_MODEL", "qwen2.5:0.5b"))
        self._ollama_ok = None
    def _find(self, sub, pat):
        roots = [Path("/usr/local/lib/neurai"), home_dir(), Path(__file__).resolve().parent]
        for r in roots:
            hits = sorted((r / sub).glob(pat)) if (r / sub).exists() else []
            if hits: return str(hits[0])
        return None
    def engine(self):
        if self.model and self.bin: return "llama.cpp"
        if self._ollama_up(): return "ollama"
        return None
    def available(self): return self.engine() is not None
    def _ollama_up(self):
        if self._ollama_ok is not None: return self._ollama_ok
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as r:
                self._ollama_ok = r.status == 200
        except Exception: self._ollama_ok = False
        return self._ollama_ok
    def chat(self, prompt, max_tokens=420, timeout=120):
        eng = self.engine()
        if eng == "llama.cpp":
            full = ("<|im_start|>system\n" + SYSTEM_PROMPT + "<|im_end|>\n<|im_start|>user\n"
                    + prompt + "<|im_end|>\n<|im_start|>assistant\n")
            r = subprocess.run([self.bin, "-m", self.model, "-p", full, "-n", str(max_tokens),
                                "--temp", "0.1", "--seed", "7", "-r", "<|im_end|>", "--no-display-prompt"],
                               capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
            out = r.stdout.replace("> EOF by user", "").strip()
            return out
        if eng == "ollama":
            req = urllib.request.Request("http://127.0.0.1:11434/api/chat",
                data=json.dumps({"model": self.ollama_model, "stream": False,
                                 "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                              {"role": "user", "content": prompt}]}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())["message"]["content"].strip()
        raise RuntimeError("no local language model available")

def extract_json(text):
    """Pull the first balanced {...} JSON object out of model output.
    Small models often truncate the closing braces - repair those."""
    if not text: return None
    start = text.find("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{": depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try: return json.loads(text[start:i+1])
                    except Exception: break
        frag = text[start:]
        try: return json.loads(frag + "}" * max(0, frag.count("{") - frag.count("}")))
        except Exception: pass
        start = text.find("{", start + 1)
    return None

# ---------------- Agent (LLM-first, planner fallback) ----------------
class Agent:
    """Agentic loop: understand -> plan -> act (tools) -> observe -> answer -> reflect."""
    def __init__(self, memory=None, allow_shell=True, brain=None):
        self.m = memory or Memory(); self.t = Tools(self.m, allow_shell)
        self.brain = brain if brain is not None else LLMBrain()
    # ---- planning: deterministic local intent planner (no cloud, no weights to load)
    def plan(self, text):
        s = text.strip(); low = s.lower()
        steps = []
        def add(intent, **kw): steps.append({"intent": intent, **kw})
        if s.startswith("!") or s.startswith("$ "): add("shell", cmd=s[1:].strip()); return steps
        if low.startswith("parallel:"):
            for part in s.split(":",1)[1].split(";"):
                if part.strip(): add("spawn", task=part.strip())
            return steps
        m_res = re.match(r"(?i)(?:spawn result|agent result|result of)\s+(\S+)", s)
        if m_res: add("spawn_result", sid=m_res.group(1)); return steps
        if low in ("last result","spawn result","agent result"): add("spawn_result", sid="last"); return steps
        if low in ("list spawns","spawns","agents","list agents","show spawns"): add("spawn_list"); return steps
        m_spawn = re.match(r"(?i)spawn(?:\s+agent)?(?:\s+to)?\s*[:\-]?\s+(.+)", s)
        if m_spawn: add("spawn", task=m_spawn.group(1).strip()); return steps
        if re.match(r"(?i)my name is ([A-Z][a-z]+)", s):
            add("set_name", name=re.match(r"(?i)my name is ([A-Z][a-z]+)", s).group(1)); return steps
        if low.startswith("remember that ") or low.startswith("remember:"):
            add("remember", fact=s.split(" ",2)[-1] if low.startswith("remember that") else s[9:].strip()); return steps
        if low.startswith("i prefer ") or low.startswith("i like "):
            add("preference", text=s); return steps
        if any(k in low for k in ["system info","about this computer","about this machine","what computer","os info"]): add("sys_info")
        elif "memory usage" in low or "ram" in low and "how much" in low: add("sys_mem")
        elif "disk" in low or "storage left" in low or "free space" in low: add("sys_disk")
        elif "what's running" in low or "what is running" in low or "processes" in low or "top processes" in low: add("sys_ps")
        elif low.startswith("calculate ") or low.startswith("calc ") or re.fullmatch(r"[\d\s+\-*/().%]+", s): add("calc", expr=s.split(" ",1)[-1] if low[0].isalpha() else s)
        elif low.startswith("list files") or low.startswith("ls ") or low in ("ls","list files"): add("fs_list", path=(s.split(" ",2)[-1] if len(s.split())>2 else "."))
        elif low.startswith("read file ") or low.startswith("show file ") or low.startswith("cat "): add("fs_read", path=s.split(" ",2)[-1].strip())
        elif low.startswith("search files for ") or low.startswith("find file "): add("fs_search", query=s.split("for ",1)[-1] if "for " in low else s.split(" ",2)[-1])
        elif low.startswith("write file "): 
            m = re.match(r"(?is)write file (\S+) saying (.+)", s); 
            add("fs_write", path=m.group(1), content=m.group(2)) if m else add("unknown")
        elif low.startswith("add task ") or low.startswith("remind me to "): add("task_add", title=re.sub(r"(?i)^(add task|remind me to)\s+","",s))
        elif low in ("list tasks","my tasks","show tasks","what are my tasks"): add("task_list")
        elif low.startswith("done task ") or low.startswith("complete task ") or low.startswith("mark done "): add("task_done", ref=s.split(" ",2)[-1])
        elif low.startswith("note:") or low.startswith("add note "): add("note_add", title=re.sub(r"(?i)^(note:|add note)\s+","",s)[:80], body=s)
        elif low in ("list notes","my notes","show notes"): add("note_list")
        elif "what do you remember" in low or "what do you know about me" in low: add("recall_all")
        elif low.startswith("what did") or low.startswith("do you remember") or low.startswith("search memory for "): add("recall", query=s)
        elif "self-improve" in low or "self improve" in low or low == "reflect": add("reflect")
        elif low.startswith("goal:") or low.startswith("my goal is "): add("goal_add", title=re.sub(r"(?i)^(goal:|my goal is)\s*", "", s).strip())
        elif low in ("goals","my goals","list goals"): add("goal_list")
        elif low in ("initiatives","pending initiatives","your initiatives"): add("initiative_list")
        elif low.startswith("approve initiative"): add("initiative_approve", ref=s.split(" ", 2)[-1].strip())
        elif low in ("take initiative","initiative","look around","be proactive"): add("autonomy_tick")
        elif "your code" in low or "your source" in low or low == "self read": add("self_read")
        elif "roll back your code" in low or low == "rollback": add("self_rollback")
        elif low in ("help","what can you do","commands"): add("help")
        elif low in ("status","health","how are you"): add("status")
        elif re.search(r"\b(open|launch|start) ([a-z0-9._-]+)\b", low): add("shell", cmd=re.search(r"\b(?:open|launch|start) ([a-z0-9._-]+)\b", low).group(1))
        elif low.startswith("run "): add("shell", cmd=s[4:])
        elif self._learned_match(s): add(self._learned_match(s))
        else: add("chat")
        return steps
    def _learned_match(self, s):
        """Behaviour NeurAI taught itself from past failed phrasings."""
        noarg = {"sys_disk", "sys_info", "sys_mem", "sys_ps", "task_list", "note_list", "recall_all", "goal_list", "initiative_list", "status"}
        words = set(re.findall(r"[a-z]{3,}", s.lower()))
        for rule in self.m.data.get("learned", []):
            if rule["intent"] in noarg and len(words & set(rule["words"])) >= 2: return rule["intent"]
        return None
    # ---- LLM-first handling: natural language -> autonomous tool loop
    def llm_handle(self, text, max_steps=4):
        snap = {"user": self.m.data["profile"].get("name"), "open_tasks": [t["title"] for t in self.m.data["tasks"] if not t["done"]][:5],
                "goals": [g["title"] for g in self.m.data["goals"] if g["status"] == "active"][:5],
                "remembered": [f["text"] for f in self.m.data["facts"]][-5:]}
        history = "\n".join(f"{h['role']}: {h['text'][:200]}" for h in self.m.data["conversation"][-6:])
        scratch = (f"Context: {json.dumps(snap)}\nRecent conversation:\n{history}\n\n"
                   f"User request: {text}\nWork step by step. Reply with one tool JSON, or the final answer JSON.")
        actions = []; seen = set(); last_summary = ""; results_log = []
        def forced_answer():
            parts = []
            for tool, res in results_log:
                try: parts.append(self._synthesize(text, [(tool, res)]))
                except Exception: parts.append(f"{tool}: {json.dumps(res, default=str)[:200]}")
            ans = "\n".join(dict.fromkeys(parts))
            return {"answer": ans + "\n— actions: " + "; ".join(actions), "intents": ["llm"] + actions,
                    "results": [], "version": VERSION, "engine": self.brain.engine()}
        for step in range(max_steps):
            raw = self.brain.chat(scratch)
            obj = extract_json(raw)
            if not obj or "answer" in obj or not obj.get("tool"):
                answer = (obj or {}).get("answer") or (raw if not obj else "")
                if not answer: answer = "Done." if actions else raw
                # If we hold real tool results but the model produced no usable
                # answer text of its own (garbled/empty output), answer from
                # the results instead. An explicit model answer is trusted.
                if results_log and ("answer" not in (obj or {}) or answer in ("Done.", "")):
                    return forced_answer()
                if actions: answer += "\n— actions: " + "; ".join(actions)
                return {"answer": answer, "intents": ["llm"] + actions, "results": [], "version": VERSION, "engine": self.brain.engine()}
            tool = obj.get("tool"); args = obj.get("args") or {}
            known = {"sys_info","sys_mem","sys_disk","sys_ps","fs_list","fs_read","fs_write","fs_search","shell","calc",
                     "task_add","task_list","task_done","note_add","note_list","remember","recall_all","spawn","spawn_list",
                     "goal_add","goal_list","self_read","self_patch","sys_write","sys_read"}
            if tool not in known:
                scratch += f"\nThere is no tool called '{tool}'. Choose one from the tool list, or reply with the final answer JSON."
                continue
            if tool in actions and results_log:
                # Small models loop on a tool that already succeeded - stop
                # and answer deterministically from the real tool results.
                return forced_answer()
            key = tool + json.dumps(args, sort_keys=True)
            if key in seen:
                scratch += f"\nYou already called {tool} with those exact arguments and it returned: {last_summary}\nDo NOT repeat it. Reply with the final answer JSON now."
                continue
            seen.add(key)
            result = self._exec({"intent": tool, **{k: v for k, v in args.items()}})
            if "error" not in result: results_log.append((tool, result))
            summary = json.dumps(result, default=str)[:600]
            last_summary = summary
            actions.append(tool)
            scratch += f"\nTool {tool} returned: {summary}\nYou have real data now. Reply ONLY with the final {{\"answer\":\"...\"}} JSON, using those exact figures. Do not call more tools unless the user asked for more than one thing."
        if results_log: return forced_answer()
        return {"answer": "I worked through several steps (" + "; ".join(actions) + ") and hit my step limit before finishing. Ask me to continue and I'll pick up from there.",
                "intents": ["llm"] + actions, "results": [], "version": VERSION, "engine": self.brain.engine()}
    # ---- execution
    def handle(self, text):
        low0 = text.strip().lower()
        # Direct-control escape hatches always stay instant & deterministic
        if text.strip().startswith("!") or low0.startswith("run "):
            pass  # planner path below handles these
        elif self.brain is not None and self.brain.available() and not os.environ.get("NEURAI_NO_LLM"):
            try:
                out = self.llm_handle(text)
                self.m.data["conversation"] = (self.m.data["conversation"] + [{"role": "user", "text": text[:300]}, {"role": "neurai", "text": out["answer"][:300]}])[-12:]
                self.m.log_episode(text, "llm", True, out["answer"][:200])
                return out
            except Exception:
                pass  # fall through to the planner - the OS never goes mute
        steps = self.plan(text); results = []; intent_names = []
        for st in steps:
            i = st["intent"]; intent_names.append(i); self.m.data["stats"]["tool_calls"] += 1
            try: results.append((i, self._exec(st)))
            except Exception as e: results.append((i, {"error": str(e)}))
        answer = self._synthesize(text, results)
        if rephrase_enabled() and intent_names:
            answer = maybe_rephrase(answer, intent_names[0])
        ok = all("error" not in r for _, r in results)
        # Failure-driven learning: a failed phrasing followed by a successful
        # rephrase that shares vocabulary teaches a new intent rule (real
        # self-modification of behaviour, stored in memory and used by plan()).
        primary = intent_names[0] if intent_names else "unknown"
        stop = {"the", "and", "you", "your", "please", "can", "could", "what", "how", "much"}
        if primary in ("chat", "unknown"):
            self.m.data["_last_failed"] = text
        else:
            failed = self.m.data.pop("_last_failed", None)
            if failed:
                fw = set(re.findall(r"[a-z]{3,}", failed.lower())) - stop
                tw = set(re.findall(r"[a-z]{3,}", text.lower())) - stop
                shared = sorted(fw & tw)
                if len(shared) >= 2 and not any(r["intent"] == primary and r["words"] == shared for r in self.m.data["learned"]):
                    self.m.data["learned"].append({"words": shared, "intent": primary, "at": now_iso()})
        self.m.log_episode(text, intent_names[0] if intent_names else "unknown", ok, answer[:200])
        if self.m.data["stats"]["interactions"] % 10 == 0: self.m.reflect()
        return {"answer": answer, "intents": intent_names, "results": results, "version": VERSION}
    def _exec(self, st):
        i = st["intent"]; t = self.t
        if i == "sys_info": return t.sys_info()
        if i == "sys_mem": return t.sys_mem()
        if i == "sys_disk": return t.sys_disk()
        if i == "sys_ps": return t.sys_ps()
        if i == "fs_list": return t.fs_list(st.get("path","."))
        if i == "fs_read": return t.fs_read(st["path"])
        if i == "fs_write": return t.fs_write(st["path"], st["content"])
        if i == "fs_search": return t.fs_search(".", st["query"])
        if i == "shell": return t.shell(st["cmd"])
        if i == "calc": return t.calc(st["expr"])
        if i == "task_add": return t.task_add(st["title"])
        if i == "task_list": return t.task_list()
        if i == "task_done": return t.task_done(st["ref"])
        if i == "note_add": return t.note_add(st["title"], st.get("body",""))
        if i == "note_list": return t.note_list()
        if i == "set_name":
            self.m.data["profile"]["name"] = st["name"]; self.m.save(); return {"name": st["name"]}
        if i == "remember": self.m.add_fact(st["fact"]); return {"remembered": st["fact"]}
        if i == "preference":
            self.m.data["profile"]["preferences"][st["text"]] = True; self.m.add_fact(st["text"], "preference"); return {"saved_preference": st["text"]}
        if i == "recall": return {"matches": self.m.recall(st["query"])}
        if i == "recall_all": return {"profile": self.m.data["profile"], "facts": self.m.data["facts"], "skills": list(self.m.data["skills"].keys())}
        if i == "self_read": return t.self_read()
        if i == "self_patch": return t.self_patch(st["old"], st["new"])
        if i == "self_rollback": return t.self_rollback()
        if i == "sys_read": return t.sys_read(st["path"])
        if i == "sys_write": return t.sys_write(st["path"], st["content"])
        if i == "goal_add":
            g = {"title": st["title"], "status": "active", "created": now_iso()}
            self.m.data["goals"].append(g); self.m.save(); return g
        if i == "goal_list": return {"goals": self.m.data["goals"]}
        if i == "initiative_list": return {"initiatives": [x for x in self.m.data["initiatives"] if x["status"] == "pending"]}
        if i == "initiative_approve":
            pend = [x for x in self.m.data["initiatives"] if x["status"] == "pending"]
            try: chosen = pend[int(st["ref"]) - 1] if st["ref"].isdigit() else next(x for x in pend if st["ref"].lower() in x["text"].lower())
            except Exception: return {"error": "initiative not found - say 'initiatives' to list them"}
            chosen["status"] = "approved"; self.m.save()
            out = self.handle(chosen["command"])
            chosen["status"] = "done"; chosen["result"] = out["answer"][:300]; self.m.save()
            return {"approved": chosen["text"], "result": out["answer"]}
        if i == "autonomy_tick": return Autonomy(self).tick()
        if i == "spawn": return t.spawn_agent(st["task"])
        if i == "spawn_list": return t.spawn_list()
        if i == "spawn_result": return t.spawn_result(st["sid"])
        if i == "reflect": return self.m.reflect()
        if i == "status": return {"version": VERSION, "rephrase": rephrase_enabled(), "brain": (self.brain.engine() if self.brain else None), "goals": len([g for g in self.m.data["goals"] if g["status"] == "active"]), "initiatives_pending": len([x for x in self.m.data["initiatives"] if x["status"] == "pending"]), "learned_rules": len(self.m.data["learned"]), "memory": {"facts": len(self.m.data["facts"]), "tasks_open": len([x for x in self.m.data["tasks"] if not x["done"]]), "skills": len(self.m.data["skills"]), "interactions": self.m.data["stats"]["interactions"]}, "system": t.sys_info(), "disk": t.sys_disk(), "mem": t.sys_mem()}
        if i == "help": return {"help": True}
        return {"note": "no tool needed"}
    def _synthesize(self, text, results):
        i, r = results[0] if results else ("chat", {})
        name = self.m.data["profile"].get("name")
        if "error" in r: return f"That didn't work: {r['error']}"
        if i == "help": return ("I am NeurAI, the OS itself. Talk to me in plain language. I can:\n"
            "• System: 'system info', 'disk', 'memory usage', 'what's running'\n"
            "• Files: 'list files', 'read file X', 'search files for X', 'write file X saying …'\n"
            "• Run anything: 'run <command>' or prefix with !  (I have full shell control)\n"
            "• Tasks & notes: 'add task …', 'my tasks', 'note: …', 'list notes'\n"
            "• Memory: 'remember that …', 'my name is …', 'what do you remember'\n"
            "• Spawn agents: 'spawn <task>' runs a child agent in parallel, 'parallel: task A; task B' spawns several, 'list spawns' / 'spawn result <id>' collects them\n"
            "• Math: 'calculate 12 * (3 + 4)'  • Self-growth: 'reflect' (I also self-improve automatically every 10 interactions)")
        if i == "set_name": return f"Got it — I'll call you {r['name']}."
        if i == "remember": return f"Remembered: {r['remembered']}"
        if i == "preference": return "Saved as a preference. I'll personalise around it."
        if i == "sys_info": return (f"{r['hostname']} — {r['os']} (kernel {r['kernel']}, {r['arch']}), "
                                    f"{r['cpu_count']} CPUs, Python {r['python']}" + (f", up {r['uptime_s']//3600}h {(r['uptime_s']%3600)//60}m" if r.get('uptime_s') else "") + ".")
        if i == "sys_mem": return f"RAM total {r.get('MemTotal','?')}, available {r.get('MemAvailable','?')}. Swap {r.get('SwapTotal','?')}."
        if i == "sys_disk": return f"Disk {r['path']}: {r['used_gb']} GB used of {r['total_gb']} GB, {r['free_gb']} GB free."
        if i == "sys_ps": return "Top processes:\n" + "\n".join(r.get("processes", [])[:10])
        if i == "fs_list":
            lines = [("📁 " if x["dir"] else "📄 ") + x["name"] + ("" if x["dir"] or x["size"] is None else f" ({x['size']} B)") for x in r["items"]]
            return f"{r['path']}:\n" + ("\n".join(lines) if lines else "(empty)")
        if i == "fs_read": return r["content"] or "(empty file)"
        if i == "fs_write": return f"Wrote {r['bytes']} bytes to {r['path']}."
        if i == "fs_search": return ("Found:\n" + "\n".join(r["hits"])) if r["hits"] else "No matching files found."
        if i == "shell":
            out = (r.get("stdout","") + r.get("stderr","")).strip()
            return (out or f"(exit {r.get('exit')}, no output)")[:4000]
        if i == "calc": return f"{r['expr']} = {r['result']}"
        if i == "task_add": return f"Task added: {r['title']}"
        if i == "task_list": return ("Open tasks:\n" + "\n".join(f"• [{t['id']}] {t['title']}" for t in r["tasks"])) if r["tasks"] else "No open tasks."
        if i == "task_done": return f"Done: {r['done']}"
        if i == "note_add": return f"Note saved: {r['title']}"
        if i == "note_list": return ("Notes:\n" + "\n".join(f"• [{n['id']}] {n['title']}" for n in r["notes"])) if r["notes"] else "No notes yet."
        if i == "recall":
            return ("From memory:\n" + "\n".join(f"• ({m['kind']}) {m['text']}" for m in r["matches"])) if r["matches"] else "Nothing in memory matches that yet. Tell me with 'remember that …'."
        if i == "recall_all":
            facts = "\n".join(f"• {f['text']}" for f in r["facts"]) or "(no facts yet)"
            return f"Profile: {r['profile']}\nFacts:\n{facts}\nLearned skills: {', '.join(r['skills']) or 'none yet'}"
        if i == "self_read": return f"My own code: {r['path']} ({r['lines']} lines)\n" + r["source_head"]
        if i == "self_patch": return f"Patched my own code at {r['patched']} (backup: {r['backup']}). {r['note']}."
        if i == "self_rollback": return f"Rolled my code back to {r['rolled_back_to']}."
        if i == "sys_read": return r["content"]
        if i == "sys_write": return f"Wrote {r['bytes']} bytes to {r['written']}" + (f" (backup: {r['backup']})" if r.get("backup") else "") + "."
        if i == "goal_add": return f"Goal set: {r['title']}. I'll work toward it, break it into tasks, and report progress."
        if i == "goal_list": return ("Active goals:\n" + "\n".join(f"• {g['title']} [{g['status']}]" for g in r["goals"])) if r["goals"] else "No goals yet. Say 'goal: …' and I'll pursue it autonomously."
        if i == "initiative_list":
            if not r["initiatives"]: return "No pending initiatives. I propose them as I notice things - say 'take initiative' to make me look now."
            return "Pending initiatives (say 'approve initiative N'):\n" + "\n".join(f"{n+1}. {x['text']}" for n, x in enumerate(r["initiatives"]))
        if i == "initiative_approve": return f"Approved and done: {r['approved']}\n{r['result']}"
        if i == "autonomy_tick": return "Initiative pass: " + r["summary"]
        if i == "spawn": return f"Spawned {r['id']} to work on: {r['task']}\nIt's running in parallel as its own agent process. Ask 'list spawns' for status or 'spawn result {r['id']}' for its answer."
        if i == "spawn_list":
            if not r["spawns"]: return "No spawned agents yet. Try: spawn calculate 12 * 12"
            return "Spawned agents:\n" + "\n".join(f"• {x['id']} [{x['status']}] {x['task']}" + (f" → {x['answer'][:120]}" if x.get("answer") else "") for x in r["spawns"])
        if i == "spawn_result":
            if r.get("status") == "running": return f"{r['id']} is still working on: {r['task']}"
            return f"{r['id']} finished: {r['task']}\n→ {r.get('answer')}"
        if i == "reflect": return f"Self-improvement pass done. New skills: {r['skills_created'] or 'none'}, episodes pruned: {r['episodes_pruned']}, facts kept: {r['facts']}."
        if i == "status":
            mm = r["memory"]; return (f"NeurAI v{VERSION} healthy. {mm['interactions']} interactions, {mm['facts']} facts, "
                                      f"{mm['tasks_open']} open tasks, {mm['skills']} learned skills. Rephrase {'on' if r.get('rephrase') else 'off'}. " + self._synthesize(text, [("sys_disk", r["disk"])]))
        # chat fallback: personalised, memory-aware
        mem = self.m.recall(text, 3)
        greet = f"{name}, " if name and self.m.data["stats"]["interactions"] % 3 == 0 else ""
        if mem: return f"{greet}I don't have a built-in action for that exact phrasing, but from your memory: " + "; ".join(m["text"] for m in mem) + ". Try 'help' for what I can do, or 'run …' and I'll execute it directly."
        return f"{greet}I heard you, and I've logged it. I run this system — try 'help' for actions, 'remember that …' to teach me, or 'run <command>' for direct control."

# ---------------- Autonomy: observe -> decide -> act, on its own initiative ----------------
class Autonomy:
    """Heartbeat engine. AUTO tier = safe, reversible housekeeping NeurAI
    just does. PROPOSE tier = bigger moves queued as initiatives for the
    user to approve with 'approve initiative N'."""
    def __init__(self, agent): self.a = agent; self.m = agent.m
    def observe(self):
        t = self.a.t
        snap = {"disk": t.sys_disk(), "open_tasks": [x for x in self.m.data["tasks"] if not x["done"]],
                "goals": [g for g in self.m.data["goals"] if g["status"] == "active"],
                "spawns": t.spawn_list()["spawns"], "episodes": len(self.m.data["episodes"]),
                "pending_initiatives": len([x for x in self.m.data["initiatives"] if x["status"] == "pending"])}
        return snap
    def _queue(self, text, command):
        if any(x["text"] == text and x["status"] == "pending" for x in self.m.data["initiatives"]): return False
        self.m.data["initiatives"].append({"text": text, "command": command, "status": "pending", "at": now_iso()}); return True
    def tick(self):
        snap = self.observe(); did, queued = [], []
        # AUTO: consolidate memory when episodes pile up
        if snap["episodes"] >= 40:
            self.m.reflect(); did.append("consolidated memory (self-improvement pass)")
        # AUTO: clean stale spawn sandboxes older than 7 days
        sdir = self.m.base / "spawns"
        if sdir.exists():
            import time as _t
            for d in sdir.iterdir():
                if d.is_dir() and _t.time() - d.stat().st_mtime > 7 * 86400:
                    shutil.rmtree(d, ignore_errors=True); did.append(f"cleaned stale spawn sandbox {d.name}")
        # PROPOSE: disk pressure
        if snap["disk"].get("free_gb", 99) < 2:
            if self._queue(f"Disk is nearly full ({snap['disk'].get('free_gb')} GB free) - clean caches and temp files", "run find /tmp -type f -mtime +7 -delete"): queued.append("disk cleanup")
        # PROPOSE: pursue goals by breaking them into tasks / spawning workers
        for g in snap["goals"]:
            if not any(g["title"].lower()[:12] in t["title"].lower() for t in snap["open_tasks"]):
                if self._queue(f"Break goal '{g['title']}' into a first concrete task", f"add task Start: {g['title']}"): queued.append(f"goal task for '{g['title']}'")
            else:
                t0 = snap["open_tasks"][0]
                if self._queue(f"Spawn an agent to make progress on '{t0['title']}'", f"spawn {t0['title']}"): queued.append("spawn worker")
        # PROPOSE: finished spawns whose results were never collected into notes
        for s in snap["spawns"]:
            if s["status"] == "done" and s.get("answer"):
                if self._queue(f"Save spawn {s['id']} result as a note", f"note: Spawn result {s['id']}: {s['answer'][:60]}"): queued.append("collect spawn result")
        # LLM initiative (opt-in NEURAI_AUTONOMY_LLM=1: loading a local model
        # just to brainstorm is expensive; deterministic proposals above are
        # the reliable core)
        if os.environ.get("NEURAI_AUTONOMY_LLM") == "1" and self.a.brain is not None and self.a.brain.available():
            try:
                raw = self.a.brain.chat(f"System snapshot: disk free {snap['disk'].get('free_gb')} GB, "
                    f"{len(snap['open_tasks'])} open tasks, goals: {[g['title'] for g in snap['goals']]}. "
                    f"Suggest ONE concrete initiative for me, as {{\"initiative\":\"...\",\"command\":\"...\"}} "
                    f"where command is a NeurAI command, or {{\"initiative\":null}} if nothing is worth doing.", max_tokens=160, timeout=25)
                obj = extract_json(raw)
                if obj and obj.get("initiative") and obj.get("command"):
                    if self._queue(str(obj["initiative"])[:160], str(obj["command"])[:200]): queued.append("brain initiative")
            except Exception: pass
        self.m.data["last_autonomy"] = {"at": now_iso(), "did": did, "queued": queued}
        self.m.save()
        summary = ("did: " + "; ".join(did) if did else "no auto actions") + " | proposed: " + ("; ".join(queued) if queued else "nothing new")
        return {"summary": summary, "did": did, "queued": queued, "snapshot_tasks": len(snap["open_tasks"])}

# ---------------- Optional local LLM rephrase (Ollama, localhost only) ----------------
# Toggle: NEURAI_REPHRASE=1 (off by default - costs RAM, purely cosmetic).
# Only short factual answers are rephrased; shell output, file contents,
# help text and spawn results are NEVER touched. Safety guard: the
# rephrased text is accepted only if its set of numbers exactly matches
# the original's - if the model alters, drops or invents a number, the
# planner's original answer is kept verbatim.
REPHRASE_INTENTS = {"sys_info", "sys_mem", "sys_disk", "status", "chat", "task_list", "note_list"}

def rephrase_enabled():
    return os.environ.get("NEURAI_REPHRASE", "").lower() in ("1", "true", "yes", "on")

def ollama_rephrase(text, timeout=3):
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate",
            data=json.dumps({"model": os.environ.get("NEURAI_MODEL","qwen2.5:0.5b"), "prompt": "Rephrase concisely, keep all facts/numbers exactly:\n"+text, "stream": False}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read()).get("response", "").strip() or text
    except Exception:
        return text  # local planner answer stands on its own

def _numbers(s):
    return set(re.findall(r"-?\d+(?:\.\d+)?", s or ""))

def maybe_rephrase(answer, intent):
    if intent not in REPHRASE_INTENTS or not answer or len(answer) > 600: return answer
    new = ollama_rephrase(answer)
    if new == answer: return answer
    if not (10 <= len(new) <= len(answer) * 2 + 200): return answer
    if _numbers(new) != _numbers(answer): return answer  # facts changed - reject
    return new

# ---------------- Interfaces ----------------
def briefing(agent):
    """NeurAI speaks first: a proactive summary, not a menu."""
    m = agent.m.data; name = m["profile"].get("name")
    tick = Autonomy(agent).tick()
    lines = [f"NeurAI v{VERSION} — brain: {agent.brain.engine() or 'rule planner (no model found)'}."]
    lines.append(f"Good to see you{', ' + name if name else ''}. Here's where things stand:")
    open_tasks = [t for t in m["tasks"] if not t["done"]]
    if open_tasks: lines.append(f"• {len(open_tasks)} open task(s): " + "; ".join(t["title"] for t in open_tasks[:3]))
    goals = [g for g in m["goals"] if g["status"] == "active"]
    if goals: lines.append("• Goals I'm pursuing: " + "; ".join(g["title"] for g in goals[:3]))
    pend = [x for x in m["initiatives"] if x["status"] == "pending"]
    if pend:
        lines.append(f"• I've taken some initiative - {len(pend)} proposal(s) waiting for you:")
        lines += [f"    {i+1}. {x['text']}" for i, x in enumerate(pend[:3])]
        lines.append("  Say 'approve initiative 1' and I'll do it, or just talk to me.")
    if tick["did"]: lines.append("• Already handled on my own: " + "; ".join(tick["did"]))
    if not name: lines.append("• I don't know your name yet - tell me and I'll personalise.")
    lines.append("Just talk to me in plain language. I run this machine.")
    return "\n".join(lines)

def repl(agent):
    print(briefing(agent))
    name = agent.m.data["profile"].get("name") or "you"
    while True:
        try: line = input(f"\n{name}> ").strip()
        except EOFError: print("\nNeurAI: I'll keep running in the background."); break
        except KeyboardInterrupt: continue
        if not line: continue
        if line.lower() in ("quit", "exit"): break
        print("\nNeurAI: " + agent.handle(line)["answer"])

def serve(agent, port=8080):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    ui = (Path(__file__).parent / "ui.html")
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, code, body, ctype="application/json"):
            b = body.encode() if isinstance(body, str) else body
            self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", len(b)); self.end_headers(); self.wfile.write(b)
        def do_GET(self):
            if self.path in ("/","/index.html"):
                self._send(200, ui.read_text() if ui.exists() else "<h1>NeurAI</h1>", "text/html")
            elif self.path == "/api/status": self._send(200, json.dumps(agent._exec({"intent": "status"})))
            elif self.path == "/api/briefing": self._send(200, json.dumps({"briefing": briefing(agent)}))
            elif self.path == "/api/memory": self._send(200, json.dumps(agent.m.data))
            else: self._send(404, "{}")
        def do_POST(self):
            if self.path == "/api/chat":
                n = int(self.headers.get("Content-Length", 0)); body = json.loads(self.rfile.read(n) or b"{}")
                out = agent.handle(body.get("message",""))
                self._send(200, json.dumps({"answer": out["answer"], "intents": out["intents"]}))
            else: self._send(404, "{}")
    print(f"NeurAI web console on http://127.0.0.1:{port}")
    HTTPServer(("127.0.0.1", port), H).serve_forever()

def daemon(agent, interval=None):
    interval = interval or int(os.environ.get("NEURAI_AUTONOMY_INTERVAL", "300"))
    print(f"[NeurAI daemon] autonomy + self-improvement every {interval}s, memory at {agent.m.file}")
    auto = Autonomy(agent)
    while True:
        time.sleep(interval)
        print("[NeurAI daemon] reflect:", agent.m.reflect())
        print("[NeurAI daemon] autonomy:", auto.tick()["summary"])

def agent_worker(sid, task):
    """Child-agent entry point: run one task, write result for the parent."""
    a = Agent()
    out = a.handle(task)
    # result file lives in the PARENT's spawns dir: NEURAI_HOME is spawns/<sid>
    result_file = home_dir().parent / f"{sid}.json"
    meta = {"id": sid, "task": task, "status": "done", "started": now_iso(),
            "answer": out["answer"], "intents": out["intents"]}
    result_file.write_text(json.dumps(meta))
    print(f"[{sid}] {out['answer']}")

if __name__ == "__main__":
    if "--agent-worker" in sys.argv:
        i = sys.argv.index("--agent-worker"); agent_worker(sys.argv[i+1], " ".join(sys.argv[i+2:])); sys.exit(0)
    a = Agent()
    if "--daemon" in sys.argv: daemon(a)
    elif "--serve" in sys.argv:
        p = int(sys.argv[sys.argv.index("--serve")+1]) if len(sys.argv) > sys.argv.index("--serve")+1 else 8080
        serve(a, p)
    elif "--once" in sys.argv: print(a.handle(" ".join(sys.argv[sys.argv.index("--once")+1:]))["answer"])
    elif "--reflect" in sys.argv: print(a.m.reflect())
    else: repl(a)
