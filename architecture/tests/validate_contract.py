#!/usr/bin/env python3
"""Phase-0 harness: contract consistency checks.

Verifies that the architecture contract is internally consistent and
remains consistent with the governing roadmap text (identifiers,
component boundaries, service/mode/change-class inventories). The
roadmap is parsed as text, not YAML: its mixed hand formatting is not
machine YAML, and identifiers are its stable surface.

Exit 0 = all checks pass. Prints one line per check group.
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]          # neurai-os/
ARCH = ROOT / "architecture"
ROADMAP = ROOT / "roadmap" / "neurai-roadmap-1.1.0.yaml"

failures = []


def check(name, ok, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def load(rel):
    with open(ARCH / rel, encoding="utf-8") as f:
        return yaml.safe_load(f)


def roadmap_ids(pattern):
    text = ROADMAP.read_text(encoding="utf-8")
    return set(re.findall(pattern, text))


def main():
    print("validate_contract: parsing all scaffolding YAML")
    parsed = {}
    for p in sorted(ARCH.rglob("*.yaml")):
        try:
            parsed[p.relative_to(ARCH).as_posix()] = yaml.safe_load(
                p.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            check(f"yaml parses: {p.relative_to(ARCH)}", False, str(e).splitlines()[0])
    check("all architecture YAML parses", not failures)
    if failures:
        print("validate_contract: FAIL (unparseable input; dependent checks skipped)")
        return 1

    full_contract = parsed.get("contract/architecture-contract.yaml", {})
    interfaces = parsed.get("contract/component-interfaces.yaml", {})

    print("validate_contract: identifier cross-checks against the roadmap")
    rm_inv = roadmap_ids(r"\bINV-\d{3}\b")
    check("roadmap defines INV-001..012",
          rm_inv == {f"INV-{i:03d}" for i in range(1, 13)}, str(sorted(rm_inv)))
    rm_svc = roadmap_ids(r"\bSVC-\d{3}\b")
    contract_svc = {s["id"] for s in full_contract["shared_services"]}
    check("contract services == roadmap SVC-001..008",
          contract_svc == rm_svc == {f"SVC-{i:03d}" for i in range(1, 9)},
          f"contract={sorted(contract_svc)} roadmap={sorted(rm_svc)}")
    iface_svc = {s["id"] for s in interfaces.get("services", [])}
    check("interface services == contract services", iface_svc == contract_svc,
          f"interfaces={sorted(iface_svc)}")
    rm_modes = roadmap_ids(r"\bMODE-\d{3}\b")
    contract_modes = {m["id"] for m in full_contract["cognition"]["execution_modes"]}
    check("contract modes == roadmap MODE-001..004",
          contract_modes == rm_modes == {f"MODE-{i:03d}" for i in range(1, 5)})
    rm_chg = roadmap_ids(r"\bCHG-\d{3}\b")
    contract_chg = {c["id"] for c in full_contract["change_governance"]["classes"]}
    check("contract change classes == roadmap CHG-001..007",
          contract_chg == rm_chg == {f"CHG-{i:03d}" for i in range(1, 8)})
    rm_mem = roadmap_ids(r"\bMEM-\d{3}\b")
    contract_mem = {c["id"] for c in full_contract["memory"]["categories"]}
    check("contract memory categories == roadmap MEM-001..007",
          contract_mem == rm_mem == {f"MEM-{i:03d}" for i in range(1, 8)})
    rm_rub = roadmap_ids(r"\bRUB-\d{3}\b")
    check("contract rubric dimensions == roadmap RUB-001..010",
          set(full_contract["evaluation"]["dimensions"]) == rm_rub
          == {f"RUB-{i:03d}" for i in range(1, 11)})

    print("validate_contract: structure and boundaries")
    comps = full_contract["components"]
    check("exactly three constituent components (HEM-A, HEM-B, CC)",
          [c["id"] for c in comps] == ["HEM-A", "HEM-B", "CC"],
          str([c["id"] for c in comps]))
    check("entity unity is integrated_entity (INV-001/INV-011)",
          full_contract["entity_model"]["unity"] == "integrated_entity")
    check("CC implementation is deterministic-core-first, reasoning advisory",
          comps[2]["implementation"]["authority_model"]
          == "deterministic_core_enforces_permissions_and_commits"
          and comps[2]["implementation"]["reasoning_engine_role"] == "advisory")
    iface_comps = {c["id"] for c in interfaces.get("components", [])}
    check("interfaces define exactly HEM-A, HEM-B, CC",
          iface_comps == {"HEM-A", "HEM-B", "CC"}, str(sorted(iface_comps)))
    rr_fields = set(interfaces["shared_record_formats"]["reasoning_report"]["fields"])
    interp_fields = set(full_contract["cognition"]["interpretation_record_fields"])
    check("reasoning_report carries every Section 4 interpretation field",
          interp_fields <= rr_fields,
          f"missing: {sorted(interp_fields - rr_fields)}")
    dr_fields = set(interfaces["shared_record_formats"]["decision_record"]["fields"])
    check("decision_record carries the ADR-001 governance fields",
          {"prior_text", "new_text", "authority_tier", "rationale"} <= dr_fields)
    modes_enum = set(interfaces["shared_record_formats"]
                     ["integrated_task_context"]["fields"]
                     ["selected_execution_mode"]["enum"])
    check("integrated_task_context mode enum == contract modes",
          modes_enum == contract_modes)
    phase_ids = [p["id"] for p in full_contract["phases"]["index"]]
    check("phase index is PHASE-0..PHASE-9 in order",
          phase_ids == [f"PHASE-{i}" for i in range(10)], str(phase_ids))
    deps_ok = all(
        all(d in phase_ids[:i] or d in phase_ids for d in p["depends_on"])
        and all(phase_ids.index(d) < i for d in p["depends_on"])
        for i, p in enumerate(full_contract["phases"]["index"]))
    check("phase dependencies only point backwards", deps_ok)
    check("contract authority model is ADR-001 tiered_by_reversibility",
          full_contract["authority"]["tiers"]
          == ["free", "gated_integrity", "gated_irreversible"])

    print(f"validate_contract: {'PASS' if not failures else 'FAIL'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
