"""Phase 4, step 4.2 tests — serialization boundary (PHASE4-SPEC-001 §5).

Covers AT-12 (serialization round-trip) and AT-13 (provenance
separation) at the 4.1/4.2 scope, and the §5.4 rejection codes (the
serialization half of AT-06; full AT-06 needs intake, step 4.3).
Stdlib unittest only.
"""

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neurai.governance.records import (
    ActionPayload,
    DeliberationRecord,
    EvidenceRef,
    InterpretationRecord,
    Provenance,
)
from neurai.governance.serialization import (
    ERR_FIELD_MISSING,
    ERR_FIELD_RANGE,
    ERR_FIELD_TYPE,
    ERR_FIELD_UNKNOWN,
    ERR_SCHEMA_KIND,
    ERR_SCHEMA_MAJOR,
    ERR_UNPARSEABLE,
    InMemoryStore,
    SerializationError,
    canonical_bytes,
    deserialize,
    record_hash,
    serialize,
)
# Helpers live in test_phase4_records; unittest discover runs with
# tests/ on the path, so this import works under the runner.
from test_phase4_records import TS, _id, make_deliberation, make_interp


def _assert_code(testcase, codes, expected):
    testcase.assertIn(expected, codes, f"expected {expected} in {codes}")


class TestRoundTripAT12(unittest.TestCase):
    def test_interpretation_round_trip_with_disagreement_and_empty_lists(self):
        rec = make_interp(
            evidence=[
                EvidenceRef(pointer="metric_01", source_kind="instrument_reading", relevance=0.9, reliability=0.8),
                EvidenceRef(pointer="metric_02", source_kind="document", relevance=0.4, reliability=0.6),
            ]
        )
        data = rec.to_dict()
        self.assertEqual(deserialize(serialize(data)), data)

    def test_deliberation_round_trip(self):
        a = make_interp("HEM-A", "a", "aggressive")
        b = make_interp("HEM-B", "b", "conservative")
        d = make_deliberation(a, b)
        data = d.to_dict()
        self.assertEqual(deserialize(serialize(data)), data)

    def test_canonical_bytes_stable_across_key_order(self):
        rec = make_interp()
        data = rec.to_dict()
        shuffled = dict(reversed(list(data.items())))
        # Nested dicts also reversed, to prove sort_keys, not input order.
        shuffled["provenance"] = dict(reversed(list(data["provenance"].items())))
        self.assertEqual(serialize(data), serialize(shuffled))
        self.assertEqual(record_hash(data), record_hash(shuffled))

    def test_record_hash_excludes_envelope(self):
        rec = make_interp()
        data = rec.to_dict()
        expected = hashlib.sha256(canonical_bytes(data)).hexdigest()
        self.assertEqual(record_hash(data), expected)
        envelope_hash = hashlib.sha256(serialize(data)).hexdigest()
        self.assertNotEqual(record_hash(data), envelope_hash)

    def test_non_ascii_is_utf8_not_escaped(self):
        rec = make_interp()
        data = rec.to_dict()
        data["intent"] = "café — modify_system_config"
        # Re-validate via a fresh record dict: serialize must not escape.
        blob = serialize(data)
        self.assertIn("café".encode("utf-8"), blob)
        self.assertNotIn(b"\\u00e9", blob)
        self.assertEqual(deserialize(blob)["intent"], "café — modify_system_config")


class TestProvenanceSeparationAT13(unittest.TestCase):
    def test_originals_hash_identical_after_store_and_fetch(self):
        store = InMemoryStore()
        a = make_interp("HEM-A", "a", "aggressive")
        b = make_interp("HEM-B", "b", "conservative")
        hash_a = record_hash(a.to_dict())
        hash_b = record_hash(b.to_dict())
        store.store(a.to_dict())
        store.store(b.to_dict())
        self.assertEqual(record_hash(store.fetch(a.record_id)), hash_a)
        self.assertEqual(record_hash(store.fetch(b.record_id)), hash_b)
        # Fetching again does not change the stored original (P-1).
        self.assertEqual(record_hash(store.fetch(a.record_id)), hash_a)

    def test_derived_record_names_both_originals(self):
        a = make_interp("HEM-A", "a", "aggressive")
        b = make_interp("HEM-B", "b", "conservative")
        d = make_deliberation(a, b)
        restored = deserialize(serialize(d.to_dict()))
        self.assertEqual(restored["provenance"]["origin"], "cc_derived")
        self.assertEqual(
            restored["provenance"]["derived_from"],
            [
                {"record_id": a.record_id, "record_hash": record_hash(a.to_dict())},
                {"record_id": b.record_id, "record_hash": record_hash(b.to_dict())},
            ],
        )
        # Originals are unchanged by deriving from them.
        self.assertEqual(restored["interpretations"][0], a.to_dict())
        self.assertEqual(restored["interpretations"][1], b.to_dict())


class TestRejectionCodes(unittest.TestCase):
    def _expect(self, data, expected_code):
        if isinstance(data, dict):
            with self.assertRaises(SerializationError) as ctx:
                serialize(data)
        else:
            with self.assertRaises(SerializationError) as ctx:
                deserialize(data)
        _assert_code(self, ctx.exception.codes, expected_code)

    def test_missing_provenance_field(self):
        data = make_interp().to_dict()
        del data["provenance"]
        self._expect(data, ERR_FIELD_MISSING)

    def test_wrong_field_type(self):
        data = make_interp().to_dict()
        data["confidence"] = "high"
        self._expect(data, ERR_FIELD_TYPE)

    def test_unknown_extra_field(self):
        data = make_interp().to_dict()
        data["extra_field"] = "surprise"
        self._expect(data, ERR_FIELD_UNKNOWN)

    def test_wrong_major_schema_version(self):
        data = make_interp().to_dict()
        data["schema_version"] = "2.0.0"
        self._expect(data, ERR_SCHEMA_MAJOR)

    def test_unknown_schema_kind(self):
        data = make_interp().to_dict()
        data["schema"] = "neurai.cc/no-such-record"
        self._expect(data, ERR_SCHEMA_KIND)

    def test_out_of_range_confidence(self):
        data = make_interp().to_dict()
        data["confidence"] = 1.5
        self._expect(data, ERR_FIELD_RANGE)

    def test_unparseable_bytes(self):
        self._expect(b"\x00\x01not json", ERR_UNPARSEABLE)
        self._expect(b'{"schema": ', ERR_UNPARSEABLE)

    def test_envelope_mismatch(self):
        data = make_interp().to_dict()
        blob = json.loads(serialize(data))
        blob["schema"] = "neurai.cc/deliberation-record"
        self._expect(canonical_bytes(blob), ERR_SCHEMA_KIND)

    def test_producer_must_not_supply_hash(self):
        # D-10: a record dict carrying its own record_hash is an unknown field.
        data = make_interp().to_dict()
        data["record_hash"] = "0" * 64
        self._expect(data, ERR_FIELD_UNKNOWN)

    def test_nested_unknown_field(self):
        data = make_interp().to_dict()
        data["evidence"][0]["extra"] = 1
        self._expect(data, ERR_FIELD_UNKNOWN)

    def test_no_repair_or_defaulting(self):
        # A missing required field is rejected, not defaulted.
        data = make_interp().to_dict()
        del data["intent"]
        with self.assertRaises(SerializationError) as ctx:
            serialize(data)
        self.assertIn(ERR_FIELD_MISSING, ctx.exception.codes)
        # serialize must not have mutated the input.
        self.assertNotIn("intent", data)


class TestStore(unittest.TestCase):
    def test_store_fetch_round_trip(self):
        store = InMemoryStore()
        rec = make_interp()
        self.assertEqual(store.store(rec.to_dict()), rec.record_id)
        self.assertEqual(store.fetch(rec.record_id), rec.to_dict())
        self.assertEqual(len(store), 1)

    def test_duplicate_store_rejected(self):
        store = InMemoryStore()
        rec = make_interp()
        store.store(rec.to_dict())
        with self.assertRaises(ValueError):
            store.store(rec.to_dict())

    def test_fetch_missing_raises(self):
        store = InMemoryStore()
        with self.assertRaises(KeyError):
            store.fetch("interpretation-record-" + "0" * 32)

    def test_fetch_returns_copy(self):
        store = InMemoryStore()
        rec = make_interp()
        store.store(rec.to_dict())
        fetched = store.fetch(rec.record_id)
        fetched["intent"] = "tampered"
        self.assertEqual(store.fetch(rec.record_id)["intent"], rec.intent)

    def test_tampered_stored_bytes_fail_closed_on_read(self):
        store = InMemoryStore()
        rec = make_interp()
        store.store(rec.to_dict())
        # Simulate stored corruption (test-only reach into the store).
        data, _hash = store._entries[rec.record_id]
        tampered = bytearray(data)
        tampered[-10] ^= 0x01
        store._entries[rec.record_id] = (bytes(tampered), _hash)
        with self.assertRaises(SerializationError):
            store.fetch(rec.record_id)


if __name__ == "__main__":
    unittest.main()
