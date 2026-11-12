"""Phase 4, step 4.1 tests — governance data structures (PHASE4-SPEC-001 §4).

Stdlib unittest only.  Synthetic hemisphere records; deterministic ids
and timestamps so hashes are stable.
"""

import copy
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neurai.governance import records
from neurai.governance.records import (
    ActionPayload,
    AuthorizationRecord,
    DecisionRecord,
    DeliberationRecord,
    EvidenceRef,
    ExecutionRecord,
    InterpretationRecord,
    InvariantCheckRecord,
    OperatorAuthorizationRecord,
    OperatorResolutionRecord,
    Provenance,
    VerificationRecord,
)
from neurai.governance.serialization import record_hash

TS = "2026-11-12T00:00:00Z"


def _id(kind, ch):
    return f"{kind}-{ch * 32}"


def make_payload(mode="aggressive"):
    return ActionPayload(
        action_type="write_config",
        parameters={"mode": mode},
        target="config/system.yaml",
        classification_hint="free",
    )


def make_interp(hemisphere="HEM-A", ch="a", mode="aggressive", evidence=None):
    return InterpretationRecord(
        record_id=_id("interpretation-record", ch),
        created_at=TS,
        provenance=Provenance(
            origin="hemisphere_original",
            origin_component=hemisphere,
            derived_from=[],
        ),
        hemisphere_id=hemisphere,
        task_reference="task-001",
        original_input_reference="input-001",
        intent="modify_system_config",
        semantic_payload=make_payload(mode),
        assumptions=["Load will remain stable"] if hemisphere == "HEM-A" else ["Traffic spike expected"],
        evidence=evidence
        if evidence is not None
        else [EvidenceRef(pointer="metric_01", source_kind="instrument_reading", relevance=0.9, reliability=0.8)],
        confidence=0.91 if hemisphere == "HEM-A" else 0.88,
        alternatives_considered=["do_nothing"] if hemisphere == "HEM-A" else ["throttle"],
        unresolved_questions=["Will load stay stable?"],
    )


def make_deliberation(a, b):
    refs = [
        {"record_id": a.record_id, "record_hash": record_hash(a.to_dict())},
        {"record_id": b.record_id, "record_hash": record_hash(b.to_dict())},
    ]
    return DeliberationRecord(
        record_id=_id("deliberation-record", "d"),
        created_at=TS,
        provenance=Provenance(origin="cc_derived", origin_component="CC", derived_from=refs),
        interpretations=[a, b],
        equivalence={"action_type_equal": True, "parameters_equal": False, "target_equal": True},
        agreement_points=["action_type"],
        divergence_points=[
            {"field": "parameters.mode", "value_a": "aggressive", "value_b": "conservative"}
        ],
        evidence_profile={
            "HEM-A": [e.to_dict() for e in a.evidence],
            "HEM-B": [e.to_dict() for e in b.evidence],
        },
        uncertainty={
            "confidence": {"HEM-A": a.confidence, "HEM-B": b.confidence},
            "unresolved_questions": ["Will load stay stable?"],
        },
        outcome="divergence_material",
        selected_payload=None,
        cc_reasoning="Payloads differ in parameters.mode; no exit from material divergence inside deliberation.",
    )


class TestInterpretationRecord(unittest.TestCase):
    def test_fields_and_canonical_hemisphere_mapping(self):
        # D-12: supplied "hem_a" maps to HEM-A.
        rec = make_interp()
        rec2 = InterpretationRecord(
            record_id=_id("interpretation-record", "e"),
            created_at=TS,
            provenance=Provenance(origin="hemisphere_original", origin_component="HEM-A", derived_from=[]),
            hemisphere_id="hem_a",
            task_reference="task-001",
            original_input_reference="input-001",
            intent="modify_system_config",
            semantic_payload=None,
            assumptions=[],
            evidence=[],
            confidence=0.5,
            alternatives_considered=[],
        )
        self.assertEqual(rec.hemisphere_id, "HEM-A")
        self.assertEqual(rec2.hemisphere_id, "HEM-A")
        self.assertIsNone(rec2.semantic_payload)  # null = interpretation only
        self.assertEqual(rec.schema, "neurai.cc/interpretation-record")
        self.assertEqual(rec.schema_version, "1.0.0")
        self.assertEqual(rec.record_id, _id("interpretation-record", "a"))

    def test_to_dict_from_dict_round_trip(self):
        rec = make_interp()
        self.assertEqual(InterpretationRecord.from_dict(rec.to_dict()).to_dict(), rec.to_dict())

    def test_evidence_preserved_verbatim_no_aggregate(self):
        # D-02: evidence is preserved as structured refs; this module
        # computes no count/score aggregate of any kind.
        rec = make_interp(
            evidence=[
                EvidenceRef(pointer="metric_01", source_kind="instrument_reading", relevance=0.2, reliability=0.3),
                EvidenceRef(pointer="metric_02", source_kind="document", relevance=0.9, reliability=0.9),
            ]
        )
        self.assertEqual(len(rec.evidence), 2)
        self.assertEqual(rec.evidence[0].pointer, "metric_01")
        self.assertEqual(rec.evidence[1].relevance, 0.9)
        for name in ("evidence_score", "evidence_count", "aggregate_evidence", "stronger_proposal"):
            self.assertFalse(hasattr(records, name), name)

    def test_confidence_range_enforced(self):
        with self.assertRaises(ValueError):
            InterpretationRecord(
                record_id=_id("interpretation-record", "f"),
                created_at=TS,
                provenance=Provenance(origin="hemisphere_original", origin_component="HEM-A", derived_from=[]),
                hemisphere_id="HEM-A",
                task_reference="t",
                original_input_reference="i",
                intent="x",
                semantic_payload=None,
                assumptions=[],
                evidence=[],
                confidence=1.5,
                alternatives_considered=[],
            )

    def test_frozen_immutability_P1(self):
        rec = make_interp()
        with self.assertRaises(FrozenInstanceError):
            rec.intent = "changed"


class TestProvenance(unittest.TestCase):
    def test_original_must_have_empty_derived_from(self):
        with self.assertRaises(ValueError):
            Provenance(
                origin="hemisphere_original",
                origin_component="HEM-A",
                derived_from=[{"record_id": _id("interpretation-record", "a"), "record_hash": "0" * 64}],
            )

    def test_derived_must_name_sources(self):
        with self.assertRaises(ValueError):
            Provenance(origin="cc_derived", origin_component="CC", derived_from=[])

    def test_origin_component_consistency(self):
        with self.assertRaises(ValueError):
            Provenance(origin="cc_derived", origin_component="HEM-A", derived_from=[
                {"record_id": _id("interpretation-record", "a"), "record_hash": "0" * 64}
            ])
        with self.assertRaises(ValueError):
            Provenance(origin="operator_record", origin_component="CC", derived_from=[])


class TestActionPayload(unittest.TestCase):
    def test_hints_are_recorded_not_authoritative(self):
        # D-03: the hint is a field on the payload; there is no tier field.
        payload = make_payload()
        d = payload.to_dict()
        self.assertEqual(d["classification_hint"], "free")
        self.assertNotIn("derived_tier", d)
        self.assertNotIn("tier", d)


class TestDeliberationRecord(unittest.TestCase):
    def test_embeds_both_originals_by_value(self):
        a = make_interp("HEM-A", "a", "aggressive")
        b = make_interp(
            "HEM-B",
            "b",
            "conservative",
            evidence=[EvidenceRef(pointer="metric_02", source_kind="instrument_reading", relevance=0.8, reliability=0.7),
                      EvidenceRef(pointer="metric_03", source_kind="document", relevance=0.6, reliability=0.9)],
        )
        d = make_deliberation(a, b)
        self.assertEqual(d.outcome, "divergence_material")
        self.assertIsNone(d.selected_payload)
        self.assertEqual([i.to_dict() for i in d.interpretations], [a.to_dict(), b.to_dict()])
        # Verbatim evidence profile and uncertainty (INV-009, INV-010).
        self.assertEqual(d.evidence_profile["HEM-A"][0]["pointer"], "metric_01")
        self.assertEqual(d.evidence_profile["HEM-B"][1]["pointer"], "metric_03")
        self.assertEqual(d.uncertainty["confidence"], {"HEM-A": 0.91, "HEM-B": 0.88})
        self.assertTrue(d.cc_reasoning)
        # P-2: derived_from names both originals, id + hash.
        self.assertEqual(
            d.provenance.derived_from,
            [
                {"record_id": a.record_id, "record_hash": record_hash(a.to_dict())},
                {"record_id": b.record_id, "record_hash": record_hash(b.to_dict())},
            ],
        )
        self.assertEqual(DeliberationRecord.from_dict(d.to_dict()).to_dict(), d.to_dict())

    def test_cc_reasoning_required_non_empty(self):
        a = make_interp("HEM-A", "a", "aggressive")
        b = make_interp("HEM-B", "b", "conservative")
        with self.assertRaises(ValueError):
            d = make_deliberation(a, b)
            DeliberationRecord(
                record_id=_id("deliberation-record", "e"),
                created_at=TS,
                provenance=d.provenance,
                interpretations=[a, b],
                equivalence={"action_type_equal": True, "parameters_equal": False, "target_equal": True},
                agreement_points=[],
                divergence_points=[],
                evidence_profile={"HEM-A": [], "HEM-B": []},
                uncertainty={"confidence": {"HEM-A": 0.91, "HEM-B": 0.88}, "unresolved_questions": []},
                outcome="divergence_material",
                selected_payload=None,
                cc_reasoning="",
            )


class TestOtherRecords(unittest.TestCase):
    def test_decision_record_defaults_A1_A2_A3(self):
        a = make_interp()
        b = make_interp("HEM-B", "b", "conservative")
        deliberation = make_deliberation(a, b)
        decision = DecisionRecord(
            record_id=_id("decision-record", "1"),
            created_at=TS,
            provenance=Provenance(
                origin="cc_derived",
                origin_component="CC",
                derived_from=[{"record_id": deliberation.record_id, "record_hash": record_hash(deliberation.to_dict())}],
            ),
            subject="write_config divergence",
            rationale="Material divergence; awaiting new records or operator resolution.",
            state="DELIBERATION_REQUIRED",
            deliberation_ref={"record_id": deliberation.record_id, "record_hash": record_hash(deliberation.to_dict())},
            invariant_checks=[],
        )
        # A1: decision_id defaults to record_id; A2: timestamp == created_at.
        self.assertEqual(decision.decision_id, decision.record_id)
        self.assertEqual(decision.timestamp, TS)
        # A3: no tier yet — null, not invented.
        self.assertIsNone(decision.authority_tier)
        self.assertEqual(decision.state, "DELIBERATION_REQUIRED")

    def test_authorization_record_hint_disagreement_recorded(self):
        auth = AuthorizationRecord(
            record_id=_id("authorization-record", "2"),
            created_at=TS,
            provenance=Provenance(
                origin="cc_derived",
                origin_component="CC",
                derived_from=[{"record_id": _id("decision-record", "1"), "record_hash": "0" * 64}],
            ),
            action_type="write_config",
            target="config/system.yaml",
            derived_tier="gated_irreversible",
            policy_rule="actions.write_config",
            policy_version="1.0.0",
            policy_hash="1" * 64,
            hint_received="free",
            hint_agrees=False,
            result="operator_required",
        )
        self.assertEqual(auth.derived_tier, "gated_irreversible")
        self.assertFalse(auth.hint_agrees)

    def test_operator_records(self):
        grant = OperatorAuthorizationRecord(
            record_id=_id("operator-authorization-record", "3"),
            created_at=TS,
            decision_id=_id("decision-record", "1"),
            action_type="write_config",
            granted=True,
            note="Approved.",
        )
        self.assertEqual(grant.operator, "Levi Nord")
        with self.assertRaises(ValueError):
            OperatorAuthorizationRecord(
                record_id=_id("operator-authorization-record", "4"),
                created_at=TS,
                decision_id=_id("decision-record", "1"),
                action_type="write_config",
                granted=True,
                operator="Someone Else",
                note="",
            )
        resolution = OperatorResolutionRecord(
            record_id=_id("operator-resolution-record", "5"),
            created_at=TS,
            decision_id=_id("decision-record", "1"),
            chosen_payload=make_payload("conservative"),
            note="Choose the conservative mode.",
        )
        self.assertEqual(resolution.chosen_payload.parameters["mode"], "conservative")

    def test_invariant_check_core(self):
        check = InvariantCheckRecord(
            record_id=_id("invariant-check-record", "6"),
            created_at=TS,
            provenance=Provenance(
                origin="cc_derived",
                origin_component="CC",
                derived_from=[{"record_id": _id("decision-record", "1"), "record_hash": "0" * 64}],
            ),
            invariant_id="INV-002",
            result="pass",
            detail="Both originals embedded by value.",
        )
        self.assertEqual(check.core_dict(), {
            "check_id": check.record_id,
            "invariant_id": "INV-002",
            "result": "pass",
            "detail": "Both originals embedded by value.",
        })

    def test_execution_and_verification_records(self):
        execution = ExecutionRecord(
            record_id=_id("execution-record", "7"),
            created_at=TS,
            provenance=Provenance(origin="executor_report", origin_component="EXECUTOR", derived_from=[]),
            decision_id=_id("decision-record", "1"),
            executor_id="executor-001",
            state="succeeded",
            started_at=TS,
            finished_at=TS,
            rollback_reference="backup-001",
            result_summary="Config written and re-read.",
            error=None,
        )
        self.assertEqual(execution.execution_id, execution.record_id)
        verification = VerificationRecord(
            record_id=_id("verification-record", "8"),
            created_at=TS,
            provenance=Provenance(origin="executor_report", origin_component="EXECUTOR", derived_from=[]),
            execution_id=execution.execution_id,
            state="verified",
            checks=[{"name": "re-read config", "result": True, "detail": "Matches canonical content."}],
            verified_by="executor-001",
        )
        self.assertEqual(verification.state, "verified")
        # COMMITTED asserts nothing about execution (D-01): the tuple is
        # carried by separate records, not collapsed into one field.
        self.assertNotIn("executed", DecisionRecord.__dataclass_fields__)


if __name__ == "__main__":
    unittest.main()
