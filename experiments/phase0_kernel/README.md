# Phase-0 cognitive-kernel experiment

This directory contains a small, isolated experiment derived from the ideas in the
operator-supplied NeurAI archive. It is **not** the production NeurAI runtime, does
not replace the roadmap, and is not wired into `neurai/neurai.py`.

## What it demonstrates

- Two independently configured local Ollama interpreters can receive the same
  original input and the same shared-memory snapshot.
- Their interpretations and proposed next steps are retained separately.
- Divergence is represented as an open `Disagreement`; confidence is recorded
  as descriptive metadata and is never used to select a winner.
- Events can be written to and reloaded from a local JSONL file.
- The Ollama adapter has no cloud/API dependency; it targets a local endpoint.

Run from the repository root:

```sh
python3 -m experiments.phase0_kernel.kernel --model-a llama3.2:1b --model-b qwen2.5:1.5b
python3 -m unittest discover -s tests -p 'test_phase0_kernel.py' -v
```

Both Ollama models must already be available locally. The test suite uses
deterministic in-process interpreters and does not require Ollama.

## Explicit boundaries

This experiment has no tools, executor, shell access, self-modification path,
permission registry, authorization pipeline, or executive-action capability.
It cannot authorize or perform external actions. It is a cognition and record
shape experiment only.

The JSONL writer uses unique event IDs, flushes and fsyncs each append, and
exposes no update/delete API. This is an append-only application interface,
**not** a tamper-evident audit log: it has no hash chain, external anchor,
multi-process locking, protected integrity volume, or startup authorization
halt. It must not be used as the production governance/audit store.

The prototype deliberately does not assign permanent rational/creative,
cautious/exploratory, or authority roles to H-A and H-B. Model identity and
configuration may differ; specialization remains open to development. Using
the same model and configuration for both interpreters does not establish
independent cognition.

The disagreement detector currently compares the two returned interpretation
and next-step strings exactly. It is a deliberately simple experimental
trigger, not a semantic classifier or a decision policy. It does not resolve
ordinary divergence or implement ADR-003 objection disposition.

## Relationship to governing architecture

The governing contract is
`roadmap/neurai-roadmap-1.3.0.yaml`, as amended by ADR-001, ADR-002, and
ADR-003. The Phase-4 implementation specification and its 1.2.0 addendum
remain authoritative for the governance implementation sequence and
acceptance criteria. This experiment does not claim conformance to them.

The uploaded PDF, *NeurAI — Foundational Architectural Specification (v0.1)*,
is not copied into this repository as a governing document. It is an earlier
design artifact. Where it differs from the accepted roadmap and ADRs, the
current roadmap and accepted decisions govern.

## Deliberately not carried forward from the supplied archive

The original trial's hard-coded "structural/precautionary" versus
"exploratory/relational" orientations were removed because the current
architecture leaves functional specialization provisional. Its refusal
string-matching and model-proposed action categories were not retained as
security controls. The earlier mutable disagreement map and count-derived
event IDs were replaced by immutable record values, copied event access,
unique IDs, and durable writes.

The supplied LoRA consolidation script is not included. It trained on
unreviewed generated examples, applied loss to prompt and response tokens
together, had no holdout/evaluation gate, and interpolated remote shell
commands. Training-data curation, adapter evaluation, and safe remote
execution need their own design and tests before they belong in the repository.

## Status

Experimental scaffolding only. No production architecture requirement is
marked implemented by the presence of this directory.
