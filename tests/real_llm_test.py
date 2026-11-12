"""Real-model smoke test: the bundled GGUF brain must parse natural
language, call a real tool, and answer from its result.
Run: NEURAI_MODEL_PATH=../llm/model.gguf NEURAI_LLAMA_BIN=../llm/llama-b12032/llama-completion python3 real_llm_test.py
"""
import sys, os, tempfile, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "neurai"))
from neurai import Agent, Memory, LLMBrain

b = LLMBrain()
print("engine:", b.engine(), "| model:", b.model)
assert b.available(), "no local model found - set NEURAI_MODEL_PATH / NEURAI_LLAMA_BIN"

a = Agent(memory=Memory(base=tempfile.mkdtemp()))
t0 = time.time()
out = a.handle("Hi NeurAI, can you check how much free disk space this computer has?")
print("elapsed: %.1fs" % (time.time() - t0))
print("intents:", out["intents"])
print("answer:", out["answer"][:400])
assert out["intents"] and out["intents"][0] == "llm", "must go through the LLM loop"
assert "sys_disk" in out["intents"], f"model must actually call sys_disk, got {out['intents']}"
import re
assert re.search(r"\d+\.\d+\s*GB", out["answer"]), f"answer must contain real figures from the tool, got: {out['answer'][:200]}"
print("REAL LLM TEST PASSED")
