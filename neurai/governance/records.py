"""NeurAI Phase 4, step 4.1 — governance data structures.

Implements PHASE4-SPEC-001 v1.0.0, §4, exactly as specified.  Stdlib only.

Scope of this module (4.1 only):
  * the common record envelope and provenance (§4.1, rules P-1 and P-2),
  * InterpretationRecord (§4.2), EvidenceRef and ActionPayload (§4.3),
  * DeliberationRecord (§4.4), InvariantCheckRecord, AuthorizationRecord
    and OperatorAuthorizationRecord (§4.5), OperatorResolutionRecord
    (§7.5, the operator's divergence escape hatch), DecisionRecord (§4.6),
    ExecutionRecord and VerificationRecord (§4.7).

What this module does NOT do:
  * It makes no decisions.  In particular it computes no evidence
    aggregate of any kind: there is no function here that counts evidence,
    sums relevance/reliability, compares confidences, or picks a "winner".
    EvidenceRefs are preserved verbatim (D-02); a count is not strength.
  * It does not classify actions.  ``classification_hint`` and
    ``reversibility_claim`` on ActionPayload are untrusted, recorded
    opinions of the proposer; the tier is derived from policy in step 4.6,
    never from a hint (D-03).
  * It does not enforce anything.  Validation of a record dict against the
    closed field sets in SCHEMA_FIELDS lives in ``serialization.py`` (§5);
    the dataclass constructors below do only construction-time fail-closed
    checks (enum membership, numeric ranges, provenance consistency) so a
    plainly malformed object cannot even be built.
  * It does not touch the v2 core, storage, the network, or the clock as a
    decision input.  ``created_at`` is evidence only (§0); record ids and
    timestamps are injectable (pass ``record_id`` / ``created_at``
    explicitly) so tests are deterministic (M-3).

Ambiguities in §4 that had to be resolved (see the module docstring of
each affected record, and the final implementation report):
  A1. Every record carries both the §4.1 envelope (``record_id``) and, for
      Decision/Execution/Verification/InvariantCheck records, a domain id
      (``decision_id`` / ``execution_id`` / ``verification_id`` /
      ``check_id``).  The domain id defaults to the envelope ``record_id``
      when not supplied, so the two never diverge silently.
  A2. DecisionRecord ``timestamp`` (Phase-0 field) is set equal to the
      envelope ``created_at``; there is one clock value, not two.
  A3. DecisionRecord ``authority_tier`` may be null until an
      AuthorizationRecord exists; a deliberating decision has no tier yet,
      and inventing one would be a claim without a source.
  A4. ``deferral_stage`` is not a §4.6 field, although §4.6 says a DEFERRED
      decision records which stage it deferred from.  No field was added
      (closed field set); the stage is carried in ``deferral_reason`` as a
      ``"<stage>: <reason>"`` string until a later step specifies a field.
  A5. Provenance origin/component consistency is enforced: hemisphere_
      original comes from HEM-A/HEM-B, cc_derived from CC, executor_report
      from EXECUTOR, operator_record from OPERATOR.  For Interpretation-
      Records the component must equal ``hemisphere_id``.
  A6. OperatorAuthorizationRecord ``operator`` must be "Levi Nord", as
      written in §4.5.
  A7. VerificationRecord check ``result`` accepts a bool or the strings
      "pass"/"fail"; §4.7 does not enumerate its values.
  A8. ``schema_version`` acceptance is §5.4: major must be 1, minor must
      be 0 (current is 1.0.0); any patch is accepted.  A newer minor is a
      newer schema and is rejected, not silently accepted.
"""

from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Enumerations and constants (spec §4)
# ---------------------------------------------------------------------------

ORIGINS = ("hemisphere_original", "cc_derived", "executor_report", "operator_record")
ORIGIN_COMPONENTS = ("HEM-A", "HEM-B", "CC", "EXECUTOR", "OPERATOR")
# Origin -> permitted origin_component values (resolution A5).
ORIGIN_COMPONENTS_FOR_ORIGIN = {
    "hemisphere_original": ("HEM-A", "HEM-B"),
    "cc_derived": ("CC",),
    "executor_report": ("EXECUTOR",),
    "operator_record": ("OPERATOR",),
}
HEMISPHERES = ("HEM-A", "HEM-B")
SOURCE_KINDS = (
    "direct_observation",
    "instrument_reading",
    "memory_record",
    "document",
    "hemisphere_inference",
    "external_report",
)
TIERS = ("free", "gated_integrity", "gated_irreversible")
DECISION_STATES = (
    "DELIBERATION_REQUIRED",
    "AUTHORIZATION_REQUIRED",
    "DEFERRED",
    "REJECTED",
    "COMMITTED",
)
DELIBERATION_OUTCOMES = (
    "agreement",
    "agreement_with_reservations",
    "divergence_material",
    "insufficient_information",
)
AUTHORIZATION_RESULTS = ("authorized", "operator_required", "denied")
EXECUTION_STATES = ("not_started", "running", "succeeded", "failed", "partially_succeeded")
VERIFICATION_STATES = ("verified", "verification_failed", "unverifiable")
DECISION_KINDS = (
    "coordination",
    "commit",
    "authority_change",
    "governance_change",
    "objection",
    "escalation",
)
OPERATOR_NAME = "Levi Nord"

RECORD_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*-[0-9a-f]{32}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")

# kind slug -> schema string (§4.1, §5.2).  The slug is also the
# record_id prefix: "<kind>-<uuid4hex>".
SCHEMAS = {
    "interpretation-record": "neurai.cc/interpretation-record",
    "deliberation-record": "neurai.cc/deliberation-record",
    "invariant-check-record": "neurai.cc/invariant-check-record",
    "authorization-record": "neurai.cc/authorization-record",
    "operator-authorization-record": "neurai.cc/operator-authorization-record",
    "operator-resolution-record": "neurai.cc/operator-resolution-record",
    "decision-record": "neurai.cc/decision-record",
    "execution-record": "neurai.cc/execution-record",
    "verification-record": "neurai.cc/verification-record",
}
SCHEMA_TO_KIND = {v: k for k, v in SCHEMAS.items()}

# kind slug -> (required provenance origin, required origin_component or None
# when the component varies, e.g. HEM-A/HEM-B for interpretations).
KIND_ORIGIN = {
    "interpretation-record": ("hemisphere_original", None),
    "deliberation-record": ("cc_derived", "CC"),
    "invariant-check-record": ("cc_derived", "CC"),
    "authorization-record": ("cc_derived", "CC"),
    "operator-authorization-record": ("operator_record", "OPERATOR"),
    "operator-resolution-record": ("operator_record", "OPERATOR"),
    "decision-record": ("cc_derived", "CC"),
    "execution-record": ("executor_report", "EXECUTOR"),
    "verification-record": ("executor_report", "EXECUTOR"),
}


def utc_now_iso() -> str:
    """ISO-8601 UTC timestamp, evidence only, never a decision input (§0)."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_record_id(kind_slug: str, uuid_hex: Optional[str] = None) -> str:
    """``<kind>-<uuid4hex>`` (§4.1).  ``uuid_hex`` is injectable for tests."""
    return f"{kind_slug}-{uuid_hex or uuid.uuid4().hex}"


def canonical_hemisphere_id(value: Any) -> str:
    """Map a supplied hemisphere identifier to its canonical id (D-12).

    ``hem_a`` -> ``HEM-A``, ``hem_b`` -> ``HEM-B`` (also ``hemisphere_a`` /
    ``hemisphere_b`` and the canonical forms themselves).  Anything else
    raises ValueError: hemisphere identifiers are a closed set.
    """
    if isinstance(value, str) and value in HEMISPHERES:
        return value
    normalized = str(value).strip().lower().replace("-", "_")
    if normalized in ("hem_a", "hemisphere_a"):
        return "HEM-A"
    if normalized in ("hem_b", "hemisphere_b"):
        return "HEM-B"
    raise ValueError(f"unknown hemisphere identifier: {value!r}")


def derived_ref(record_id: str, record_hash: str) -> dict:
    """One ``derived_from`` / reference entry: {record_id, record_hash}."""
    return {"record_id": record_id, "record_hash": record_hash}


# ---------------------------------------------------------------------------
# Provenance, EvidenceRef, ActionPayload (§4.1, §4.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Provenance:
    """§4.1 provenance.  P-1: originals carry an empty derived_from and are
    never edited.  P-2: derived records name every source record."""

    origin: str
    origin_component: str
    derived_from: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.origin not in ORIGINS:
            raise ValueError(f"unknown provenance origin: {self.origin!r}")
        allowed = ORIGIN_COMPONENTS_FOR_ORIGIN[self.origin]
        if self.origin_component not in allowed:
            raise ValueError(
                f"origin_component {self.origin_component!r} inconsistent "
                f"with origin {self.origin!r}; allowed: {allowed}"
            )
        if not isinstance(self.derived_from, list):
            raise ValueError("derived_from must be a list")
        for entry in self.derived_from:
            if not isinstance(entry, dict) or set(entry) != {"record_id", "record_hash"}:
                raise ValueError("derived_from entries must be {record_id, record_hash}")
            if not isinstance(entry["record_id"], str) or not RECORD_ID_RE.match(entry["record_id"]):
                raise ValueError(f"bad derived_from record_id: {entry['record_id']!r}")
            if not isinstance(entry["record_hash"], str) or not HASH_RE.match(entry["record_hash"]):
                raise ValueError("bad derived_from record_hash: must be 64 hex chars")
        if self.origin == "hemisphere_original" and self.derived_from:
            raise ValueError("hemisphere_original records must have empty derived_from (P-1)")
        if self.origin == "cc_derived" and not self.derived_from:
            raise ValueError("cc_derived records must name their sources in derived_from (P-2)")

    def to_dict(self) -> dict:
        return {
            "origin": self.origin,
            "origin_component": self.origin_component,
            "derived_from": copy.deepcopy(self.derived_from),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Provenance":
        return cls(
            origin=data["origin"],
            origin_component=data["origin_component"],
            derived_from=copy.deepcopy(data.get("derived_from", [])),
        )


@dataclass(frozen=True, kw_only=True)
class EvidenceRef:
    """§4.3.  A reference with an assessment, not a counted vote (D-02).
    No count or count-derived aggregate of EvidenceRefs may be a decision
    input anywhere in Phase 4; this type carries no such aggregate."""

    pointer: str
    source_kind: str
    relevance: float
    reliability: float

    def __post_init__(self) -> None:
        if not isinstance(self.pointer, str) or not self.pointer:
            raise ValueError("EvidenceRef.pointer must be a non-empty string")
        if self.source_kind not in SOURCE_KINDS:
            raise ValueError(f"unknown source_kind: {self.source_kind!r}")
        for name, value in (("relevance", self.relevance), ("reliability", self.reliability)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"EvidenceRef.{name} must be a number")
            if not 0 <= value <= 1:
                raise ValueError(f"EvidenceRef.{name} must be in [0, 1]")

    def to_dict(self) -> dict:
        return {
            "pointer": self.pointer,
            "source_kind": self.source_kind,
            "relevance": self.relevance,
            "reliability": self.reliability,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvidenceRef":
        return cls(
            pointer=data["pointer"],
            source_kind=data["source_kind"],
            relevance=data["relevance"],
            reliability=data["reliability"],
        )


@dataclass(frozen=True, kw_only=True)
class ActionPayload:
    """§4.3.  ``classification_hint`` and ``reversibility_claim`` are
    untrusted: recorded, never used as the authorization tier (D-03)."""

    action_type: str
    parameters: dict = field(default_factory=dict)
    target: Optional[str] = None
    classification_hint: Optional[str] = None
    reversibility_claim: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.action_type, str) or not self.action_type:
            raise ValueError("ActionPayload.action_type must be a non-empty string")
        if not isinstance(self.parameters, dict):
            raise ValueError("ActionPayload.parameters must be an object")
        if self.target is not None and not isinstance(self.target, str):
            raise ValueError("ActionPayload.target must be a string or null")
        for name in ("classification_hint", "reversibility_claim"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"ActionPayload.{name} must be a string or null")

    def to_dict(self) -> dict:
        return {
            "action_type": self.action_type,
            "parameters": copy.deepcopy(self.parameters),
            "target": self.target,
            "classification_hint": self.classification_hint,
            "reversibility_claim": self.reversibility_claim,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ActionPayload":
        return cls(
            action_type=data["action_type"],
            parameters=copy.deepcopy(data.get("parameters", {})),
            target=data.get("target"),
            classification_hint=data.get("classification_hint"),
            reversibility_claim=data.get("reversibility_claim"),
        )


# ---------------------------------------------------------------------------
# Shared envelope helpers
# ---------------------------------------------------------------------------


def _envelope_defaults(kind_slug: str, record_id, schema, schema_version, created_at):
    """Fill envelope fields with spec defaults; returns the four values."""
    rid = record_id if record_id is not None else new_record_id(kind_slug)
    sch = schema if schema is not None else SCHEMAS[kind_slug]
    ver = schema_version if schema_version is not None else SCHEMA_VERSION
    ts = created_at if created_at is not None else utc_now_iso()
    return rid, sch, ver, ts


def _base_dict(obj) -> dict:
    return {
        "record_id": obj.record_id,
        "schema": obj.schema,
        "schema_version": obj.schema_version,
        "created_at": obj.created_at,
        "provenance": obj.provenance.to_dict(),
    }


def _provenance_from(data) -> Provenance:
    if isinstance(data, Provenance):
        return data
    return Provenance.from_dict(data)

# ---------------------------------------------------------------------------
# InterpretationRecord (§4.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class InterpretationRecord:
    """§4.2.  A hemisphere's own interpretation.  Provenance origin is
    hemisphere_original; the record is immutable (P-1) — corrections are
    new records, never edits."""

    record_id: Optional[str] = None
    schema: Optional[str] = None
    schema_version: Optional[str] = None
    created_at: Optional[str] = None
    provenance: Optional[Any] = None
    hemisphere_id: str = "HEM-A"
    task_reference: str = ""
    original_input_reference: str = ""
    intent: str = ""
    semantic_payload: Optional[Any] = None  # ActionPayload or None; None is not a default here
    assumptions: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    confidence: float = 0.0
    alternatives_considered: list = field(default_factory=list)
    unresolved_questions: list = field(default_factory=list)

    def __post_init__(self) -> None:
        kind = "interpretation-record"
        rid, sch, ver, ts = _envelope_defaults(kind, self.record_id, self.schema, self.schema_version, self.created_at)
        object.__setattr__(self, "record_id", rid)
        object.__setattr__(self, "schema", sch)
        object.__setattr__(self, "schema_version", ver)
        object.__setattr__(self, "created_at", ts)
        # Canonical hemisphere id (D-12); supplied "hem_a" maps to HEM-A.
        hid = canonical_hemisphere_id(self.hemisphere_id)
        object.__setattr__(self, "hemisphere_id", hid)
        prov = self.provenance
        if prov is None:
            prov = Provenance(origin="hemisphere_original", origin_component=hid, derived_from=[])
            object.__setattr__(self, "provenance", prov)
        else:
            prov = _provenance_from(prov)
            object.__setattr__(self, "provenance", prov)
            if prov.origin != "hemisphere_original" or prov.origin_component != hid:
                raise ValueError("InterpretationRecord provenance must be hemisphere_original from its own hemisphere (A5)")
        for name in ("task_reference", "original_input_reference", "intent"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"InterpretationRecord.{name} must be a non-empty string")
        payload = self.semantic_payload
        if payload is not None and not isinstance(payload, ActionPayload):
            payload = ActionPayload.from_dict(payload)
            object.__setattr__(self, "semantic_payload", payload)
        for name in ("assumptions", "alternatives_considered", "unresolved_questions"):
            value = getattr(self, name)
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError(f"InterpretationRecord.{name} must be a list of strings")
        ev = self.evidence
        if not isinstance(ev, list):
            raise ValueError("InterpretationRecord.evidence must be a list")
        converted = [e if isinstance(e, EvidenceRef) else EvidenceRef.from_dict(e) for e in ev]
        object.__setattr__(self, "evidence", converted)
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ValueError("InterpretationRecord.confidence must be a number")
        if not 0 <= self.confidence <= 1:
            raise ValueError("InterpretationRecord.confidence must be in [0, 1]")

    def to_dict(self) -> dict:
        d = _base_dict(self)
        d.update(
            {
                "hemisphere_id": self.hemisphere_id,
                "task_reference": self.task_reference,
                "original_input_reference": self.original_input_reference,
                "intent": self.intent,
                "semantic_payload": self.semantic_payload.to_dict() if self.semantic_payload is not None else None,
                "assumptions": list(self.assumptions),
                "evidence": [e.to_dict() for e in self.evidence],
                "confidence": self.confidence,
                "alternatives_considered": list(self.alternatives_considered),
                "unresolved_questions": list(self.unresolved_questions),
            }
        )
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "InterpretationRecord":
        return cls(
            record_id=data.get("record_id"),
            schema=data.get("schema"),
            schema_version=data.get("schema_version"),
            created_at=data.get("created_at"),
            provenance=Provenance.from_dict(data["provenance"]) if "provenance" in data else None,
            hemisphere_id=data["hemisphere_id"],
            task_reference=data["task_reference"],
            original_input_reference=data["original_input_reference"],
            intent=data["intent"],
            semantic_payload=(
                ActionPayload.from_dict(data["semantic_payload"])
                if data.get("semantic_payload") is not None
                else None
            ),
            assumptions=list(data.get("assumptions", [])),
            evidence=[EvidenceRef.from_dict(e) for e in data.get("evidence", [])],
            confidence=data["confidence"],
            alternatives_considered=list(data.get("alternatives_considered", [])),
            unresolved_questions=list(data.get("unresolved_questions", [])),
        )


# ---------------------------------------------------------------------------
# DeliberationRecord (§4.4, cc_derived)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class DeliberationRecord:
    """§4.4.  Embeds both InterpretationRecords by value (D-05).  The
    complete disagreement record: field-level divergence_points, verbatim
    evidence_profile and uncertainty, required non-empty cc_reasoning.
    ``selected_payload`` is set only on agreement outcomes; it is the
    payload both records agree on, never a "winner's" payload."""

    record_id: Optional[str] = None
    schema: Optional[str] = None
    schema_version: Optional[str] = None
    created_at: Optional[str] = None
    provenance: Optional[Any] = None
    interpretations: list = field(default_factory=list)
    equivalence: dict = field(default_factory=dict)
    agreement_points: list = field(default_factory=list)
    divergence_points: list = field(default_factory=list)
    evidence_profile: dict = field(default_factory=dict)
    uncertainty: dict = field(default_factory=dict)
    outcome: str = ""
    selected_payload: Optional[Any] = None
    cc_reasoning: str = ""

    def __post_init__(self) -> None:
        kind = "deliberation-record"
        rid, sch, ver, ts = _envelope_defaults(kind, self.record_id, self.schema, self.schema_version, self.created_at)
        object.__setattr__(self, "record_id", rid)
        object.__setattr__(self, "schema", sch)
        object.__setattr__(self, "schema_version", ver)
        object.__setattr__(self, "created_at", ts)
        prov = self.provenance
        if prov is None:
            raise ValueError("DeliberationRecord requires provenance (cc_derived, with derived_from)")
        prov = _provenance_from(prov)
        object.__setattr__(self, "provenance", prov)
        if prov.origin != "cc_derived" or prov.origin_component != "CC":
            raise ValueError("DeliberationRecord provenance must be cc_derived from CC (P-2)")
        if not isinstance(self.interpretations, list) or len(self.interpretations) != 2:
            raise ValueError("DeliberationRecord.interpretations must be exactly two InterpretationRecords")
        converted = [
            i if isinstance(i, InterpretationRecord) else InterpretationRecord.from_dict(i)
            for i in self.interpretations
        ]
        object.__setattr__(self, "interpretations", converted)
        if not isinstance(self.equivalence, dict) or set(self.equivalence) != {
            "action_type_equal",
            "parameters_equal",
            "target_equal",
        } or not all(isinstance(v, bool) for v in self.equivalence.values()):
            raise ValueError("DeliberationRecord.equivalence must be {action_type_equal, parameters_equal, target_equal} bools")
        for name in ("agreement_points",):
            value = getattr(self, name)
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError(f"DeliberationRecord.{name} must be a list of strings")
        if not isinstance(self.divergence_points, list):
            raise ValueError("DeliberationRecord.divergence_points must be a list")
        for entry in self.divergence_points:
            if not isinstance(entry, dict) or set(entry) != {"field", "value_a", "value_b"}:
                raise ValueError("divergence_points entries must be {field, value_a, value_b}")
            if not isinstance(entry["field"], str):
                raise ValueError("divergence_points.field must be a string")
        if not isinstance(self.evidence_profile, dict) or set(self.evidence_profile) != {"HEM-A", "HEM-B"}:
            raise ValueError("DeliberationRecord.evidence_profile must have exactly HEM-A and HEM-B keys")
        for hemi, refs in self.evidence_profile.items():
            if not isinstance(refs, list):
                raise ValueError("DeliberationRecord.evidence_profile values must be lists")
            # Validate each ref (constructing EvidenceRef) but store the
            # plain dict form, so the attribute matches to_dict() output.
            self.evidence_profile[hemi] = [
                (r.to_dict() if isinstance(r, EvidenceRef) else EvidenceRef.from_dict(r).to_dict())
                for r in refs
            ]
        if not isinstance(self.uncertainty, dict) or set(self.uncertainty) != {"confidence", "unresolved_questions"}:
            raise ValueError("DeliberationRecord.uncertainty must be {confidence, unresolved_questions}")
        conf = self.uncertainty["confidence"]
        if not isinstance(conf, dict) or set(conf) != {"HEM-A", "HEM-B"}:
            raise ValueError("DeliberationRecord.uncertainty.confidence must have exactly HEM-A and HEM-B keys")
        for value in conf.values():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise ValueError("DeliberationRecord.uncertainty.confidence values must be numbers in [0, 1]")
        if not isinstance(self.uncertainty["unresolved_questions"], list) or not all(
            isinstance(x, str) for x in self.uncertainty["unresolved_questions"]
        ):
            raise ValueError("DeliberationRecord.uncertainty.unresolved_questions must be a list of strings")
        if self.outcome not in DELIBERATION_OUTCOMES:
            raise ValueError(f"unknown deliberation outcome: {self.outcome!r}")
        payload = self.selected_payload
        if payload is not None and not isinstance(payload, ActionPayload):
            payload = ActionPayload.from_dict(payload)
            object.__setattr__(self, "selected_payload", payload)
        if not isinstance(self.cc_reasoning, str) or not self.cc_reasoning:
            raise ValueError("DeliberationRecord.cc_reasoning is required and non-empty")

    def to_dict(self) -> dict:
        d = _base_dict(self)
        d.update(
            {
                "interpretations": [i.to_dict() for i in self.interpretations],
                "equivalence": dict(self.equivalence),
                "agreement_points": list(self.agreement_points),
                "divergence_points": copy.deepcopy(self.divergence_points),
                "evidence_profile": copy.deepcopy(self.evidence_profile),
                "uncertainty": copy.deepcopy(self.uncertainty),
                "outcome": self.outcome,
                "selected_payload": self.selected_payload.to_dict() if self.selected_payload is not None else None,
                "cc_reasoning": self.cc_reasoning,
            }
        )
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "DeliberationRecord":
        return cls(
            record_id=data.get("record_id"),
            schema=data.get("schema"),
            schema_version=data.get("schema_version"),
            created_at=data.get("created_at"),
            provenance=Provenance.from_dict(data["provenance"]) if "provenance" in data else None,
            interpretations=[InterpretationRecord.from_dict(i) for i in data["interpretations"]],
            equivalence=dict(data["equivalence"]),
            agreement_points=list(data.get("agreement_points", [])),
            divergence_points=copy.deepcopy(data.get("divergence_points", [])),
            evidence_profile={
                hemi: [EvidenceRef.from_dict(r) for r in refs]
                for hemi, refs in data["evidence_profile"].items()
            },
            uncertainty=copy.deepcopy(data["uncertainty"]),
            outcome=data["outcome"],
            selected_payload=(
                ActionPayload.from_dict(data["selected_payload"])
                if data.get("selected_payload") is not None
                else None
            ),
            cc_reasoning=data["cc_reasoning"],
        )

# ---------------------------------------------------------------------------
# InvariantCheckRecord, AuthorizationRecord, Operator records (§4.5, §7.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class InvariantCheckRecord:
    """§4.5.  One per check run (§8).  A decision carries the full list; a
    fail is never summarised away.  ``check_id`` defaults to ``record_id``
    (resolution A1)."""

    record_id: Optional[str] = None
    schema: Optional[str] = None
    schema_version: Optional[str] = None
    created_at: Optional[str] = None
    provenance: Optional[Any] = None
    check_id: Optional[str] = None
    invariant_id: str = ""
    result: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        kind = "invariant-check-record"
        rid, sch, ver, ts = _envelope_defaults(kind, self.record_id, self.schema, self.schema_version, self.created_at)
        object.__setattr__(self, "record_id", rid)
        object.__setattr__(self, "schema", sch)
        object.__setattr__(self, "schema_version", ver)
        object.__setattr__(self, "created_at", ts)
        prov = self.provenance
        if prov is None:
            raise ValueError("InvariantCheckRecord requires provenance (cc_derived)")
        prov = _provenance_from(prov)
        object.__setattr__(self, "provenance", prov)
        if prov.origin != "cc_derived" or prov.origin_component != "CC":
            raise ValueError("InvariantCheckRecord provenance must be cc_derived from CC")
        if self.check_id is None:
            object.__setattr__(self, "check_id", rid)
        if not isinstance(self.check_id, str) or not self.check_id:
            raise ValueError("InvariantCheckRecord.check_id must be a non-empty string")
        if not isinstance(self.invariant_id, str) or not self.invariant_id:
            raise ValueError("InvariantCheckRecord.invariant_id must be a non-empty string")
        if self.result not in ("pass", "fail"):
            raise ValueError("InvariantCheckRecord.result must be pass or fail")
        if not isinstance(self.detail, str):
            raise ValueError("InvariantCheckRecord.detail must be a string")

    def to_dict(self) -> dict:
        d = _base_dict(self)
        d.update(
            {"check_id": self.check_id, "invariant_id": self.invariant_id, "result": self.result, "detail": self.detail}
        )
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "InvariantCheckRecord":
        return cls(
            record_id=data.get("record_id"),
            schema=data.get("schema"),
            schema_version=data.get("schema_version"),
            created_at=data.get("created_at"),
            provenance=Provenance.from_dict(data["provenance"]) if "provenance" in data else None,
            check_id=data.get("check_id"),
            invariant_id=data["invariant_id"],
            result=data["result"],
            detail=data.get("detail", ""),
        )

    def core_dict(self) -> dict:
        """The nested form carried inside a DecisionRecord (§4.5): the four
        core fields only, without the record envelope."""
        return {
            "check_id": self.check_id,
            "invariant_id": self.invariant_id,
            "result": self.result,
            "detail": self.detail,
        }


@dataclass(frozen=True, kw_only=True)
class AuthorizationRecord:
    """§4.5, cc_derived.  The tier is policy-derived only (D-03); the
    proposal's hint is recorded in ``hint_received`` / ``hint_agrees``
    and never used as the tier."""

    record_id: Optional[str] = None
    schema: Optional[str] = None
    schema_version: Optional[str] = None
    created_at: Optional[str] = None
    provenance: Optional[Any] = None
    action_type: str = ""
    target: Optional[str] = None
    derived_tier: str = ""
    policy_rule: str = ""
    policy_version: str = ""
    policy_hash: str = ""
    hint_received: Optional[str] = None
    hint_agrees: Optional[bool] = None
    result: str = ""

    def __post_init__(self) -> None:
        kind = "authorization-record"
        rid, sch, ver, ts = _envelope_defaults(kind, self.record_id, self.schema, self.schema_version, self.created_at)
        object.__setattr__(self, "record_id", rid)
        object.__setattr__(self, "schema", sch)
        object.__setattr__(self, "schema_version", ver)
        object.__setattr__(self, "created_at", ts)
        prov = self.provenance
        if prov is None:
            raise ValueError("AuthorizationRecord requires provenance (cc_derived, with derived_from)")
        prov = _provenance_from(prov)
        object.__setattr__(self, "provenance", prov)
        if prov.origin != "cc_derived" or prov.origin_component != "CC":
            raise ValueError("AuthorizationRecord provenance must be cc_derived from CC (P-2)")
        if not isinstance(self.action_type, str) or not self.action_type:
            raise ValueError("AuthorizationRecord.action_type must be a non-empty string")
        if self.target is not None and not isinstance(self.target, str):
            raise ValueError("AuthorizationRecord.target must be a string or null")
        if self.derived_tier not in TIERS:
            raise ValueError(f"unknown derived_tier: {self.derived_tier!r}")
        if not isinstance(self.policy_rule, str) or not self.policy_rule:
            raise ValueError("AuthorizationRecord.policy_rule must be a non-empty string")
        if not isinstance(self.policy_version, str) or not self.policy_version:
            raise ValueError("AuthorizationRecord.policy_version must be a non-empty string")
        if not isinstance(self.policy_hash, str) or not HASH_RE.match(self.policy_hash):
            raise ValueError("AuthorizationRecord.policy_hash must be 64 hex chars")
        if self.hint_received is not None and not isinstance(self.hint_received, str):
            raise ValueError("AuthorizationRecord.hint_received must be a string or null")
        if self.hint_agrees is not None and not isinstance(self.hint_agrees, bool):
            raise ValueError("AuthorizationRecord.hint_agrees must be a bool or null")
        if self.result not in AUTHORIZATION_RESULTS:
            raise ValueError(f"unknown authorization result: {self.result!r}")

    def to_dict(self) -> dict:
        d = _base_dict(self)
        d.update(
            {
                "action_type": self.action_type,
                "target": self.target,
                "derived_tier": self.derived_tier,
                "policy_rule": self.policy_rule,
                "policy_version": self.policy_version,
                "policy_hash": self.policy_hash,
                "hint_received": self.hint_received,
                "hint_agrees": self.hint_agrees,
                "result": self.result,
            }
        )
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "AuthorizationRecord":
        return cls(
            record_id=data.get("record_id"),
            schema=data.get("schema"),
            schema_version=data.get("schema_version"),
            created_at=data.get("created_at"),
            provenance=Provenance.from_dict(data["provenance"]) if "provenance" in data else None,
            action_type=data["action_type"],
            target=data.get("target"),
            derived_tier=data["derived_tier"],
            policy_rule=data["policy_rule"],
            policy_version=data["policy_version"],
            policy_hash=data["policy_hash"],
            hint_received=data.get("hint_received"),
            hint_agrees=data.get("hint_agrees"),
            result=data["result"],
        )


@dataclass(frozen=True, kw_only=True)
class OperatorAuthorizationRecord:
    """§4.5, operator_record.  The only record that can move a gated
    decision from AUTHORIZATION_REQUIRED to COMMITTED.  Operator denials
    are records too, and are terminal for that decision."""

    record_id: Optional[str] = None
    schema: Optional[str] = None
    schema_version: Optional[str] = None
    created_at: Optional[str] = None
    provenance: Optional[Any] = None
    decision_id: str = ""
    action_type: str = ""
    granted: bool = False
    operator: str = OPERATOR_NAME
    note: str = ""

    def __post_init__(self) -> None:
        kind = "operator-authorization-record"
        rid, sch, ver, ts = _envelope_defaults(kind, self.record_id, self.schema, self.schema_version, self.created_at)
        object.__setattr__(self, "record_id", rid)
        object.__setattr__(self, "schema", sch)
        object.__setattr__(self, "schema_version", ver)
        object.__setattr__(self, "created_at", ts)
        prov = self.provenance
        if prov is None:
            prov = Provenance(origin="operator_record", origin_component="OPERATOR", derived_from=[])
            object.__setattr__(self, "provenance", prov)
        else:
            prov = _provenance_from(prov)
            object.__setattr__(self, "provenance", prov)
            if prov.origin != "operator_record" or prov.origin_component != "OPERATOR":
                raise ValueError("OperatorAuthorizationRecord provenance must be operator_record from OPERATOR")
        if not isinstance(self.decision_id, str) or not self.decision_id:
            raise ValueError("OperatorAuthorizationRecord.decision_id must be a non-empty string")
        if not isinstance(self.action_type, str) or not self.action_type:
            raise ValueError("OperatorAuthorizationRecord.action_type must be a non-empty string")
        if not isinstance(self.granted, bool):
            raise ValueError("OperatorAuthorizationRecord.granted must be a bool")
        if self.operator != OPERATOR_NAME:
            raise ValueError(f"OperatorAuthorizationRecord.operator must be {OPERATOR_NAME!r} (A6)")
        if not isinstance(self.note, str):
            raise ValueError("OperatorAuthorizationRecord.note must be a string")

    def to_dict(self) -> dict:
        d = _base_dict(self)
        d.update(
            {
                "decision_id": self.decision_id,
                "action_type": self.action_type,
                "granted": self.granted,
                "operator": self.operator,
                "note": self.note,
            }
        )
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "OperatorAuthorizationRecord":
        return cls(
            record_id=data.get("record_id"),
            schema=data.get("schema"),
            schema_version=data.get("schema_version"),
            created_at=data.get("created_at"),
            provenance=Provenance.from_dict(data["provenance"]) if "provenance" in data else None,
            decision_id=data["decision_id"],
            action_type=data["action_type"],
            granted=data["granted"],
            operator=data.get("operator", OPERATOR_NAME),
            note=data.get("note", ""),
        )


@dataclass(frozen=True, kw_only=True)
class OperatorResolutionRecord:
    """§7.5, operator_record.  The human divergence escape hatch, openly,
    on the record.  The CC must not manufacture an equivalent outcome."""

    record_id: Optional[str] = None
    schema: Optional[str] = None
    schema_version: Optional[str] = None
    created_at: Optional[str] = None
    provenance: Optional[Any] = None
    decision_id: str = ""
    chosen_payload: Optional[Any] = None
    note: str = ""

    def __post_init__(self) -> None:
        kind = "operator-resolution-record"
        rid, sch, ver, ts = _envelope_defaults(kind, self.record_id, self.schema, self.schema_version, self.created_at)
        object.__setattr__(self, "record_id", rid)
        object.__setattr__(self, "schema", sch)
        object.__setattr__(self, "schema_version", ver)
        object.__setattr__(self, "created_at", ts)
        prov = self.provenance
        if prov is None:
            prov = Provenance(origin="operator_record", origin_component="OPERATOR", derived_from=[])
            object.__setattr__(self, "provenance", prov)
        else:
            prov = _provenance_from(prov)
            object.__setattr__(self, "provenance", prov)
            if prov.origin != "operator_record" or prov.origin_component != "OPERATOR":
                raise ValueError("OperatorResolutionRecord provenance must be operator_record from OPERATOR")
        if not isinstance(self.decision_id, str) or not self.decision_id:
            raise ValueError("OperatorResolutionRecord.decision_id must be a non-empty string")
        payload = self.chosen_payload
        if payload is None:
            raise ValueError("OperatorResolutionRecord.chosen_payload is required")
        if not isinstance(payload, ActionPayload):
            payload = ActionPayload.from_dict(payload)
            object.__setattr__(self, "chosen_payload", payload)
        if not isinstance(self.note, str):
            raise ValueError("OperatorResolutionRecord.note must be a string")

    def to_dict(self) -> dict:
        d = _base_dict(self)
        d.update(
            {
                "decision_id": self.decision_id,
                "chosen_payload": self.chosen_payload.to_dict(),
                "note": self.note,
            }
        )
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "OperatorResolutionRecord":
        return cls(
            record_id=data.get("record_id"),
            schema=data.get("schema"),
            schema_version=data.get("schema_version"),
            created_at=data.get("created_at"),
            provenance=Provenance.from_dict(data["provenance"]) if "provenance" in data else None,
            decision_id=data["decision_id"],
            chosen_payload=ActionPayload.from_dict(data["chosen_payload"]),
            note=data.get("note", ""),
        )

# ---------------------------------------------------------------------------
# DecisionRecord (§4.6), ExecutionRecord, VerificationRecord (§4.7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class DecisionRecord:
    """§4.6.  The CC's central output.  Core fields conform to the Phase-0
    ``decision_record`` schema in
    ``architecture/contract/component-interfaces.yaml``; extended with
    state, deliberation/authorization refs, invariant checks, execution /
    verification refs, and deferral_reason.

    COMMITTED means deliberation concluded, invariants passed,
    authorization granted — nothing more (D-01).  It asserts nothing
    about execution; execution and verification are separate records
    (§4.7) and the user-facing status is the tuple (decision state,
    execution state, verification state).
    ``decision_id`` defaults to ``record_id`` (A1); ``timestamp`` is set
    equal to ``created_at`` (A2); ``authority_tier`` may be null until an
    AuthorizationRecord exists (A3); the deferral stage is carried in
    ``deferral_reason`` as "<stage>: <reason>" (A4)."""

    record_id: Optional[str] = None
    schema: Optional[str] = None
    schema_version: Optional[str] = None
    created_at: Optional[str] = None
    provenance: Optional[Any] = None
    decision_id: Optional[str] = None
    timestamp: Optional[str] = None
    kind: str = "coordination"
    subject: str = ""
    rationale: str = ""
    alternatives_considered: list = field(default_factory=list)
    unresolved_objections: list = field(default_factory=list)
    authority_tier: Optional[str] = None
    change_record_reference: Optional[str] = None
    prior_text: Optional[str] = None
    new_text: Optional[str] = None
    state: str = "DELIBERATION_REQUIRED"
    deliberation_ref: Optional[dict] = None
    authorization_ref: Optional[dict] = None
    invariant_checks: list = field(default_factory=list)
    execution_ref: Optional[dict] = None
    verification_ref: Optional[dict] = None
    deferral_reason: Optional[str] = None

    def __post_init__(self) -> None:
        kind_slug = "decision-record"
        rid, sch, ver, ts = _envelope_defaults(kind_slug, self.record_id, self.schema, self.schema_version, self.created_at)
        object.__setattr__(self, "record_id", rid)
        object.__setattr__(self, "schema", sch)
        object.__setattr__(self, "schema_version", ver)
        object.__setattr__(self, "created_at", ts)
        prov = self.provenance
        if prov is None:
            raise ValueError("DecisionRecord requires provenance (cc_derived)")
        prov = _provenance_from(prov)
        object.__setattr__(self, "provenance", prov)
        if prov.origin != "cc_derived" or prov.origin_component != "CC":
            raise ValueError("DecisionRecord provenance must be cc_derived from CC")
        if self.decision_id is None:
            object.__setattr__(self, "decision_id", rid)
        if self.timestamp is None:
            object.__setattr__(self, "timestamp", ts)
        if self.kind not in DECISION_KINDS:
            raise ValueError(f"unknown decision kind: {self.kind!r}")
        if not isinstance(self.subject, str) or not self.subject:
            raise ValueError("DecisionRecord.subject must be a non-empty string")
        if not isinstance(self.rationale, str) or not self.rationale:
            raise ValueError("DecisionRecord.rationale must be a non-empty string")
        for name in ("alternatives_considered", "unresolved_objections"):
            value = getattr(self, name)
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError(f"DecisionRecord.{name} must be a list of strings")
        if self.authority_tier is not None and self.authority_tier not in TIERS:
            raise ValueError(f"unknown authority_tier: {self.authority_tier!r}")
        for name in ("change_record_reference", "prior_text", "new_text", "deferral_reason"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"DecisionRecord.{name} must be a string or null")
        if self.state not in DECISION_STATES:
            raise ValueError(f"unknown decision state: {self.state!r}")
        for name in ("deliberation_ref", "authorization_ref", "execution_ref", "verification_ref"):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, dict) or set(value) != {"record_id", "record_hash"}:
                    raise ValueError(f"DecisionRecord.{name} must be {{record_id, record_hash}} or null")
        if not isinstance(self.invariant_checks, list):
            raise ValueError("DecisionRecord.invariant_checks must be a list")
        converted = []
        for check in self.invariant_checks:
            if isinstance(check, InvariantCheckRecord):
                converted.append(check.core_dict())
            elif isinstance(check, dict):
                if set(check) != {"check_id", "invariant_id", "result", "detail"}:
                    raise ValueError("invariant_checks entries must be {check_id, invariant_id, result, detail}")
                converted.append(dict(check))
            else:
                raise ValueError("invariant_checks entries must be InvariantCheckRecord or dict")
        object.__setattr__(self, "invariant_checks", converted)

    def to_dict(self) -> dict:
        d = _base_dict(self)
        d.update(
            {
                "decision_id": self.decision_id,
                "timestamp": self.timestamp,
                "kind": self.kind,
                "subject": self.subject,
                "rationale": self.rationale,
                "alternatives_considered": list(self.alternatives_considered),
                "unresolved_objections": list(self.unresolved_objections),
                "authority_tier": self.authority_tier,
                "change_record_reference": self.change_record_reference,
                "prior_text": self.prior_text,
                "new_text": self.new_text,
                "state": self.state,
                "deliberation_ref": copy.deepcopy(self.deliberation_ref) if self.deliberation_ref is not None else None,
                "authorization_ref": copy.deepcopy(self.authorization_ref) if self.authorization_ref is not None else None,
                "invariant_checks": copy.deepcopy(self.invariant_checks),
                "execution_ref": copy.deepcopy(self.execution_ref) if self.execution_ref is not None else None,
                "verification_ref": copy.deepcopy(self.verification_ref) if self.verification_ref is not None else None,
                "deferral_reason": self.deferral_reason,
            }
        )
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "DecisionRecord":
        return cls(
            record_id=data.get("record_id"),
            schema=data.get("schema"),
            schema_version=data.get("schema_version"),
            created_at=data.get("created_at"),
            provenance=Provenance.from_dict(data["provenance"]) if "provenance" in data else None,
            decision_id=data.get("decision_id"),
            timestamp=data.get("timestamp"),
            kind=data.get("kind", "coordination"),
            subject=data["subject"],
            rationale=data["rationale"],
            alternatives_considered=list(data.get("alternatives_considered", [])),
            unresolved_objections=list(data.get("unresolved_objections", [])),
            authority_tier=data.get("authority_tier"),
            change_record_reference=data.get("change_record_reference"),
            prior_text=data.get("prior_text"),
            new_text=data.get("new_text"),
            state=data["state"],
            deliberation_ref=copy.deepcopy(data.get("deliberation_ref")),
            authorization_ref=copy.deepcopy(data.get("authorization_ref")),
            invariant_checks=copy.deepcopy(data.get("invariant_checks", [])),
            execution_ref=copy.deepcopy(data.get("execution_ref")),
            verification_ref=copy.deepcopy(data.get("verification_ref")),
            deferral_reason=data.get("deferral_reason"),
        )


@dataclass(frozen=True, kw_only=True)
class ExecutionRecord:
    """§4.7, executor_report.  Execution is a separate lifecycle from the
    decision (D-01): a COMMITTED decision whose execution failed is an
    authorized action that failed, never "done".  ``execution_id``
    defaults to ``record_id`` (A1).  For free-tier state changes,
    ``rollback_reference`` MUST be populated before the action is
    performed (§10.3) — that rule is enforced by the executor in step
    4.7, not by this record type."""

    record_id: Optional[str] = None
    schema: Optional[str] = None
    schema_version: Optional[str] = None
    created_at: Optional[str] = None
    provenance: Optional[Any] = None
    execution_id: Optional[str] = None
    decision_id: str = ""
    executor_id: str = ""
    state: str = "not_started"
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    rollback_reference: Optional[str] = None
    result_summary: str = ""
    error: Optional[str] = None

    def __post_init__(self) -> None:
        kind = "execution-record"
        rid, sch, ver, ts = _envelope_defaults(kind, self.record_id, self.schema, self.schema_version, self.created_at)
        object.__setattr__(self, "record_id", rid)
        object.__setattr__(self, "schema", sch)
        object.__setattr__(self, "schema_version", ver)
        object.__setattr__(self, "created_at", ts)
        prov = self.provenance
        if prov is None:
            raise ValueError("ExecutionRecord requires provenance (executor_report)")
        prov = _provenance_from(prov)
        object.__setattr__(self, "provenance", prov)
        if prov.origin != "executor_report" or prov.origin_component != "EXECUTOR":
            raise ValueError("ExecutionRecord provenance must be executor_report from EXECUTOR")
        if self.execution_id is None:
            object.__setattr__(self, "execution_id", rid)
        if not isinstance(self.decision_id, str) or not self.decision_id:
            raise ValueError("ExecutionRecord.decision_id must be a non-empty string")
        if not isinstance(self.executor_id, str) or not self.executor_id:
            raise ValueError("ExecutionRecord.executor_id must be a non-empty string")
        if self.state not in EXECUTION_STATES:
            raise ValueError(f"unknown execution state: {self.state!r}")
        for name in ("started_at", "finished_at", "rollback_reference", "error"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"ExecutionRecord.{name} must be a string or null")
        if not isinstance(self.result_summary, str):
            raise ValueError("ExecutionRecord.result_summary must be a string")

    def to_dict(self) -> dict:
        d = _base_dict(self)
        d.update(
            {
                "execution_id": self.execution_id,
                "decision_id": self.decision_id,
                "executor_id": self.executor_id,
                "state": self.state,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "rollback_reference": self.rollback_reference,
                "result_summary": self.result_summary,
                "error": self.error,
            }
        )
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionRecord":
        return cls(
            record_id=data.get("record_id"),
            schema=data.get("schema"),
            schema_version=data.get("schema_version"),
            created_at=data.get("created_at"),
            provenance=Provenance.from_dict(data["provenance"]) if "provenance" in data else None,
            execution_id=data.get("execution_id"),
            decision_id=data["decision_id"],
            executor_id=data["executor_id"],
            state=data["state"],
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            rollback_reference=data.get("rollback_reference"),
            result_summary=data.get("result_summary", ""),
            error=data.get("error"),
        )


@dataclass(frozen=True, kw_only=True)
class VerificationRecord:
    """§4.7, executor_report.  Verification is a separate step from
    execution (§10.2): an action type with no registered verifier yields
    ``unverifiable``, never ``verified`` by optimism."""

    record_id: Optional[str] = None
    schema: Optional[str] = None
    schema_version: Optional[str] = None
    created_at: Optional[str] = None
    provenance: Optional[Any] = None
    verification_id: Optional[str] = None
    execution_id: str = ""
    state: str = ""
    checks: list = field(default_factory=list)
    verified_by: str = ""

    def __post_init__(self) -> None:
        kind = "verification-record"
        rid, sch, ver, ts = _envelope_defaults(kind, self.record_id, self.schema, self.schema_version, self.created_at)
        object.__setattr__(self, "record_id", rid)
        object.__setattr__(self, "schema", sch)
        object.__setattr__(self, "schema_version", ver)
        object.__setattr__(self, "created_at", ts)
        prov = self.provenance
        if prov is None:
            raise ValueError("VerificationRecord requires provenance (executor_report)")
        prov = _provenance_from(prov)
        object.__setattr__(self, "provenance", prov)
        if prov.origin != "executor_report" or prov.origin_component != "EXECUTOR":
            raise ValueError("VerificationRecord provenance must be executor_report from EXECUTOR")
        if self.verification_id is None:
            object.__setattr__(self, "verification_id", rid)
        if not isinstance(self.execution_id, str) or not self.execution_id:
            raise ValueError("VerificationRecord.execution_id must be a non-empty string")
        if self.state not in VERIFICATION_STATES:
            raise ValueError(f"unknown verification state: {self.state!r}")
        if not isinstance(self.checks, list):
            raise ValueError("VerificationRecord.checks must be a list")
        for entry in self.checks:
            if not isinstance(entry, dict) or set(entry) != {"name", "result", "detail"}:
                raise ValueError("verification checks must be {name, result, detail}")
            if not isinstance(entry["name"], str) or not entry["name"]:
                raise ValueError("verification check name must be a non-empty string")
            if not (isinstance(entry["result"], bool) or entry["result"] in ("pass", "fail")):
                raise ValueError("verification check result must be a bool or pass/fail (A7)")
            if not isinstance(entry["detail"], str):
                raise ValueError("verification check detail must be a string")
        if not isinstance(self.verified_by, str) or not self.verified_by:
            raise ValueError("VerificationRecord.verified_by must be a non-empty string")

    def to_dict(self) -> dict:
        d = _base_dict(self)
        d.update(
            {
                "verification_id": self.verification_id,
                "execution_id": self.execution_id,
                "state": self.state,
                "checks": copy.deepcopy(self.checks),
                "verified_by": self.verified_by,
            }
        )
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "VerificationRecord":
        return cls(
            record_id=data.get("record_id"),
            schema=data.get("schema"),
            schema_version=data.get("schema_version"),
            created_at=data.get("created_at"),
            provenance=Provenance.from_dict(data["provenance"]) if "provenance" in data else None,
            verification_id=data.get("verification_id"),
            execution_id=data["execution_id"],
            state=data["state"],
            checks=copy.deepcopy(data.get("checks", [])),
            verified_by=data["verified_by"],
        )


# ---------------------------------------------------------------------------
# Closed field sets for validation (§4, §5.4)
#
# Used by serialization.py.  Each entry: field name -> spec dict.
#   type: see serialization._validate_value
#   required: True means the field must be present in the record dict.
#             False means it may be absent (defaults apply on construction).
#   enum: permitted values, for enum-typed fields.
#   non_empty: the string must be non-empty.
# ---------------------------------------------------------------------------

_COMMON_FIELDS = {
    "record_id": {"type": "record_id", "required": True},
    "schema": {"type": "str", "required": True},
    "schema_version": {"type": "str", "required": True},
    "created_at": {"type": "iso8601", "required": True},
    "provenance": {"type": "provenance", "required": True},
}

SCHEMA_FIELDS = {
    SCHEMAS["interpretation-record"]: {
        **_COMMON_FIELDS,
        "hemisphere_id": {"type": "str", "required": True, "enum": HEMISPHERES},
        "task_reference": {"type": "str", "required": True, "non_empty": True},
        "original_input_reference": {"type": "str", "required": True, "non_empty": True},
        "intent": {"type": "str", "required": True, "non_empty": True},
        "semantic_payload": {"type": "action_payload_or_null", "required": True},
        "assumptions": {"type": "list_str", "required": True},
        "evidence": {"type": "list_evidence", "required": True},
        "confidence": {"type": "number", "required": True, "min": 0, "max": 1},
        "alternatives_considered": {"type": "list_str", "required": True},
        "unresolved_questions": {"type": "list_str", "required": False},
    },
    SCHEMAS["deliberation-record"]: {
        **_COMMON_FIELDS,
        "interpretations": {"type": "list_interpretation", "required": True},
        "equivalence": {"type": "equivalence", "required": True},
        "agreement_points": {"type": "list_str", "required": True},
        "divergence_points": {"type": "list_divergence", "required": True},
        "evidence_profile": {"type": "evidence_profile", "required": True},
        "uncertainty": {"type": "uncertainty", "required": True},
        "outcome": {"type": "str", "required": True, "enum": DELIBERATION_OUTCOMES},
        "selected_payload": {"type": "action_payload_or_null", "required": True},
        "cc_reasoning": {"type": "str", "required": True, "non_empty": True},
    },
    SCHEMAS["invariant-check-record"]: {
        **_COMMON_FIELDS,
        "check_id": {"type": "str", "required": True, "non_empty": True},
        "invariant_id": {"type": "str", "required": True, "non_empty": True},
        "result": {"type": "str", "required": True, "enum": ("pass", "fail")},
        "detail": {"type": "str", "required": True},
    },
    SCHEMAS["authorization-record"]: {
        **_COMMON_FIELDS,
        "action_type": {"type": "str", "required": True, "non_empty": True},
        "target": {"type": "str_or_null", "required": True},
        "derived_tier": {"type": "str", "required": True, "enum": TIERS},
        "policy_rule": {"type": "str", "required": True, "non_empty": True},
        "policy_version": {"type": "str", "required": True, "non_empty": True},
        "policy_hash": {"type": "hash", "required": True},
        "hint_received": {"type": "str_or_null", "required": True},
        "hint_agrees": {"type": "bool_or_null", "required": True},
        "result": {"type": "str", "required": True, "enum": AUTHORIZATION_RESULTS},
    },
    SCHEMAS["operator-authorization-record"]: {
        **_COMMON_FIELDS,
        "decision_id": {"type": "str", "required": True, "non_empty": True},
        "action_type": {"type": "str", "required": True, "non_empty": True},
        "granted": {"type": "bool", "required": True},
        "operator": {"type": "str", "required": True, "enum": (OPERATOR_NAME,)},
        "note": {"type": "str", "required": True},
    },
    SCHEMAS["operator-resolution-record"]: {
        **_COMMON_FIELDS,
        "decision_id": {"type": "str", "required": True, "non_empty": True},
        "chosen_payload": {"type": "action_payload", "required": True},
        "note": {"type": "str", "required": True},
    },
    SCHEMAS["decision-record"]: {
        **_COMMON_FIELDS,
        "decision_id": {"type": "str", "required": True, "non_empty": True},
        "timestamp": {"type": "iso8601", "required": True},
        "kind": {"type": "str", "required": True, "enum": DECISION_KINDS},
        "subject": {"type": "str", "required": True, "non_empty": True},
        "rationale": {"type": "str", "required": True, "non_empty": True},
        "alternatives_considered": {"type": "list_str", "required": False},
        "unresolved_objections": {"type": "list_str", "required": False},
        "authority_tier": {"type": "str_or_null", "required": False, "enum": TIERS},
        "change_record_reference": {"type": "str_or_null", "required": False},
        "prior_text": {"type": "str_or_null", "required": False},
        "new_text": {"type": "str_or_null", "required": False},
        "state": {"type": "str", "required": True, "enum": DECISION_STATES},
        "deliberation_ref": {"type": "ref_or_null", "required": False},
        "authorization_ref": {"type": "ref_or_null", "required": False},
        "invariant_checks": {"type": "list_invariant_core", "required": True},
        "execution_ref": {"type": "ref_or_null", "required": False},
        "verification_ref": {"type": "ref_or_null", "required": False},
        "deferral_reason": {"type": "str_or_null", "required": False},
    },
    SCHEMAS["execution-record"]: {
        **_COMMON_FIELDS,
        "execution_id": {"type": "str", "required": True, "non_empty": True},
        "decision_id": {"type": "str", "required": True, "non_empty": True},
        "executor_id": {"type": "str", "required": True, "non_empty": True},
        "state": {"type": "str", "required": True, "enum": EXECUTION_STATES},
        "started_at": {"type": "iso8601_or_null", "required": True},
        "finished_at": {"type": "iso8601_or_null", "required": True},
        "rollback_reference": {"type": "str_or_null", "required": True},
        "result_summary": {"type": "str", "required": True},
        "error": {"type": "str_or_null", "required": True},
    },
    SCHEMAS["verification-record"]: {
        **_COMMON_FIELDS,
        "verification_id": {"type": "str", "required": True, "non_empty": True},
        "execution_id": {"type": "str", "required": True, "non_empty": True},
        "state": {"type": "str", "required": True, "enum": VERIFICATION_STATES},
        "checks": {"type": "list_verification_check", "required": True},
        "verified_by": {"type": "str", "required": True, "non_empty": True},
    },
}

# Nested-object field sets, keyed by the "type" names used above.
NESTED_SCHEMAS = {
    "provenance": {
        "origin": {"type": "str", "required": True, "enum": ORIGINS},
        "origin_component": {"type": "str", "required": True, "enum": ORIGIN_COMPONENTS},
        "derived_from": {"type": "list_ref", "required": True},
    },
    "evidence_ref": {
        "pointer": {"type": "str", "required": True, "non_empty": True},
        "source_kind": {"type": "str", "required": True, "enum": SOURCE_KINDS},
        "relevance": {"type": "number", "required": True, "min": 0, "max": 1},
        "reliability": {"type": "number", "required": True, "min": 0, "max": 1},
    },
    "action_payload": {
        "action_type": {"type": "str", "required": True, "non_empty": True},
        "parameters": {"type": "dict", "required": True},
        "target": {"type": "str_or_null", "required": True},
        "classification_hint": {"type": "str_or_null", "required": False},
        "reversibility_claim": {"type": "str_or_null", "required": False},
    },
    "ref": {
        "record_id": {"type": "record_id", "required": True},
        "record_hash": {"type": "hash", "required": True},
    },
    "equivalence": {
        "action_type_equal": {"type": "bool", "required": True},
        "parameters_equal": {"type": "bool", "required": True},
        "target_equal": {"type": "bool", "required": True},
    },
    "invariant_check_core": {
        "check_id": {"type": "str", "required": True, "non_empty": True},
        "invariant_id": {"type": "str", "required": True, "non_empty": True},
        "result": {"type": "str", "required": True, "enum": ("pass", "fail")},
        "detail": {"type": "str", "required": True},
    },
    "verification_check": {
        "name": {"type": "str", "required": True, "non_empty": True},
        "result": {"type": "bool_or_passfail", "required": True},
        "detail": {"type": "str", "required": True},
    },
    "divergence_point": {
        "field": {"type": "str", "required": True, "non_empty": True},
        "value_a": {"type": "json_value", "required": True},
        "value_b": {"type": "json_value", "required": True},
    },
}

__all__ = [
    "SCHEMA_VERSION",
    "SCHEMAS",
    "SCHEMA_TO_KIND",
    "SCHEMA_FIELDS",
    "NESTED_SCHEMAS",
    "KIND_ORIGIN",
    "ORIGINS",
    "ORIGIN_COMPONENTS",
    "HEMISPHERES",
    "SOURCE_KINDS",
    "TIERS",
    "DECISION_STATES",
    "DELIBERATION_OUTCOMES",
    "AUTHORIZATION_RESULTS",
    "EXECUTION_STATES",
    "VERIFICATION_STATES",
    "DECISION_KINDS",
    "OPERATOR_NAME",
    "RECORD_ID_RE",
    "HASH_RE",
    "utc_now_iso",
    "new_record_id",
    "canonical_hemisphere_id",
    "derived_ref",
    "Provenance",
    "EvidenceRef",
    "ActionPayload",
    "InterpretationRecord",
    "DeliberationRecord",
    "InvariantCheckRecord",
    "AuthorizationRecord",
    "OperatorAuthorizationRecord",
    "OperatorResolutionRecord",
    "DecisionRecord",
    "ExecutionRecord",
    "VerificationRecord",
]
