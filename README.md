# NeurAI Integrated OS v2.0.0 — x86_64

A bootable, Linux-based operating system whose foundation is a **fully
integrated, local AI**. No cloud, no account, no API key. v2 rebuilds the
interaction around a real language model, direct self-modification, and
autonomous initiative: natural language first, the AI pursues goals, and
it can rewrite its own code and system files.

**Target hardware:** Lenovo V14 ADA (x86_64, 8 GB RAM, SSD + additional
500 GB SSD installed 2026-11-02) — a **dedicated NeurAI host and a clean
slate**: nothing on the machine needs preserving, and both drives may be
formatted and partitioned as most practical. The storage/host plan
(two-drive layout with a physically separate integrity partition,
implementing ADR-001's gated tier at the disk level) is recorded in
`roadmap/deployment-host.md`.

## Project status and governing architecture

This project is governed by an architectural contract:
**`roadmap/neurai-roadmap-1.1.0.yaml`** — the NeurAI OS Architectural
Roadmap and Evaluation Rubric (v1.0.0 operator-supplied 2026-11-02;
v1.1.0 amends it via ADR-001; the 1.0.0 original is preserved alongside).
The roadmap defines the intended architecture — one unified entity with
two independent cognitive hemispheres and a deterministic Corpus
Callosum governance layer — plus 12 foundational invariants, a phased
development plan (Phases 0–9), and a conformance rubric (RUB-001..010).

**Honest status of this release:** v2.0.0 was evaluated against the
rubric on 2026-11-02 (`roadmap/evaluation-v2.0.0.yaml`,
EVAL-2026-11-02-001). It scores 3 on interaction coherence and 2 or
below on everything else (cognitive independence 0, CC governance 1,
security & authority 1) and **does not meet the rubric's release
gates**. It is a working prototype: the conversation interface, agency
engine, and tool layer are real and tested, while the roadmap's
foundational pieces — hemispheres, the CC governance layer, SQLite
provenance memory — are not yet built. Development follows the
roadmap's sequencing from here: Phase 0 contract scaffolding, then the
CC/permission layer, then hemispheres.

## Authority model (ADR-001)

Self-modification authority is tiered by **reversibility**, per
`roadmap/decisions/ADR-001-capability-first-authority.yaml`:

- **Free tier** — anything fully captured by versioned state (own source,
  config, governance documents) may be committed autonomously, controlled
  by staging, validation, backups, change records, and post-hoc review.
- **Gated: integrity** — the audit log, backup store, rollback mechanism,
  and authority-enforcement code require strict review and operator
  authorization, because edits there can destroy reversibility itself.
- **Gated: irreversible** — effects outside versioned state (destroying
  unversioned data, external transmission of private data, spending)
  require operator authorization.

*Implementation note:* the shipped v2 core implements the free tier's
mechanics (staged, syntax-checked, backed-up, logged patches with
rollback) but **not yet** the enforcement layer — there is no authority
registry or append-only decision log yet, and the single-file core
cannot physically protect its own rollback code. That separation is
roadmap Phase 4 work; the ADR records it as a known gap, not a claim.

## What's in v2
- **A real language-model brain, bundled in the ISO.** Qwen2.5-1.5B-Instruct
  (GGUF Q4_K_M, ~1.1 GB) served by llama.cpp, fully offline. You talk in
  plain language; NeurAI parses intent and drives its tools in a multi-step
  loop (max 4 steps), narrating what it did ("— actions: …"). If a local
  Ollama server is present it can be used instead (`NEURAI_OLLAMA_MODEL`).
  With no brain available (`NEURAI_NO_LLM=1`), a deterministic planner
  takes over — capability degrades, nothing breaks. Guards: repeat/unknown
  tool calls are rejected, and garbled model output is replaced by an answer
  synthesized from the real tool results, never invented numbers.
- **Conversation-first interface.** On boot NeurAI speaks first: a briefing
  with brain status, your open tasks and goals, anything it already did on
  its own, and initiatives waiting for your approval. The prompt is yours
  (`YourName> ` once it knows you). No command syntax required — commands
  still work, and `run <cmd>` / `!<cmd>` always bypass the model.
- **Direct write access to itself and the system.** `self_read`,
  `self_patch` (exact-anchor patch of its own source — staged, syntax-checked
  with `py_compile`, backed up, logged; `self_rollback` undoes the last
  change) and `sys_read` / `sys_write` for any file on the system (original
  backed up first). Ask in plain language: "patch your code so …".
- **Autonomy engine.** A heartbeat (daemon, every `NEURAI_AUTONOMY_INTERVAL`
  seconds, default 300) observes disk, tasks, goals, spawns and episodes,
  then acts in two tiers: safe maintenance on its own (reflection runs,
  stale-spawn cleanup), and an **initiative queue** for everything else —
  disk-pressure cleanup, tasks derived from your goals, spawns to work on
  them, saving finished spawn results as notes. Review with `initiatives`,
  run one with `approve initiative N` or `take initiative`.
  (Model-suggested initiatives are opt-in: `NEURAI_AUTONOMY_LLM=1`.)
- **Goals.** `my goal is …` / `goal: …` records a standing goal; the
  autonomy engine works toward goals by proposing tasks and spawns.
- **Learned intents.** When a phrasing fails and a rephrase succeeds, the
  mapping is learned — the same way of asking works next time.
- Everything from v1 remains: agentic `spawn` / `parallel:` child agents,
  persistent personalized memory, recurring self-improvement (episode log →
  distilled skills), tasks, notes, full shell/file/system tools, and the
  localhost web console.

## Honest limits (v2.0.0)
- A 1.5B brain is a small brain: clear system requests ("how much disk is
  free?") work reliably; ambiguous ones occasionally pick a near-miss tool
  ("remember" instead of "task_add"). The planner + learned intents cover
  common phrasings deterministically, and every tool result shown is real.
- Architecturally, v2 is a single cognitive component with no governance
  layer — see the status section above and the evaluation record for the
  full gap list against the roadmap.
- RAM: the ISO carries the model, so the live system wants **4 GB**
  (2 GB works with `NEURAI_NO_LLM=1`, planner-only); the target machine's
  8 GB is comfortable, including the roadmap's sequential
  (one-model-at-a-time) execution model. CPU inference on old hardware
  takes ~10–30 s per model step — NeurAI is deliberate, not instant.

## Boot it
1. Write `neurai-os-2.0.0-amd64.iso` (~1.1 GB) to USB: Rufus / Balena Etcher,
   or `sudo dd if=neurai-os-2.0.0-amd64.iso of=/dev/sdX bs=4M status=progress conv=fsync`
2. Boot the laptop from USB (F12 / Esc / F2 boot menu).
3. NeurAI briefs you, then just talk: "how much memory does this machine
   have?", "my goal is to organize my photos", "what have you been doing?",
   `initiatives`, `help`.

## Install to hard disk (erases the disk)
**Use the disk images** — see `INSTALL-DISK.md`. The installed system
ships as two images (`neurai-system-2.0.0.img`, `neurai-data-2.0.0.img`)
implementing the two-drive partition plan (ESP + root + integrity +
swap; data volume with the model and NeurAI's persistent home). Boot
the live USB, `dd` the images onto the two SSDs, reboot. Verified end
to end in QEMU (`tests/disk_test.py`: boot, teach, power off, reboot,
fact persists). The old `install-to-disk.sh` does not produce a
bootable system and is kept only as a historical sketch — do not use it.
Note: the current installer uses a single disk with a simple layout; the
two-drive dedicated-host plan in `roadmap/deployment-host.md` (separate
integrity partition, persistent data volume on the 500 GB SSD) is the
target layout and requires installer + init updates (roadmap Phase 1/7).

### Universal installer (new, 2026-11-07)
**See `INSTALL-UNIVERSAL.md`.** The installer ISO
(`neurai-installer-1.0.0-amd64.iso`, built by `build_installer.py`)
carries the OS as payload *files* plus `neurai-install`, a program
that partitions and formats at install time — one stick installs a
single-drive machine, a split two-drive machine, or each member of
the fleet with a role seed (interface / inference / memory).
QEMU-verified end to end (`tests/installer_test.py`, all scenarios
pass). The two-image route below remains available as recovery media.

## Run without the ISO (any machine with Python 3)
```
python3 neurai/neurai.py                 # REPL (briefing + conversation)
python3 neurai/neurai.py --serve 8080    # web console on localhost
python3 neurai/neurai.py --daemon        # autonomy heartbeat in background
NEURAI_MODEL_PATH=llm/model.gguf NEURAI_LLAMA_BIN=llm/llama-b12032/llama-completion python3 neurai/neurai.py
python3 tests/test_neurai.py             # 14 unit/integration tests
NEURAI_MODEL_PATH=llm/model.gguf NEURAI_LLAMA_BIN=llm/llama-b12032/llama-completion python3 tests/real_llm_test.py  # real-model end-to-end
```

## Rebuild the ISO
`python3 build_iso.py` stages the rootfs (Debian kernel 6.1 .debs, BusyBox,
Python, llama.cpp + `llm/model.gguf`) and initramfs; `build-grub-iso.sh`
wraps it in the dual BIOS+UEFI GRUB image.

## Layout
- `neurai/neurai.py` — the entire AI core (single file, Python stdlib only)
- `llm/` — llama.cpp binaries + `model.gguf` (the brain)
- `neurai/ui.html` — web console UI
- `rootfs/init` — PID 1: mounts, network, daemon, web console, AI shell
- `tests/` — unit/integration suite, real-LLM test, QEMU boot test
- `roadmap/` — the governing architecture contract and its records:
  `neurai-roadmap-1.1.0.yaml` (current), `neurai-roadmap-1.0.0.yaml`
  (preserved original), `evaluation-v2.0.0.yaml` (conformance evaluation),
  `decisions/ADR-001-capability-first-authority.yaml`
