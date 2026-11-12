#!/usr/bin/env python3
"""Phase-0 harness: invariant registry tests.

Checks the registry itself (completeness, stable identifiers, verbatim
rules cross-checked against the roadmap), the ADR-001 authority data
(INV-007), structural invariants against the contract (INV-001/011),
and the declared static markers against the v2.0.0 core source for the
invariants that are partially present today (INV-003/004/006/008) or
honestly absent (INV-007 enforcement).

Nothing here claims runtime enforcement: the registry's
`defined_not_enforced` states are asserted as recorded, not as passed
behavior.
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ARCH = ROOT / "architecture"
ROADMAP_TEXT = (ROOT / "roadmap" / "neurai-roadmap-1.1.0.yaml").read_text(encoding="utf-8")
CORE = (ROOT / "neurai" / "neurai.py").read_text(encoding="utf-8")

failures = []
ALLOWED_METHODS = {"contract_structure", "static_core", "registry_integrity",
                   "evaluation_review", "deferred_runtime"}


def check(name, ok, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def norm(s):
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("“", '"').replace("”", '"')).strip()


def main():
    with open(ARCH / "contract" / "invariants" / "invariants.yaml", encoding="utf-8") as f:
        registry = yaml.safe_load(f)
    invs = registry["invariants"]
    roadmap_norm = norm(ROADMAP_TEXT)

    print("test_invariants: registry completeness")
    ids = [i["id"] for i in invs]
    check("registry holds exactly INV-001..012 with stable unique ids",
          ids == [f"INV-{n:03d}" for n in range(1, 13)], str(ids))
    check("registry header count matches", registry["registry"]["count"] == 12)
    for inv in invs:
        check(f"{inv['id']}: rule text is verbatim from the roadmap",
              norm(inv["rule"]) in roadmap_norm)
        c = inv["check"]
        check(f"{inv['id']}: check method declared and phase named",
              c["method"] in ALLOWED_METHODS
              and re.fullmatch(r"PHASE-\d", c["enforcing_phase"]) is not None
              and len(c.get("detail", "")) > 40)
        check(f"{inv['id']}: conformance status and evaluation reference recorded",
              bool(inv.get("conformance_status")) and bool(inv.get("evaluation_reference")))

    print("test_invariants: structural invariants (INV-001, INV-011)")
    with open(ARCH / "contract" / "architecture-contract.yaml", encoding="utf-8") as f:
        contract = yaml.safe_load(f)
    check("contract: one integrated entity, three constituents, one interface",
          contract["entity_model"]["unity"] == "integrated_entity"
          and len(contract["entity_model"]["constituent_components"]) == 3
          and "The user interacts with one coherent NeurAI interface."
          in contract["interaction_flow"]["rules"])

    print("test_invariants: INV-007 authority data (ADR-001)")
    inv7 = next(i for i in invs if i["id"] == "INV-007")
    check("INV-007 carries the ADR-001 interpretation (silent vs loud expansion)",
          inv7.get("interpretation", {}).get("decided_by", "").startswith("ADR-001"))
    with open(ARCH / "registry" / "authority-tiers.yaml", encoding="utf-8") as f:
        registry_data = yaml.safe_load(f)
    tiers = {t["tier"]: t for t in registry_data["tiers"]}
    check("tiers are exactly free / gated_integrity / gated_irreversible",
          set(tiers) == {"free", "gated_integrity", "gated_irreversible"})
    free_reqs = set(tiers["free"]["requirements"])
    check("free tier carries all four ADR-001 controls",
          {"staged_validation_before_commit", "backup_or_version_snapshot_before_commit",
           "change_record_created", "post_hoc_review_available"} <= free_reqs,
          str(sorted(free_reqs)))
    gi_scope = " ".join(tiers["gated_integrity"]["scope_includes"])
    check("gated_integrity scope enumerates the integrity machinery",
          all(k in gi_scope for k in ["audit", "backup", "rollback", "enforcement", "decision_log"]),
          gi_scope)
    check("registry honestly records enforcement as not yet existing",
          registry_data["registry"]["enforcement_status"] == "defined_not_enforced")

    print("test_invariants: static core markers (partial presence / honest absence)")
    for inv in invs:
        markers = inv["check"].get("static_markers")
        if not markers:
            continue
        for m in markers.get("present", []):
            check(f"{inv['id']}: core marker present: {m!r}", m in CORE)
        for m in markers.get("absent", []):
            check(f"{inv['id']}: core marker absent (not yet implemented): {m!r}",
                  m not in CORE)

    print(f"test_invariants: {'PASS' if not failures else 'FAIL'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
