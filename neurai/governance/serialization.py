"""NeurAI Phase 4, step 4.2 — the serialization boundary.

Implements PHASE4-SPEC-001 v1.0.0, §5.  Stdlib only.

The boundary sits between every pair of parties that exchange governance
records.  Its job is transport with validation: it proves that what
arrived is exactly what was sent, in a format both sides agree on.

It makes no decisions.  It does not normalize away disagreements, drop
uncertainty fields, coerce values for convenience, reconcile alternatives,
or otherwise edit content.  A boundary that edits would be a second,
hidden governance layer, and there is exactly one governance layer.

Canonical format (§5.2):
  * JSON, UTF-8, one record per payload.
  * Envelope: {"schema": <kind>, "schema_version": "1.0.0", "record": {...}}.
  * Canonical form: json.dumps(obj, sort_keys=True,
    separators=(",", ":"), ensure_ascii=False), encoded as UTF-8.
  * record_hash = hex SHA-256 of the canonical bytes of the record object
    alone (envelope excluded).  Producers never supply hashes; the
    boundary computes them (D-10).

Rejection (§5.4) is total: a payload is either a valid record,
byte-for-byte the sender's content, or it does not cross.  There is no
partial repair, no "accept and fix", no defaulting of missing fields.

Storage neutrality (§5.5): ``InMemoryStore`` is a test double implementing
``store(record) -> record_id`` and ``fetch(record_id) -> record`` with
validation on write and on read.  SQLite (Phase 2), a filesystem store,
or any other store are legitimate implementations of the same two
functions; nothing in steps 4.3 onward may know which store is live.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime

from .records import (
    HASH_RE,
    KIND_ORIGIN,
    NESTED_SCHEMAS,
    RECORD_ID_RE,
    SCHEMAS,
    SCHEMA_FIELDS,
    SCHEMA_TO_KIND,
    SCHEMA_VERSION,
)

# Error codes, §5.4.
ERR_SCHEMA_MAJOR = "ERR_SCHEMA_MAJOR"
ERR_SCHEMA_KIND = "ERR_SCHEMA_KIND"
ERR_FIELD_MISSING = "ERR_FIELD_MISSING"
ERR_FIELD_UNKNOWN = "ERR_FIELD_UNKNOWN"
ERR_FIELD_TYPE = "ERR_FIELD_TYPE"
ERR_FIELD_RANGE = "ERR_FIELD_RANGE"
ERR_UNPARSEABLE = "ERR_UNPARSEABLE"


class SerializationError(Exception):
    """A record failed the §5.4 boundary validation.

    ``codes`` is a list of machine-readable error codes, in the order the
    problems were found, deduplicated.  ``message`` is human-readable and
    names the offending field path.
    """

    def __init__(self, codes, message: str):
        if isinstance(codes, str):
            codes = [codes]
        self.codes = list(dict.fromkeys(codes))
        self.message = message
        super().__init__(f"{self.codes}: {message}")


# ---------------------------------------------------------------------------
# Canonical form and hashing (§5.2)
# ---------------------------------------------------------------------------


def canonical_bytes(obj) -> bytes:
    """The only form ever hashed or stored (§5.2)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def record_hash(record: dict) -> str:
    """SHA-256 over the canonical form of the record object alone.

    The envelope is excluded.  The record is validated first: hashing an
    invalid record would give a meaningless anchor.  Producers never
    supply hashes; the boundary computes them.
    """
    validate_record(record)
    return hashlib.sha256(canonical_bytes(record)).hexdigest()


# ---------------------------------------------------------------------------
# Validation (§5.3, §5.4)
# ---------------------------------------------------------------------------


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_json_value(value) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    if _is_number(value):
        return True
    if isinstance(value, list):
        return all(_is_json_value(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_value(v) for k, v in value.items())
    return False


def _parse_iso8601(value: str):
    """Parse an ISO-8601 timestamp; return None if unparseable or naive."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _check_version(version) -> list:
    """§5.4 versioning.  Current is 1.0.0: major must be 1, minor must
    be 0; any patch is accepted.  A newer minor is a newer schema."""
    codes = []
    if not isinstance(version, str):
        return [ERR_FIELD_TYPE]
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return [ERR_FIELD_TYPE]
    major, minor, _patch = (int(g) for g in match.groups())
    if major != 1:
        codes.append(ERR_SCHEMA_MAJOR)
    elif minor != 0:
        codes.append(ERR_SCHEMA_MAJOR)
    return codes


class _Validator:
    """Collects §5.4 codes while walking a record dict.  No repair."""

    def __init__(self):
        self.codes: list = []

    def add(self, code: str):
        if code not in self.codes:
            self.codes.append(code)

    def check_fields(self, obj, fields: dict, path: str):
        if not isinstance(obj, dict):
            self.add(ERR_FIELD_TYPE)
            return
        for name, spec in fields.items():
            if spec.get("required") and name not in obj:
                self.add(ERR_FIELD_MISSING)
        for name in obj:
            if name not in fields:
                self.add(ERR_FIELD_UNKNOWN)
        for name, spec in fields.items():
            if name not in obj:
                continue
            self.check_value(obj[name], spec, f"{path}.{name}" if path else name)

    def check_value(self, value, spec: dict, path: str):
        type_name = spec["type"]
        if type_name == "str":
            if not isinstance(value, str):
                self.add(ERR_FIELD_TYPE)
                return
            if spec.get("non_empty") and not value:
                self.add(ERR_FIELD_RANGE)
            if "enum" in spec and value not in spec["enum"]:
                self.add(ERR_FIELD_RANGE)
        elif type_name == "str_or_null":
            if value is None:
                return
            if not isinstance(value, str):
                self.add(ERR_FIELD_TYPE)
                return
            if spec.get("non_empty") and not value:
                self.add(ERR_FIELD_RANGE)
            if "enum" in spec and value not in spec["enum"]:
                self.add(ERR_FIELD_RANGE)
        elif type_name == "number":
            if not _is_number(value):
                self.add(ERR_FIELD_TYPE)
                return
            if "min" in spec and value < spec["min"]:
                self.add(ERR_FIELD_RANGE)
            if "max" in spec and value > spec["max"]:
                self.add(ERR_FIELD_RANGE)
        elif type_name == "bool":
            if not isinstance(value, bool):
                self.add(ERR_FIELD_TYPE)
        elif type_name == "bool_or_null":
            if value is not None and not isinstance(value, bool):
                self.add(ERR_FIELD_TYPE)
        elif type_name == "bool_or_passfail":
            if not (isinstance(value, bool) or value in ("pass", "fail")):
                self.add(ERR_FIELD_TYPE)
        elif type_name == "record_id":
            if not isinstance(value, str) or not RECORD_ID_RE.match(value):
                self.add(ERR_FIELD_TYPE)
        elif type_name == "hash":
            if not isinstance(value, str) or not HASH_RE.match(value):
                self.add(ERR_FIELD_TYPE)
        elif type_name == "iso8601":
            if _parse_iso8601(value) is None:
                self.add(ERR_FIELD_TYPE)
        elif type_name == "iso8601_or_null":
            if value is not None and _parse_iso8601(value) is None:
                self.add(ERR_FIELD_TYPE)
        elif type_name == "dict":
            if not isinstance(value, dict):
                self.add(ERR_FIELD_TYPE)
                return
            if not _is_json_value(value):
                self.add(ERR_FIELD_TYPE)
        elif type_name == "json_value":
            if not _is_json_value(value):
                self.add(ERR_FIELD_TYPE)
        elif type_name == "list_str":
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                self.add(ERR_FIELD_TYPE)
        elif type_name == "list_ref":
            if not isinstance(value, list):
                self.add(ERR_FIELD_TYPE)
                return
            for i, entry in enumerate(value):
                self.check_fields(entry, NESTED_SCHEMAS["ref"], f"{path}[{i}]")
        elif type_name == "list_evidence":
            if not isinstance(value, list):
                self.add(ERR_FIELD_TYPE)
                return
            for i, entry in enumerate(value):
                self.check_fields(entry, NESTED_SCHEMAS["evidence_ref"], f"{path}[{i}]")
        elif type_name == "list_interpretation":
            if not isinstance(value, list):
                self.add(ERR_FIELD_TYPE)
                return
            if len(value) != 2:
                self.add(ERR_FIELD_RANGE)
            for i, entry in enumerate(value):
                self.check_record(entry, f"{path}[{i}]")
        elif type_name == "list_divergence":
            if not isinstance(value, list):
                self.add(ERR_FIELD_TYPE)
                return
            for i, entry in enumerate(value):
                self.check_fields(entry, NESTED_SCHEMAS["divergence_point"], f"{path}[{i}]")
        elif type_name == "list_invariant_core":
            if not isinstance(value, list):
                self.add(ERR_FIELD_TYPE)
                return
            for i, entry in enumerate(value):
                self.check_fields(entry, NESTED_SCHEMAS["invariant_check_core"], f"{path}[{i}]")
        elif type_name == "list_verification_check":
            if not isinstance(value, list):
                self.add(ERR_FIELD_TYPE)
                return
            for i, entry in enumerate(value):
                self.check_fields(entry, NESTED_SCHEMAS["verification_check"], f"{path}[{i}]")
        elif type_name == "provenance":
            self._check_provenance(value, path)
        elif type_name == "evidence_ref":
            self.check_fields(value, NESTED_SCHEMAS["evidence_ref"], path)
        elif type_name == "action_payload":
            self.check_fields(value, NESTED_SCHEMAS["action_payload"], path)
        elif type_name == "action_payload_or_null":
            if value is None:
                return
            self.check_fields(value, NESTED_SCHEMAS["action_payload"], path)
        elif type_name == "equivalence":
            self.check_fields(value, NESTED_SCHEMAS["equivalence"], path)
        elif type_name == "evidence_profile":
            if not isinstance(value, dict) or set(value) != {"HEM-A", "HEM-B"}:
                self.add(ERR_FIELD_TYPE)
                return
            for hemi, refs in value.items():
                if not isinstance(refs, list):
                    self.add(ERR_FIELD_TYPE)
                    continue
                for i, entry in enumerate(refs):
                    self.check_fields(entry, NESTED_SCHEMAS["evidence_ref"], f"{path}.{hemi}[{i}]")
        elif type_name == "uncertainty":
            if not isinstance(value, dict) or set(value) != {"confidence", "unresolved_questions"}:
                self.add(ERR_FIELD_TYPE)
                return
            conf = value["confidence"]
            if not isinstance(conf, dict) or set(conf) != {"HEM-A", "HEM-B"}:
                self.add(ERR_FIELD_TYPE)
            else:
                for hemi, number in conf.items():
                    if not _is_number(number) or not 0 <= number <= 1:
                        self.add(ERR_FIELD_RANGE)
            if not isinstance(value["unresolved_questions"], list) or not all(
                isinstance(x, str) for x in value["unresolved_questions"]
            ):
                self.add(ERR_FIELD_TYPE)
        elif type_name == "ref":
            self.check_fields(value, NESTED_SCHEMAS["ref"], path)
        elif type_name == "ref_or_null":
            if value is None:
                return
            self.check_fields(value, NESTED_SCHEMAS["ref"], path)
        else:  # pragma: no cover - unknown type name is a programmer error
            raise ValueError(f"unknown field type in schema table: {type_name!r}")

    def _check_provenance(self, value, path: str):
        self.check_fields(value, NESTED_SCHEMAS["provenance"], path)
        if not isinstance(value, dict):
            return
        origin = value.get("origin")
        component = value.get("origin_component")
        # Origin/component consistency (resolution A5, records.py).
        allowed = {
            "hemisphere_original": ("HEM-A", "HEM-B"),
            "cc_derived": ("CC",),
            "executor_report": ("EXECUTOR",),
            "operator_record": ("OPERATOR",),
        }
        if origin in allowed and component not in allowed[origin]:
            self.add(ERR_FIELD_RANGE)
        derived = value.get("derived_from")
        if isinstance(derived, list):
            if origin == "hemisphere_original" and derived:
                self.add(ERR_FIELD_RANGE)
            if origin == "cc_derived" and not derived:
                self.add(ERR_FIELD_RANGE)

    def check_record(self, record, path: str = ""):
        """Validate one record dict against its kind's closed field set."""
        if not isinstance(record, dict):
            self.add(ERR_FIELD_TYPE)
            return
        schema = record.get("schema")
        if not isinstance(schema, str):
            self.add(ERR_FIELD_MISSING)
            return
        if schema not in SCHEMA_FIELDS:
            self.add(ERR_SCHEMA_KIND)
            return
        fields = SCHEMA_FIELDS[schema]
        self.check_fields(record, fields, path)
        # Envelope/record consistency for this kind.
        kind_slug = SCHEMA_TO_KIND[schema]
        record_id = record.get("record_id")
        if isinstance(record_id, str):
            prefix = f"{kind_slug}-"
            if not record_id.startswith(prefix):
                self.add(ERR_FIELD_TYPE)
        version = record.get("schema_version")
        if version is not None:
            for code in _check_version(version):
                self.add(code)
        # Provenance origin/component required by kind (records.py KIND_ORIGIN).
        provenance = record.get("provenance")
        if isinstance(provenance, dict):
            expected_origin, expected_component = KIND_ORIGIN[kind_slug]
            if provenance.get("origin") != expected_origin:
                self.add(ERR_FIELD_RANGE)
            if expected_component is not None and provenance.get("origin_component") != expected_component:
                self.add(ERR_FIELD_RANGE)
            # Interpretation records: component must equal hemisphere_id.
            if kind_slug == "interpretation-record":
                if provenance.get("origin_component") != record.get("hemisphere_id"):
                    self.add(ERR_FIELD_RANGE)


def validate_record(record: dict) -> None:
    """Validate a record dict; raise SerializationError on any failure.

    Rejection is total: no partial repair, no defaulting.
    """
    validator = _Validator()
    validator.check_record(record)
    if validator.codes:
        raise SerializationError(validator.codes, "record failed §5.4 validation")


# ---------------------------------------------------------------------------
# Boundary interface (§5.3)
# ---------------------------------------------------------------------------


def serialize(record: dict) -> bytes:
    """Validate, wrap in the envelope, return canonical bytes.

    Raises SerializationError on an invalid record.  The input dict is
    not mutated.
    """
    if not isinstance(record, dict):
        raise SerializationError(ERR_FIELD_TYPE, "record must be a dict")
    validate_record(record)
    envelope = {
        "schema": record["schema"],
        "schema_version": record["schema_version"],
        "record": copy.deepcopy(record),
    }
    return canonical_bytes(envelope)


def deserialize(data: bytes) -> dict:
    """Parse, check the envelope, validate against the kind's schema,
    return the record dict exactly as sent.

    Raises SerializationError.  The returned dict is a fresh copy; the
    sender's content is never edited.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise SerializationError(ERR_FIELD_TYPE, "serialized record must be bytes")
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError:
        raise SerializationError(ERR_UNPARSEABLE, "bytes are not valid UTF-8")
    try:
        envelope = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        raise SerializationError(ERR_UNPARSEABLE, "bytes are not valid JSON")
    if not isinstance(envelope, dict):
        raise SerializationError(ERR_UNPARSEABLE, "envelope must be a JSON object")
    if set(envelope) != {"schema", "schema_version", "record"}:
        codes = []
        for key in ("schema", "schema_version", "record"):
            if key not in envelope:
                codes.append(ERR_FIELD_MISSING)
        for key in envelope:
            if key not in ("schema", "schema_version", "record"):
                codes.append(ERR_FIELD_UNKNOWN)
        raise SerializationError(codes, "envelope must have exactly schema, schema_version, record")
    schema = envelope["schema"]
    if not isinstance(schema, str):
        raise SerializationError(ERR_FIELD_TYPE, "envelope schema must be a string")
    if schema not in SCHEMA_FIELDS:
        raise SerializationError(ERR_SCHEMA_KIND, f"unknown schema kind: {schema!r}")
    for code in _check_version(envelope["schema_version"]):
        raise SerializationError(code, "envelope schema_version is not accepted")
    record = envelope["record"]
    if not isinstance(record, dict):
        raise SerializationError(ERR_FIELD_TYPE, "envelope record must be an object")
    if record.get("schema") != schema:
        raise SerializationError(ERR_SCHEMA_KIND, "envelope schema does not match record schema")
    if record.get("schema_version") != envelope["schema_version"]:
        raise SerializationError(ERR_SCHEMA_MAJOR, "envelope schema_version does not match record schema_version")
    validate_record(record)
    return copy.deepcopy(record)


# ---------------------------------------------------------------------------
# Storage neutrality (§5.5) — in-memory test double
# ---------------------------------------------------------------------------


class InMemoryStore:
    """A storage-neutral test double for §5.5.

    Stores canonical bytes + record_hash; validation runs on write and
    on read (stored corruption is detected at read time and fails
    closed).  ``store`` returns the record_id; ``fetch`` returns a deep
    copy, so mutating a fetched record cannot alter what is stored
    (P-1).  A duplicate record_id is a store error (ValueError): an
    original is never silently overwritten.
    """

    def __init__(self):
        self._entries: dict = {}  # record_id -> (canonical bytes, record_hash)

    def store(self, record: dict) -> str:
        data = serialize(record)  # validates first
        record_id = record["record_id"]
        if record_id in self._entries:
            raise ValueError(f"duplicate record_id already stored: {record_id}")
        self._entries[record_id] = (data, record_hash(record))
        return record_id

    def fetch(self, record_id: str) -> dict:
        if record_id not in self._entries:
            raise KeyError(record_id)
        data, stored_hash = self._entries[record_id]
        record = deserialize(data)  # validates on the way out
        if record_hash(record) != stored_hash:
            raise SerializationError(
                ERR_FIELD_TYPE, f"stored record {record_id} failed hash verification on read"
            )
        return record

    def __contains__(self, record_id: str) -> bool:
        return record_id in self._entries

    def __len__(self) -> int:
        return len(self._entries)


__all__ = [
    "ERR_SCHEMA_MAJOR",
    "ERR_SCHEMA_KIND",
    "ERR_FIELD_MISSING",
    "ERR_FIELD_UNKNOWN",
    "ERR_FIELD_TYPE",
    "ERR_FIELD_RANGE",
    "ERR_UNPARSEABLE",
    "SerializationError",
    "canonical_bytes",
    "record_hash",
    "validate_record",
    "serialize",
    "deserialize",
    "InMemoryStore",
]
