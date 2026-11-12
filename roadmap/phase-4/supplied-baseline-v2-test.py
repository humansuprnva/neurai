# SUPPLIED BASELINE TEST — transcribed verbatim from Levi Nord's message
# of 2026-11-07 (Build NeurAI Integrated OS side chat). Tests the supplied
# baseline v2 (supplied-baseline-v2.py); it assumes the baseline's names
# are already in scope (the paste carried no imports), so run it
# concatenated after the baseline or from a module that imports it.
# Preserved as the seed of acceptance test AT-01 in the Phase 4
# implementation specification. Do not edit; the strengthened version
# lives with the Phase 4 test suite when implemented.

def test_cc_divergence_preservation():
    log = ProtectedAppendOnlyLog()
    governor = CorpusCallosumGovernor(log)

    prop_a = InterpretationRecord(
        hemisphere_id="hem_a",
        intent="modify_system_config",
        semantic_payload={"action_type": "write_config", "parameters": {"mode": "aggressive"}},
        assumptions=["Load will remain stable"],
        evidence_pointers=["metric_01"],
        confidence=0.91,
        alternatives_considered=["do_nothing"]
    )
    
    prop_b = InterpretationRecord(
        hemisphere_id="hem_b",
        intent="modify_system_config",
        semantic_payload={"action_type": "write_config", "parameters": {"mode": "conservative"}},
        assumptions=["Traffic spike expected"],
        evidence_pointers=["metric_02", "metric_03"],
        confidence=0.88,
        alternatives_considered=["throttle"]
    )

    result = governor.evaluate(prop_a, prop_b)
    
    # Assertions
    assert result.state == DecisionState.DELIBERATION_REQUIRED
    assert result.disagreement_vector is not None
    assert len(log._log) == 1
    print("Test passed: Divergence correctly routed to DELIBERATION_REQUIRED without data loss.")

if __name__ == "__main__":
    test_cc_divergence_preservation()
