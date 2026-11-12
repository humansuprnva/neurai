#!/bin/sh
# NeurAI OS — Phase-0 architecture test harness.
# Runs the contract validator, the record/config validator, and the
# invariant registry tests. Exit 0 only if all three pass.
set -e
cd "$(dirname "$0")"
echo "== validate_contract =="
python3 validate_contract.py
echo "== validate_records =="
python3 validate_records.py
echo "== test_invariants =="
python3 test_invariants.py
echo "PHASE-0 HARNESS: ALL PASS"
