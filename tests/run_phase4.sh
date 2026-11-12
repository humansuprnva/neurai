#!/bin/sh
# Phase 4 acceptance-test runner (PHASE4-SPEC-001 §12, step 4.9 in part).
# Runs only the Phase 4, steps 4.1/4.2 tests implemented so far.
# Stdlib unittest only; exits non-zero if any test fails.
set -e
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -p "test_phase4_*.py" -v
