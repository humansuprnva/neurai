#!/usr/bin/env python3
"""Phase-0 harness: record and configuration validation.

Validates the records in architecture/records/ against the Section 12
schemas in architecture/contract/schemas/, and configurations against
architecture/contract/config-schema.yaml. Fixtures prove the Phase-0
acceptance criterion: a valid config passes and malformed config/records
are detected and rejected.

Exit 0 = all checks pass (including that malformed fixtures FAIL).
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ARCH = ROOT / "architecture"
TESTS = Path(__file__).resolve().parent

failures = []


def check(name, ok, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "list": lambda v: isinstance(v, list),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}


def validate_record(record, schema):
    """Return a list of problems; empty list means valid."""
    problems = []
    fields = schema["fields"]
    for name, spec in fields.items():
        if spec.get("required") and name not in record:
            problems.append(f"missing required field '{name}'")
    for name, value in record.items():
        spec = fields.get(name)
        if spec is None:
            continue  # extra fields are allowed (indexes carry summaries)
        t = spec.get("type")
        if t and not TYPE_CHECKS[t](value):
            problems.append(f"field '{name}' should be {t}, got {type(value).__name__}")
            continue
        if "enum" in spec and value not in spec["enum"]:
            problems.append(f"field '{name}' value {value!r} not in enum {spec['enum']}")
        if "pattern" in spec and isinstance(value, str) \
                and not re.match(spec["pattern"], value):
            problems.append(f"field '{name}' value {value!r} fails pattern {spec['pattern']}")
        if "min_items" in spec and isinstance(value, list) \
                and len(value) < spec["min_items"]:
            problems.append(f"field '{name}' has fewer than {spec['min_items']} items")
    return problems


def validate_config(config, schema_doc):
    """Return a list of problems applying the config schema's rules."""
    problems = []
    if not isinstance(config, dict):
        return ["configuration is not a mapping"]
    schema = schema_doc["schema"]
    fields = schema_doc["fields"]
    for section in schema_doc["required_top_level"]:
        if section not in config:
            problems.append(f"missing required top-level section '{section}'")
    for section, spec in fields.items():
        if section not in config:
            continue
        _validate_config_fields(config[section], spec, section, problems)
    mem = config.get("memory", {})
    if mem.get("transactions") is not True:
        problems.append("memory.transactions must be true (contract rule)")
    rt = config.get("runtime", {})
    if rt.get("degrade_under_pressure") is not True:
        problems.append("runtime.degrade_under_pressure must be true (contract rule)")
    models = config.get("models", {})
    if models.get("allow_network_models") is not False:
        problems.append("models.allow_network_models must be false in the foundational configuration")
    return problems


def _validate_config_fields(value, spec, path, problems):
    if spec.get("type") == "object" and "fields" in spec:
        if not isinstance(value, dict):
            problems.append(f"'{path}' should be an object")
            return
        for name, fspec in spec["fields"].items():
            if fspec.get("required") and name not in value:
                problems.append(f"missing required field '{path}.{name}'")
            if name in value:
                _validate_config_fields(value[name], fspec, f"{path}.{name}", problems)
        return
    if "enum" in spec and value not in spec["enum"]:
        problems.append(f"'{path}' value {value!r} not in enum {spec['enum']}")


def main():
    schemas = {
        name: load(ARCH / "contract" / "schemas" / f"{name}.schema.yaml")
        for name in ["requirement", "decision-record", "change-record",
                     "change-proposal", "evaluation-record"]
    }

    print("validate_records: tracker records against Section 12 schemas")
    reqs = load(ARCH / "records" / "requirements.yaml")["requirements"]
    bad = [(r.get("id", "?"), validate_record(r, schemas["requirement"]))
           for r in reqs]
    bad = [(i, p) for i, p in bad if p]
    check(f"all {len(reqs)} requirements validate", not bad, str(bad[:2]))
    ids = [r["id"] for r in reqs]
    check("requirement ids are unique", len(ids) == len(set(ids)))
    unverified = [r["id"] for r in reqs
                  if r["status"] == "verified" and not r.get("evidence")]
    check("no requirement is 'verified' without evidence (operating rule)",
          not unverified, str(unverified))
    mandatory_split = any(r["mandatory"] for r in reqs) and \
        all("mandatory" in r for r in reqs)
    check("mandatory vs proposed is distinguishable on every requirement",
          mandatory_split)

    decisions = load(ARCH / "records" / "decisions.yaml")["decisions"]
    bad = [(d.get("decision_id", "?"), validate_record(d, schemas["decision-record"]))
           for d in decisions]
    bad = [(i, p) for i, p in bad if p]
    check(f"all {len(decisions)} decision index entries validate", not bad, str(bad[:2]))
    missing_files = [d["decision_id"] for d in decisions
                     if not (ROOT / d["record_path"]).exists()]
    check("every indexed decision record file exists", not missing_files,
          str(missing_files))

    evals = load(ARCH / "records" / "evaluations.yaml")["evaluations"]
    bad = [(e.get("evaluation_id", "?"), validate_record(e, schemas["evaluation-record"]))
           for e in evals]
    bad = [(i, p) for i, p in bad if p]
    check(f"all {len(evals)} evaluation index entries validate", not bad, str(bad[:2]))
    missing_files = [e["evaluation_id"] for e in evals
                     if not (ROOT / e["record_path"]).exists()]
    check("every indexed evaluation record file exists", not missing_files,
          str(missing_files))
    for e in evals:
        scores = e["dimension_scores"]
        check(f"{e['evaluation_id']}: scores cover RUB-001..010 within 0..4",
              set(scores) == {f"RUB-{i:03d}" for i in range(1, 11)}
              and all(isinstance(v, int) and 0 <= v <= 4 for v in scores.values()))

    changes = load(ARCH / "records" / "changes.yaml")["records"]
    bad = [(c.get("change_id", "?"), validate_record(c, schemas["change-record"]))
           for c in changes]
    bad = [(i, p) for i, p in bad if p]
    check(f"change log validates ({len(changes)} records; empty is valid at Phase 0)",
          not bad, str(bad[:2]))

    print("validate_records: configuration schema and fixtures")
    config_schema = load(ARCH / "contract" / "config-schema.yaml")
    good = load(TESTS / "fixtures" / "valid-config.yaml")
    problems = validate_config(good, config_schema)
    check("valid fixture configuration passes", not problems, str(problems))
    bad_cfg = load(TESTS / "fixtures" / "malformed-config.yaml")
    problems = validate_config(bad_cfg, config_schema)
    check("malformed fixture configuration is rejected", len(problems) >= 3,
          f"only detected: {problems}")
    bad_rec = load(TESTS / "fixtures" / "malformed-change-record.yaml")
    problems = validate_record(bad_rec, schemas["change-record"])
    check("malformed fixture change record is rejected", len(problems) >= 2,
          f"only detected: {problems}")

    print(f"validate_records: {'PASS' if not failures else 'FAIL'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
