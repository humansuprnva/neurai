# Archive assessment and integration decisions

The operator-supplied archive contained a Phase-0 Python trial, two test
variants, a LoRA consolidation script, and a v0.1 PDF specification.

| Archive item | Decision | Reason |
|---|---|---|
| Phase-0 Python trial | Replaced by the smaller isolated kernel in this directory | The source was useful as a behavioral sketch but its hard-coded hemisphere orientations, substring-based refusal detection, and mutable log state were not suitable as an implementation baseline. |
| Test variants | Replaced by focused deterministic tests | The repo-ready variant included a false-positive assertion that checked a separately constructed substrate rather than the CC's substrate. |
| LoRA consolidation script | Deferred; not copied | Training data was not curated or held out, labels embedded assumed hemisphere roles, loss covered prompt and response, and remote command construction needed hardening. |
| v0.1 PDF | Not copied as governing documentation | It predates the accepted roadmap and ADRs and contains authority/topology assumptions that require reconciliation. |

## Compatibility notes

- The v0.1 PDF's two-key executive gate is not a substitute for the accepted
  capability-first authority model in ADR-001.
- ADR-002 defines the current topology and resource-placement/degraded-mode
  decisions; model count and placement remain configuration choices.
- ADR-003 grants bounded procedural disposition authority for explicit
  objections. It does not authorize erasing dissent, waiving invariants, or
  replacing the authorization pipeline.
- The current architecture does not permanently assign H-A and H-B fixed
  cognitive personalities or specializations.
- A prototype's refusal message or action-category prediction is not
  deterministic permission enforcement.

This experiment is intentionally kept outside `neurai/` and
`neurai/governance/`. No production import path depends on it.
