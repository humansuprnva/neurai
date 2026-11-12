# NeurAI OS — Architecture Contract (Phase 0)

Phase-0 scaffolding for NeurAI OS, built 2026-11-04. This directory is the
repository-level foundation prescribed by **PHASE-0** of the governing
roadmap (`roadmap/neurai-roadmap-1.1.0.yaml`, as amended by ADR-001):
*architecture specification, component interfaces, configuration schema,
invariant registry, decision and change record formats, and a test harness.*

## Authority and precedence

- The **roadmap is the contract**. Everything here is an executable,
  machine-checkable restatement of it. If anything in this directory ever
  conflicts with `roadmap/neurai-roadmap-1.1.0.yaml`, the roadmap governs
  and the file here is the defect.
- Roadmap and evaluation originals are **not** duplicated or replaced:
  - Roadmap: `roadmap/neurai-roadmap-1.1.0.yaml` (1.0.0 preserved alongside)
  - Authority decision: `roadmap/decisions/ADR-001-capability-first-authority.yaml`
  - Conformance baseline: `roadmap/evaluation-v2.0.0.yaml` (EVAL-2026-11-02-001)
- Status at creation: v2.0.0 is a functional prototype that does **not**
  meet the rubric release gates (EVAL-2026-11-02-001). PHASE-0 was
  `not_started`; this scaffolding is its deliverable set. Nothing here
  claims any invariant is implemented — the registry records, per
  invariant, how conformance will be checked and in which phase.

## Layout

| Path | Purpose |
|---|---|
| `contract/architecture-contract.yaml` | Executable restatement of the roadmap contract: entity model, components, services, interaction flow, execution modes, memory model, change governance, release gates, phase index. |
| `contract/component-interfaces.yaml` | Interface definitions for HEM-A, HEM-B, CC, and shared services SVC-001..008: provided/consumed interfaces, report formats, and how the v2.0.0 core maps onto them today. |
| `contract/config-schema.yaml` | Configuration schema for the future runtime config, with the rules the test harness enforces. |
| `contract/schemas/*.schema.yaml` | Record formats (roadmap Section 12): requirement, architecture decision, change proposal (Section 8), change record, evaluation record. |
| `contract/invariants/invariants.yaml` | The invariant registry: INV-001..012 with verbatim rules, check method, and the phase that will enforce each. |
| `registry/authority-tiers.yaml` | ADR-001's authority tiers (free / gated_integrity / gated_irreversible) as explicit, inspectable data. **Defined, not yet enforced** — enforcement is Phase-4/Phase-6 work. |
| `records/requirements.yaml` | Requirement tracker (Section 12 format): ENT-001..006, MEM-R01..R08, AG-008..AG-014, plus per-phase deliverable tracking. |
| `records/decisions.yaml` | Index of architecture decisions; full records live in `roadmap/decisions/`. |
| `records/changes.yaml` | Change-record log (empty; format defined by the schemas). |
| `records/evaluations.yaml` | Index of conformance evaluations; full records live in `roadmap/`. |
| `tests/` | Test harness: `run.sh` entry point, contract/record validators, invariant tests. |

## Phase sequence and where the next phase begins

Roadmap order from here (per EVAL-2026-11-02-001's sequencing note):
**Phase 0 (this) → CC / permission layer (Phase 4 core, with the Phase-6
authority registry) → hemispheres (Phase 3)** — not more features on the
current single-component core.

The Corpus Callosum governance phase starts from three clean entry points:

1. `contract/component-interfaces.yaml` → component `CC`: its
   responsibilities, constraints, provided interfaces (`coordinate`,
   `enforce_authority`, `record_decision`, `reconcile`, `commit_change`,
   `verify_self_model`), and the `decision_record` it must emit.
2. `registry/authority-tiers.yaml`: the tier data the CC's deterministic
   enforcement core will load. Phase 4 turns this file from *defined* to
   *enforced* and factors the integrity machinery (audit log store,
   backup store, rollback mechanism, registry enforcement code) into a
   separately protected component, per ADR-001's recorded follow-up.
3. `contract/schemas/` + `records/`: the decision-log entries, change
   proposals, and change records the CC must produce are already in their
   final formats, so governance work lands in a prepared structure.

## Explicit non-goals of this scaffolding

- **No integrity-volume writing.** The installed system's integrity
  volume (`NEURAI_INTEGRITY`) is referenced only as a future Phase-4
  home for the integrity machinery. Nothing here reads or writes it.
- **No changes to the shipped system.** The v2.0.0 core (`neurai/neurai.py`),
  the live ISO, and the installed disk images in `dist/` are untouched.
- **No enforcement claims.** Permission enforcement, the decision log,
  and the authority registry's runtime behavior are later phases; this
  scaffolding defines their contracts and data, not their code.

## Running the checks

```sh
architecture/tests/run.sh
```

Exit code 0 = the contract, registry, and records are internally
consistent, conform to the schemas, and remain consistent with the
roadmap text and the v2.0.0 core's recorded state.
