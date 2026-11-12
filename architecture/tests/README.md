# Phase-0 Test Harness

Run everything:

```sh
architecture/tests/run.sh
```

## What is checked

- `validate_contract.py` — every YAML file in `architecture/` parses;
  component, service, mode, change-class, memory-category, and rubric
  identifiers in the contract match the governing roadmap text exactly;
  the component set is exactly HEM-A / HEM-B / CC; the hemispheres'
  `reasoning_report` carries every Section 4 interpretation field; the
  CC `decision_record` carries the ADR-001 governance fields; phase
  dependencies only point backwards.
- `validate_records.py` — every requirement, decision index entry, and
  evaluation index entry in `records/` validates against its Section 12
  schema; indexed record files exist; no requirement is marked
  `verified` without evidence; the valid fixture configuration passes
  the config schema while the malformed configuration and malformed
  change-record fixtures are rejected (Phase-0 acceptance: automated
  checks detect malformed configuration and records).
- `test_invariants.py` — the invariant registry holds exactly
  INV-001..012, each rule verbatim from the roadmap (whitespace/quote
  normalized), each with a declared check method and enforcing phase;
  INV-007's ADR-001 tier data is complete (four free-tier controls,
  integrity machinery enumerated, enforcement honestly recorded as not
  yet existing); static markers confirm the partial presence the
  evaluation recorded in the v2.0.0 core (backups, self_changes, actions
  footer, rollback) and the honest absence of an authority registry or
  decision log in that core.

## What is deliberately not checked

Runtime behavior. There is no CC, no hemispheres, and no enforcement to
test yet — claiming otherwise would violate INV-006. Invariants whose
check method is `deferred_runtime` or `evaluation_review` are asserted
as *registered with an honest status*, and become behavioral tests in
their enforcing phases (3–7). The harness grows with the phases; this
structure is the starting point the Corpus Callosum phase builds on.
