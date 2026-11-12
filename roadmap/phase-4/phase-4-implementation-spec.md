# NeurAI Phase 4 Implementation Specification — Corpus Callosum Governance

- **Document:** PHASE4-SPEC-001, version 1.0.0
- **Date:** 2026-11-07
- **Status:** Approved structure (operator, 2026-11-07); ready for implementation
- **Governs:** Roadmap v1.1.0 PHASE-4 ("Corpus Callosum core"), as sequenced by the
  operator's build sequence of 2026-11-07 (§3 of this specification)
- **Authority chain:** `roadmap/neurai-roadmap-1.1.0.yaml` → ADR-001
  (`roadmap/decisions/ADR-001-capability-first-authority.yaml`) → Phase-0 contract
  (`architecture/contract/`, `architecture/registry/authority-tiers.yaml`) → this
  specification. Where this specification and the roadmap disagree, the roadmap
  wins and the disagreement is recorded as a deviation, per the roadmap's
  operating rules.
- **Baseline incorporated:** the operator-supplied CC implementations preserved
  verbatim in this directory (`supplied-baseline-v1.py`,
  `supplied-baseline-v2.py`, `supplied-baseline-v2-test.py`). §2 assesses them;
  every correction the operator directed on 2026-11-07 is traced to the section
  that implements it.

## 0. How to read this specification

- **MUST / MUST NOT** are requirements. **SHOULD** is a strong default that may be
  deviated from only with the deviation recorded. Nothing else in this document
  is optional colour.
- This specification is intended to be implementable **without inventing
  architectural policy**. Where a genuine policy choice is not settled here, it
  is listed in §15 as an open question (OQ) with the conservative interim
  behaviour the implementation MUST exhibit until the operator decides. An
  implementer who hits an unlisted gap stops and raises it; they do not fill it
  in silently.
- Phase 4 is **deterministic**. No decision in §§6–10 may depend on a language
  model's judgement, on wall-clock time, on randomness, or on the order in
  which equal inputs arrived. Timestamps are recorded as evidence; they are
  never decision inputs. (Roadmap §2: any reasoning engine attached to the CC
  is advisory only; the CC's enforcement core is deterministic.)
- Implementation language is Python 3, standard library only, consistent with
  the existing core. Module layout is specified in §13.

## 1. Scope and non-goals

**In scope.** The Corpus Callosum governance layer exactly as sequenced in §3:
governance records (4.1), the serialization boundary (4.2), interpretation
intake (4.3), the deliberation engine (4.4), invariant enforcement (4.5), the
authorization engine (4.6), the execution interface (4.7), the decision and
audit log (4.8), and structural validation (4.9).

**Sequencing note.** Roadmap v1.1.0 lists PHASE-4 as depending on PHASE-3
(hemispheres). The Phase-0 scaffolding already recorded the operative order —
Phase 0 → CC / permission layer → hemispheres — and the operator's 2026-11-07
sequence confirms it: governance is built first, against the record contract of
§4–§5, and exercised with synthetic hemisphere records. Consequence: nothing in
Phase 4 may assume a particular hemisphere implementation; hemispheres are
whatever produces valid InterpretationRecords through the serialization
boundary.

**Non-goals.**

- Hemisphere implementations (Phase 3), adaptive mode selection policy beyond
  recording the mode (Phase 5), and the full authority registry with delegated
  grants (Phase 6). Phase 4 loads tier/policy data; it does not build the
  Phase-6 registry UI or grant machinery.
- `verify_self_model` is a roadmap PHASE-4 deliverable that the operator's
  4.1–4.9 sequence does not cover. It is **deferred to a Phase 4 addendum**,
  explicitly, not silently dropped (§14).
- Shared-memory storage technology. Phase 2 owns the SQLite provenance store.
  §5 defines the record boundary so that the storage decision stays open; the
  boundary MUST NOT be designed around SQLite or any other specific store.
- Runtime distribution across machines. Phase 4 is single-node.
- Replacing the v2.0.0 core's tool set. Phase 4 governs actions; which actions
  exist is the core's business, classified through the policy of §9.

## 2. Baseline assessment — what the supplied code establishes, and what changes

The supplied lineage (v1 → v2) establishes the skeleton this specification
keeps:

- A two-hemisphere input shape and a CC that sits between proposals and any
  effect on the world.
- A staged pipeline: intake → deliberation → authorization → outcome.
- A decision-state vocabulary (`COMMITTED`, `DELIBERATION_REQUIRED`,
  `AUTHORIZATION_REQUIRED`, `REJECTED`, `DEFERRED`).
- Deterministic semantic comparison (`action_type`, then `parameters`) in
  place of v1's raw dict equality.
- The divergence-preservation test, adopted as the seed of AT-01.

The following deviations from the baseline are **normative**. Each cites the
operator's correction of 2026-11-07 or a defect found in review; each names
the section that resolves it.

| ID | Defect in baseline | Resolution |
|---|---|---|
| D-01 | v2 Stage 4 is labelled "Execution (Commit through controlled channel)" but executes nothing: it mints a `COMMITTED` result. `COMMITTED` therefore conflates *authorized* with *done and verified*. | Decision and execution are separate lifecycles (§4.6, §10). `COMMITTED` means deliberation concluded, invariants passed, authorization granted — nothing more. Execution and verification get their own records and states. |
| D-02 | v2 `_deliberate` picks the "stronger" proposal by `len(evidence_pointers)`. Evidence count is not evidential strength; three weak references do not outweigh one decisive observation. | Evidence is preserved as structured references with relevance/reliability assessments (§4.3). No count or count-derived aggregate may be a decision input anywhere in Phase 4 (§7, AT-07). |
| D-03 | v2 `_authorize` reads `payload.get("classification", "free")`: the proposal classifies itself, and a missing classification defaults to the *free* tier. Self-classification decides authorization, and absence of information becomes permission. | The CC derives the tier from an independently maintained action policy keyed by action type and target (§9). A proposal's classification is demoted to an untrusted hint, recorded and compared, never used. |
| D-04 | v2 `ProtectedAppendOnlyLog` is an in-memory Python list behind one underscore. It is not durable, has no integrity verification, no controlled-write boundary (any holder of the object can append anything, and the supplied test itself reaches into `_log`), and "append-only" is a docstring claim. ADR-001 classifies the audit store as `gated_integrity` machinery. | §11: durable hash-chained JSONL store on the integrity volume, single-writer component, fsync-before-acknowledge, chain verification, fail-closed behaviour on verification failure. |
| D-05 | v2 `_to_log_entry` stores only hemisphere, intent, and confidence per proposal. The disagreement record — payloads, assumptions, evidence, alternatives, CC reasoning — is discarded at exactly the point it matters most. | Audit entries embed complete records (§11.3). Lossy projections are prohibited as the sole record of any decision. |
| D-06 | v1 commits unilaterally on divergence when one side's confidence ≥ 0.85 ("hemisphere_a_dominant"). One hemisphere's self-reported confidence overrides the other's dissent — consensus bypass by self-report. | Removed. Material divergence has no confidence-based exit (§7). Confidence is recorded evidence, never a commit threshold; Phase 4 defines no numeric confidence thresholds at all. |
| D-07 | v1 names the divergence outcome `ROLLED_BACK`. Nothing was executed, so nothing was rolled back; the name misrepresents state (INV-006). | Divergence outcome is `DELIBERATION_REQUIRED` (v2's correction, retained). Rollback exists only in the execution lifecycle, after an execution (§10). |
| D-08 | v2 `evaluate()` lets a `REJECTED` deliberation result fall through into `_authorize` (`state != COMMITTED and state != REJECTED` returns early — so REJECTED proceeds), where `_authorize(None)` would fail or misbehave. | The state machine in §4.6 is explicit and total: `REJECTED` is terminal, and every stage's legal successors are enumerated. AT-14 is the regression test. |
| D-09 | v2 `CCDecisionResult.audit_hash` is decorative: it hashes `str(dict)` (non-canonical), references a `timestamp` field the class does not have (the `hasattr` guard silently substitutes `''`), omits the proposals, the disagreement, and the tier, is computed at construction before later mutation, and is not chained to any previous record — so it detects no tampering with anything. | Canonical serialization (§5.3) and a hash chain over complete entries (§11.2). A record's hash covers its full canonical content and its predecessor. |
| D-10 | v2 `ProtectedAppendOnlyLog.append` returns `entry['audit_hash']` — a `KeyError` for any entry lacking that key, coupling the log to one caller's dict shape. | The audit writer accepts records through the serialization boundary and computes hashes itself (§11.2). Callers never supply hashes. |
| D-11 | v2 `_authorize` falls through to `free` for *any* unrecognized classification value, not just a missing one. Unknown input is silently treated as the safest class. | Fail-closed matrix (§9.5): unknown action types defer, malformed records reject, unrecognized hints are ignored-and-noted. No code path defaults to a permissive tier. |
| D-12 | Baseline hemisphere identifiers are free strings (`hem_a`, `hemisphere_a`), and `InterpretationRecord` lacks the Phase-0 contract's provenance fields (`task_reference`, `original_input_reference`), so a record cannot be traced to the input that produced it. | Canonical component IDs `HEM-A` / `HEM-B`; §4.2's record carries full provenance. Supplied test fixtures map `hem_a → HEM-A`, `hem_b → HEM-B`. |

What is **not** changed: v2's stage order, its DecisionState vocabulary (kept
as decision-outcome states in §4.6), semantic comparison as the deliberation
equivalence test (formalized in §7.2), and the divergence test's scenario
(adopted in AT-01). The baseline's instinct — structure over vibes — is the
specification's instinct too; the corrections are about where the structure
must not trust its inputs.

## 3. Build sequence (normative)

Phase 4 is built in this order, and only this order. The serialization
boundary (4.2) is a step **inside** the sequence — an explicit prerequisite
for every step after it — not an independent project and not a preliminary
side-quest. The ordering rationale is the operator's: the governance model
(4.1) must be settled before deciding how its records travel (4.2), and the
governance engine (4.3–4.8) is then built against that contract (4.9).

| Step | Component | Required outcome | Depends on | Specified in | Proven by |
|---|---|---|---|---|---|
| 4.1 | Governance data structures | Formal interpretation, disagreement, decision, and authorization records, with provenance and an explicit state model | — | §4 | AT-12, AT-13 |
| 4.2 | Serialization boundary | Validated, versioned transfer of records between hemispheres, CC, and shared memory | 4.1 | §5 | AT-06, AT-12 |
| 4.3 | Interpretation intake | Both proposals preserved independently, including provenance | 4.2 | §6 | AT-06, AT-13 |
| 4.4 | Deliberation engine | Agreement, divergence, uncertainty, and unresolved conflicts represented explicitly | 4.3 | §7 | AT-01, AT-07 |
| 4.5 | Invariant enforcement | Structural rules checked independently of confidence and consensus | 4.4 | §8 | AT-08, AT-14 |
| 4.6 | Authorization engine | ADR-001 reversibility tiers enforced through a separate permission mechanism | 4.5 | §9 | AT-03, AT-04, AT-05, AT-15, AT-16 |
| 4.7 | Execution interface | Authorized actions passed to a controlled executor; execution and verification recorded separately | 4.6 | §10 | AT-02, AT-11, AT-15 |
| 4.8 | Decision and audit log | Append-only records, traceable decisions, integrity verification | 4.1 (format), 4.4–4.7 (events) | §11 | AT-09, AT-10 |
| 4.9 | Structural validation | Automated tests for invariants, authority boundaries, divergence, and failure conditions | 4.1–4.8 | §12 | the AT suite itself |

Rules of the sequence:

- **S-1.** No step ≥ 4.3 may accept, pass, or store a governance record except
  through the serialization boundary of §5. There is no in-memory side door:
  the boundary is the only door. (This is what makes the boundary a real
  architectural surface instead of a utility function.)
- **S-2.** A step is complete when its acceptance tests (§12) pass, not when
  its code exists. Documentation of a step is not the step (roadmap operating
  rules).
- **S-3.** Steps 4.4–4.7 are built against §4's records and §5's boundary
  only. None of them may reach into shared-memory storage internals; storage
  sits behind the boundary (§5.5).
- **S-4.** The audit log's *format* is fixed at 4.1/§11.2 even though its full
  event set arrives with 4.4–4.7, so no early step writes a log format that
  later steps must migrate.

## 4. Step 4.1 — Governance data structures

All records are plain data (dicts at the boundary; dataclasses in code) with a
**closed field set**: a field not defined here is a validation error (§5.4),
never silently carried.

### 4.1 Common envelope and provenance

Every record carries:

| Field | Type | Notes |
|---|---|---|
| `record_id` | string | Unique, producer-assigned, format `<kind>-<uuid4hex>`. |
| `schema` | string | Record kind, e.g. `neurai.cc/interpretation-record`. |
| `schema_version` | string | Semver; this specification defines `1.0.0` for every kind. |
| `created_at` | string | ISO-8601 UTC. Evidence only; never a decision input. |
| `provenance` | object | See below. |

`provenance`:

| Field | Type | Notes |
|---|---|---|
| `origin` | enum | `hemisphere_original` \| `cc_derived` \| `executor_report` \| `operator_record`. |
| `origin_component` | string | `HEM-A`, `HEM-B`, `CC`, `EXECUTOR`, or `OPERATOR`. |
| `derived_from` | list | For derived records: `{record_id, record_hash}` of every source record. Empty for originals. |

Two provenance rules do real work:

- **P-1 (originals are immutable).** A `hemisphere_original` record is never
  edited by the CC or anyone else. Corrections and reinterpretations are new
  records whose `derived_from` points at the original (consistent with
  MEM-R03: corrections preserve history).
- **P-2 (derived is distinguishable).** Anything the CC computes — a
  deliberation outcome, a derived tier, a selected payload — lives in a
  `cc_derived` record whose `derived_from` names its sources. A reader must
  always be able to tell what a hemisphere actually proposed from what the CC
  subsequently made of it. The baseline conflated these (the deliberation
  result *replaced* the proposals in the log); that is D-05's root cause.

### 4.2 InterpretationRecord

Extends the supplied baseline record with the Phase-0 contract's provenance
fields (`reasoning_report` in `architecture/contract/component-interfaces.yaml`).
Field names from the baseline are kept where they exist.

| Field | Type | Required | Notes |
|---|---|---|---|
| `hemisphere_id` | enum `HEM-A` \| `HEM-B` | yes | Canonical component IDs (D-12). |
| `task_reference` | string | yes | The task this interpretation answers. |
| `original_input_reference` | string | yes | Pointer to the unmodified original input (INV: reconciliation must never replace the original). |
| `intent` | string | yes | The hemisphere's perceived intent, in its own terms. |
| `semantic_payload` | ActionPayload (§4.3) or `null` | yes | `null` = this hemisphere proposes no action (interpretation only). |
| `assumptions` | list[string] | yes | May be empty; must be present. |
| `evidence` | list[EvidenceRef] (§4.3) | yes | May be empty; must be present. Replaces baseline `evidence_pointers`. |
| `confidence` | number 0..1 | yes | Self-reported. Recorded evidence; never a threshold input (§7). |
| `alternatives_considered` | list[string] | yes | May be empty; must be present. |
| `unresolved_questions` | list[string] | no | Carried verbatim into deliberation (INV-010). |

### 4.3 EvidenceRef and ActionPayload

`EvidenceRef` — evidence is a *reference with an assessment*, not a counted vote:

| Field | Type | Required | Notes |
|---|---|---|---|
| `pointer` | string | yes | Reference to the evidence (metric ID, memory record ID, document, observation log). Opaque to the CC. |
| `source_kind` | enum | yes | `direct_observation` \| `instrument_reading` \| `memory_record` \| `document` \| `hemisphere_inference` \| `external_report`. |
| `relevance` | number 0..1 | yes | The citing hemisphere's assessment of relevance to this decision. |
| `reliability` | number 0..1 | yes | The citing hemisphere's assessment of the source's reliability. |

The CC preserves EvidenceRefs verbatim, in both sets, in the DeliberationRecord.
It may *report* them; it MUST NOT aggregate them into a score, a count
comparison, or any other branch input (D-02). If a future phase wants
evidence-weighted reasoning, that is a roadmap-level change with its own
record types — not a Phase 4 implementer's improvisation.

`ActionPayload` — the actionable content of a proposal:

| Field | Type | Required | Notes |
|---|---|---|---|
| `action_type` | string | yes | Key into the action policy (§9). |
| `parameters` | object | yes | Action-specific; compared canonically (§7.2). |
| `target` | string or `null` | yes | Artifact/resource the action acts on (path, store, component). Drives target-based tier escalation (§9.3). |
| `classification_hint` | string or `null` | no | **Untrusted.** The proposing hemisphere's opinion of its own action's class. Recorded, compared against the policy-derived tier, never used as the tier (D-03). |
| `reversibility_claim` | string or `null` | no | **Untrusted**, same treatment as `classification_hint`. |

### 4.4 DeliberationRecord (`cc_derived`)

Produced by §7. Embeds both InterpretationRecords **by value** (the complete
disagreement record, D-05), plus:

| Field | Type | Notes |
|---|---|---|
| `interpretations` | [InterpretationRecord, InterpretationRecord] | Exactly the two intake originals, unmodified (P-1). |
| `equivalence` | object | `{action_type_equal: bool, parameters_equal: bool, target_equal: bool}` from §7.2. |
| `agreement_points` | list[string] | What the records concur on (payload fields, assumptions, evidence pointers held in common). |
| `divergence_points` | list[object] | One entry per differing field: `{field, value_a, value_b}` — field-level, not a blob. |
| `evidence_profile` | object | `{HEM-A: [EvidenceRef...], HEM-B: [EvidenceRef...]}` — verbatim copies. |
| `uncertainty` | object | `{confidence: {HEM-A: x, HEM-B: y}, unresolved_questions: [...]}` — verbatim carry-over. |
| `outcome` | enum | `agreement` \| `agreement_with_reservations` \| `divergence_material` \| `insufficient_information`. |
| `selected_payload` | ActionPayload or `null` | Set only when `outcome` is `agreement*`; it is the payload both records agree on, never a "winner's" payload (§7.3). |
| `cc_reasoning` | string | Required, non-empty. The CC's reasoning is itself part of the record, stated in the open. |

### 4.5 InvariantCheckRecord, AuthorizationRecord, OperatorAuthorizationRecord

`InvariantCheckRecord` — one per check run (§8): `{check_id, invariant_id,
result: pass|fail, detail}`. A decision carries the full list; a `fail` is
never summarised away.

`AuthorizationRecord` (`cc_derived`) — produced by §9:

| Field | Notes |
|---|---|
| `action_type`, `target` | Copied from the agreed payload. |
| `derived_tier` | `free` \| `gated_integrity` \| `gated_irreversible` — from policy only. |
| `policy_rule` | Identifier of the policy entry (or escalation rule) that produced the tier. |
| `policy_version`, `policy_hash` | The exact policy snapshot used (AT-16). |
| `hint_received`, `hint_agrees` | The proposal's untrusted hint, and whether it matched. Disagreement is recorded and surfaced, not punished and not obeyed. |
| `result` | `authorized` \| `operator_required` \| `denied`. |

`OperatorAuthorizationRecord` (`operator_record`) — `{decision_id, action_type,
granted: bool, operator: "Levi Nord", note}`. The only record that can move a
gated decision from `AUTHORIZATION_REQUIRED` to `COMMITTED`. Operator denials
are records too, and are terminal for that decision.

### 4.6 Decision state model

`DecisionRecord` is the CC's central output. Its core fields conform to the
Phase-0 `decision_record` schema (`decision_id`, `timestamp`, `kind`,
`subject`, `rationale`, `alternatives_considered`, `unresolved_objections`,
`authority_tier`, `change_record_reference`, `prior_text`/`new_text` for
governance changes), extended with: `state`, `deliberation_ref`
(`{record_id, record_hash}`), `authorization_ref`, `invariant_checks`
([InvariantCheckRecord]), `execution_ref` (nullable), `verification_ref`
(nullable), and `deferral_reason` (nullable).

**States** (baseline vocabulary, corrected semantics):

| State | Meaning — exactly this, no more |
|---|---|
| `DELIBERATION_REQUIRED` | Material divergence or insufficient information; the decision is open and awaiting new records or operator resolution. Non-terminal. |
| `AUTHORIZATION_REQUIRED` | Deliberation concluded in agreement and invariants passed, but the derived tier is gated and no operator grant is on record. Non-terminal. |
| `DEFERRED` | The CC cannot proceed for a stated, curable reason (`deferral_reason`, §9.5). Non-terminal. |
| `REJECTED` | Refused: invariant failure, malformed or unprovenanced input at the decision stage, or operator denial. **Terminal.** |
| `COMMITTED` | Deliberation concluded, invariants passed, authorization granted for the derived tier. **This asserts nothing about execution.** An action in state `COMMITTED` has been *decided and permitted*, not done. |

**Legal transitions** (the state machine is total — any transition not listed
is a defect):

```
intake ──► DELIBERATION_REQUIRED ──(new records; §7 re-run)──► DELIBERATION_REQUIRED
              │  agreement + checks pass + tier free          │ agreement + checks pass + tier gated
              ▼                                               ▼
          COMMITTED                              AUTHORIZATION_REQUIRED ──(grant)──► COMMITTED
              ▲                                               └──(denial)──► REJECTED (terminal)
              └──(operator resolution of divergence, §7.5)──── DELIBERATION_REQUIRED
any stage ──(curable defect, §9.5)──► DEFERRED ──(defect cured; stage re-entered)──► (origin stage)
any stage ──(invariant fail / operator denial)──► REJECTED (terminal)
COMMITTED ──► (execution lifecycle, §4.7 — separate records, decision state stays COMMITTED)
```

`REJECTED` has no successors (D-08). A `DEFERRED` decision records which stage
it deferred from and re-enters at that stage; it never skips forward.

### 4.7 ExecutionRecord and VerificationRecord (`executor_report`)

Execution is a separate lifecycle with its own records, linked by
`decision_id` (§10):

`ExecutionRecord`: `{execution_id, decision_id, executor_id, state, started_at,
finished_at, rollback_reference (nullable), result_summary, error (nullable)}`
with `state` ∈ `not_started` \| `running` \| `succeeded` \| `failed` \|
`partially_succeeded`. For free-tier state changes, `rollback_reference` MUST
be populated **before** the action is performed (§10.3).

`VerificationRecord`: `{verification_id, execution_id, state, checks:
[{name, result, detail}], verified_by}` with `state` ∈ `verified` \|
`verification_failed` \| `unverifiable`.

The user-facing status of any action (SVC-002, INV-006) is the tuple
(decision state, execution state, verification state) — e.g. *authorized, not
yet executed*; *executed, verification failed*. No layer may collapse this
tuple into "done" (D-01).

## 5. Step 4.2 — The serialization boundary

### 5.1 Position and purpose

The boundary sits between every pair of parties that exchange governance
records: hemisphere → shared memory → CC intake, CC → shared memory, CC →
executor, CC → audit log. Its job is **transport with validation**: it proves
that what arrived is exactly what was sent, in a format both sides agree on.
It makes no decisions. It MUST NOT normalize away disagreements, drop
uncertainty fields, coerce values for convenience, reconcile alternatives, or
otherwise edit content — a boundary that edits is a second, hidden governance
layer, and there is exactly one governance layer.

Per S-1, all record exchange in steps ≥ 4.3 goes through this boundary,
including calls that happen to be in-process today. The discipline is the
point: when hemispheres later run as separate processes or on separate
machines, nothing about the governance design changes.

### 5.2 Canonical format

- Encoding: JSON, UTF-8, one record per payload.
- Envelope: `{"schema": <kind>, "schema_version": "1.0.0", "record": {...}}`.
- Canonical form (the only form ever hashed or stored):
  `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
  encoded as UTF-8. Two records with equal content have byte-identical
  canonical forms regardless of the producer's key ordering (AT-12).
- `record_hash` = hex SHA-256 of the canonical bytes of the `record` object
  alone (envelope excluded). Producers never supply hashes; the boundary
  computes them (D-10's lesson, generalized).

### 5.3 Interface

```python
# neurai/governance/serialization.py  (stdlib only)
def serialize(record: dict) -> bytes: ...
    # Validate, wrap in envelope, return canonical bytes.
    # Raises SerializationError on an invalid record.
def deserialize(data: bytes) -> dict: ...
    # Parse, check envelope, validate against the kind's schema,
    # return the record dict exactly as sent. Raises SerializationError.
def record_hash(record: dict) -> str: ...
    # SHA-256 over the canonical form of the record object.
class SerializationError(Exception):
    codes: list[str]  # machine-readable, from §5.4
```

Validation is hand-rolled per record kind from §4's tables (the project is
stdlib-only; no external schema library). A validator checks: required fields
present, types exact, enums in range, numeric ranges (`confidence`,
`relevance`, `reliability` ∈ [0, 1]), closed field set.

### 5.4 Versioning and rejection rules

- Same major version, minor ≤ current: accept.
- Major version different from current: reject, code `ERR_SCHEMA_MAJOR`.
- Unknown `schema` kind: reject, `ERR_SCHEMA_KIND`.
- Missing/extra field: reject, `ERR_FIELD_MISSING` / `ERR_FIELD_UNKNOWN`.
- Wrong type or out-of-range value: reject, `ERR_FIELD_TYPE` / `ERR_FIELD_RANGE`.
- Unparseable bytes: reject, `ERR_UNPARSEABLE`.

Rejection is total: a payload is either a valid record, byte-for-byte the
sender's content, or it does not cross. There is no partial repair, no
"accept and fix," no defaulting of missing fields. (Baseline contrast:
`payload.get("classification", "free")` is precisely a boundary-level default
smuggled into authorization — D-03/D-11.)

### 5.5 Storage neutrality

Shared memory stores canonical bytes + `record_hash` and returns them;
validation runs on write **and** on read (stored corruption is detected at
read time and fails closed, §9.5). The boundary API is:

```python
def store(record: dict) -> str: ...        # returns record_id; validates first
def fetch(record_id: str) -> dict: ...     # validates on the way out
```

SQLite (Phase 2), a filesystem store, or an in-memory test double are all
legitimate implementations of these two functions. Phase 4 acceptance tests
run against a test double; nothing in §§6–10 may know which store is live.

## 6. Step 4.3 — Interpretation intake

```python
# neurai/governance/intake.py
def intake(data: bytes) -> IntakeReceipt: ...
    # deserialize (validates) -> store original -> receipt
@dataclass
class IntakeReceipt:
    record_id: str
    record_hash: str
    stored: bool
```

Rules:

- **I-1.** Intake accepts bytes, never live objects (S-1). The record that
  enters governance is the record that crossed the boundary.
- **I-2.** Each hemisphere's record is intaken and stored **independently**.
  Intake never pairs, merges, or compares; pairing happens in deliberation,
  by `record_id`.
- **I-3.** The stored original is immutable (P-1). Any later fetch returns
  content whose hash equals the intake receipt's hash; a mismatch is
  corruption and fails closed (§9.5).
- **I-4.** A failed validation raises `IntakeRejection` carrying the §5.4
  codes. The rejection is audit-logged with the SHA-256 of the raw bytes and
  the codes (the malformed bytes themselves are not stored as a record). No
  DecisionRecord is created for rejected intake.
- **I-5.** If neither record of a task proposes an action (both
  `semantic_payload` are `null`), intake still stores both; no decision is
  opened. Phase 4 governs actions; pure interpretation synthesis belongs to
  coordination (Phase 5).

## 7. Step 4.4 — Deliberation engine

```python
# neurai/governance/deliberation.py
def deliberate(record_id_a: str, record_id_b: str) -> DeliberationRecord: ...
    # fetches both originals through the boundary; pure function of the two records
```

### 7.1 What deliberation is

A deterministic comparison that produces a complete, explicit account of
agreement, divergence, and uncertainty — the DeliberationRecord of §4.4.
Deliberation does not decide what is true and does not pick winners. It
establishes whether the two hemispheres have, in fact, proposed the same
action, and it preserves everything needed for a human or a later process to
see the disagreement exactly as it stood.

### 7.2 Equivalence

Two ActionPayloads are equivalent iff, after removing the untrusted hint
fields (`classification_hint`, `reversibility_claim`), their canonical forms
(§5.2) are byte-identical: same `action_type`, same `parameters`, same
`target`. This formalizes the baseline's semantic comparison and extends it
to the whole payload, so a difference in any substantive field is a
divergence, never silently ignored.

### 7.3 Outcomes

- Both payloads present and equivalent → `agreement`. `selected_payload` is
  the agreed payload (identical in both records; the choice of *which copy*
  is provenance bookkeeping via `derived_from`, not a judgement). If the
  records' assumptions, evidence, or unresolved questions materially conflict
  despite payload agreement → `agreement_with_reservations`, with the
  conflicts in `divergence_points`; the decision may still proceed, but the
  reservations are part of its record and are surfaced, not buried.
- Payloads present and not equivalent → `divergence_material`. The decision
  state is `DELIBERATION_REQUIRED`. There is **no exit from material
  divergence inside deliberation**: no confidence comparison, no evidence
  weighing, no CC tie-break (D-02, D-06). Exits are exactly: (a) new
  InterpretationRecords that converge (a fresh deliberation over new
  record IDs), or (b) an operator resolution (§7.5).
- Exactly one payload present, or either record's payload `null` while the
  other proposes an action → `insufficient_information`, decision `DEFERRED`
  with `deferral_reason = awaiting_second_proposal`.
- Confidence values, evidence counts, and arrival order play no part in any
  outcome. A deliberation implementation containing a numeric comparison of
  the two records' `confidence` or `len(evidence)` as a branch condition is
  non-conformant by construction; AT-07 tests this behaviourally.

### 7.4 Disagreement preservation

`divergence_points` is field-level: one entry per differing field, both
values verbatim. The DeliberationRecord embeds both originals by value
(§4.4). Nothing about either hemisphere's position may be summarised,
averaged, or reconciled away at this stage or any later one (INV-002,
INV-009, MEM-R08).

### 7.5 Operator resolution

The operator may resolve a `DELIBERATION_REQUIRED` decision by filing an
OperatorResolutionRecord (`operator_record`: `{decision_id, chosen_payload,
note}`) — the human is the divergence escape hatch, openly, on the record.
The resulting decision proceeds to §8/§9 with the chosen payload, its
deliberation reference intact, and the resolution attached. The CC MUST NOT
manufacture an equivalent outcome on its own.

## 8. Step 4.5 — Invariant enforcement

Invariant checks are **structural predicates over records and policy** —
pure functions, run on every decision that reaches agreement, in a fixed
order, regardless of how confident or unanimous the hemispheres are. A check
never reads confidence as exculpatory evidence. Failure outcomes are part of
each check's definition: `REJECTED` for incurable defects, `DEFERRED` for
curable ones (§4.6).

| Check | Invariant | Predicate | On fail |
|---|---|---|---|
| CHK-PROV-01 | INV-006 | Both InterpretationRecords carry complete provenance and their stored hashes match intake receipts. | REJECTED |
| CHK-PLUR-01 | INV-002 | The DeliberationRecord embeds both originals by value and its `derived_from` names exactly those two records. | REJECTED |
| CHK-EVID-01 | INV-009 | If deliberation outcome is not `agreement*`, the decision is not `COMMITTED` — unless an OperatorResolutionRecord is attached. (Consensus is not evidence; and evidence, not consensus, is what a resolution must answer to.) | REJECTED |
| CHK-AUTH-01 | INV-007 | An AuthorizationRecord exists whose `derived_tier` is policy-sourced (§9) with `policy_version` and `policy_hash` recorded. No tier may trace to a proposal hint. | DEFERRED (`authorization_unavailable`) |
| CHK-REV-01 | INV-008 | The policy entry for the action declares a `reversibility_class`; `tier = free` with class `irreversible` is a policy contradiction. | DEFERRED (`policy_inconsistent`) |
| CHK-HUM-01 | INV-012 | A decision whose derived tier is gated reaches `COMMITTED` only with an OperatorAuthorizationRecord with `granted = true` on file. | (state machine enforces; §4.6) |
| CHK-UNC-01 | INV-010 | Both records' `confidence` and `unresolved_questions` appear verbatim in the DeliberationRecord's `uncertainty`. Uncertainty may be carried; it may not be laundered. | REJECTED |

Checks produce InvariantCheckRecords attached to the DecisionRecord —
including the passes. "The rules were checked" is a claim the record must be
able to prove, per-check, not a vibe (INV-006).

## 9. Step 4.6 — Authorization engine

This step implements the Phase-0 `CC.enforce_authority` interface and ADR-001's
tiers. Its core property: **the tier of an action is a fact the CC derives
from policy it does not take from the proposer.** A hemisphere cannot classify
its own action as safe and thereby set its own authorization tier (D-03).

### 9.1 The action policy

Authorization reads an **action policy**: versioned data, the runtime
successor of `architecture/registry/authority-tiers.yaml`, maintained
independently of any proposal. Format (YAML):

```yaml
policy_version: "1.0.0"
integrity_targets:            # §9.3 escalation list
  - "integrity/audit-log"
  - "integrity/backup-store"
  - "integrity/rollback"
  - "registry/authority"
  - "registry/action-policy"
actions:
  self_patch:
    tier: free
    reversibility_class: reversible
    operator_grant: not_required
  # ... one entry per action_type; Appendix B is the proposed initial table
```

Each action entry: `{tier, reversibility_class: reversible|irreversible|
conditional|readonly, operator_grant: not_required|per_action|per_class,
notes?}`.

### 9.2 Tier derivation (deterministic, total)

```
def derive_tier(payload, policy) -> AuthorizationRecord:
    entry = policy.actions.get(payload.action_type)
    if entry is None:            -> DEFERRED(unknown_action_type)      # §9.5
    tier = entry.tier
    if payload.target in policy.integrity_targets:
        tier = gated_integrity   # §9.3 target escalation, overrides the table
    record hint_received = payload.classification_hint
    record hint_agrees = (hint maps to tier)  # comparison only; never an input
```

`enforce_authority(actor, action, target)` from the Phase-0 contract is this
function's interface face: same registry state + same action ⇒ same decision,
always.

### 9.3 Target-based escalation

Any action whose `target` names the integrity machinery — the audit log store,
the backup store, the rollback mechanism, the authority registry, or the
action policy itself — is `gated_integrity` **regardless of its action-type
entry**. This is ADR-001's one hard self-modification gate, enforced by
construction: a free-tier action type aimed at the machinery that makes the
free tier safe does not stay free.

### 9.4 Gated flow

- Derived tier `free` → AuthorizationRecord `result = authorized`; the
  decision may become `COMMITTED` (subject to §8).
- Derived tier gated → `result = operator_required`; decision state
  `AUTHORIZATION_REQUIRED`. Only an OperatorAuthorizationRecord moves it:
  `granted = true` → `COMMITTED` path resumes; `granted = false` →
  `REJECTED`, terminal, both records audit-logged.
- Governance self-modification follows ADR-001 exactly: changes to governance
  documents and tier assignments are free-tier **only** as append-only
  decision-log entries carrying prior text, new text, and rationale — except
  changes to the decision-log mechanism itself, which are `gated_integrity`.

### 9.5 Fail-closed matrix (normative)

Missing information is never permission. Every defect below has a defined,
non-permissive outcome; there is no other default anywhere in Phase 4.

| Defect | Outcome |
|---|---|
| Unparseable payload / schema validation failure (§5.4) | Intake rejection; no decision created (I-4) |
| Missing provenance on a stored record / hash mismatch at fetch | Decision `REJECTED` (CHK-PROV-01) |
| Unknown `action_type` (absent from policy) | `DEFERRED` (`unknown_action_type`) — curable by a policy addition, which is itself a governance act (§9.6) |
| Unrecognized `classification_hint` value | Hint ignored and noted in the AuthorizationRecord; tier derived from policy as usual |
| Policy file missing or unparseable at startup | CC runs in **halted mode**: intake and deliberation work; every authorization returns `DEFERRED` (`policy_unavailable`); nothing is authorized |
| Policy contradiction (CHK-REV-01) | `DEFERRED` (`policy_inconsistent`) |
| Audit chain verification failure at startup (§11) | Authorization halted (`DEFERRED`, `audit_unverified`) until an operator acknowledgment record is filed; deliberation and intake continue |
| Executor unavailable at dispatch | Decision stays `COMMITTED`; execution state `not_started`; surfaced as authorized-not-executed, never as failure of the decision and never as success |

### 9.6 Policy governance

The action policy is governance data. Loading: at startup the CC parses the
policy, computes its canonical hash, and audit-logs `policy_loaded` with
`policy_version` + `policy_hash`. Every AuthorizationRecord cites both, so
any later reader can tell exactly which policy authorized what (AT-16). A
policy change takes effect only on reload, is itself recorded (per §9.4's
decision-log rule for tier assignments), and — if it touches the
`integrity_targets` list or the policy mechanism — is `gated_integrity`. The
CC never edits its own policy; it only reads snapshots.

## 10. Step 4.7 — Execution interface

### 10.1 The controlled executor

Execution is performed by exactly one component class, the executor, through:

```python
# neurai/governance/execution.py
class Executor(Protocol):
    def execute(self, decision: DecisionRecord,
                authorization: AuthorizationRecord) -> ExecutionRecord: ...
    def verify(self, execution: ExecutionRecord) -> VerificationRecord: ...
```

- **E-1.** The executor accepts only a `COMMITTED` DecisionRecord with its
  AuthorizationRecord. Before acting it **re-validates independently**:
  decision state is `COMMITTED`, `authorization.decision_id` matches, and for
  gated tiers an OperatorAuthorizationRecord with `granted = true` exists.
  The executor does not trust the pipeline that handed it the decision
  (defense in depth at the one boundary where effects leave the system).
- **E-2. Idempotency.** `decision_id` is the idempotency key. A second
  `execute` for the same decision returns the original ExecutionRecord
  without re-performing the action.
- **E-3.** No other component performs actions. Hemisphere code and CC
  reasoning code hold no executor handle; the dependency direction is tested
  (AT-17).

### 10.2 Execution and verification are separate steps

`execute` performs the action and returns an ExecutionRecord. `verify` then
checks the resulting state against the action's intent (per-action verifiers
registered alongside the policy; e.g. for a config write: re-read the target
and compare canonical content). An action type with no registered verifier
yields `state = unverifiable` — reported as such (INV-006), never upgraded to
`verified` by optimism.

### 10.3 Reversibility at execution time

For a free-tier state-changing action, the executor first creates the
rollback snapshot through the commit/rollback service (SVC-007) and records
`rollback_reference` in the ExecutionRecord **before** performing the action
(ADR-001: backup before commit; INV-008). If the snapshot fails, execution
fails at stage `preparation` and the action is not performed. Rollback after
a failed or partially succeeded execution is an operator-visible operation
recorded in the same terms — and it is the only place the word "rollback"
appears in Phase 4 (D-07).

### 10.4 Status honesty

At every moment, the true status of an action is the §4.7 tuple — decision,
execution, verification. In particular: a `COMMITTED` decision whose
execution failed is **an authorized action that failed**, and any surface
that reports it as "committed, therefore done" is non-conformant (D-01).

## 11. Step 4.8 — Decision and audit log

ADR-001 names the audit log store part of the `gated_integrity` machinery.
This section specifies the real thing the baseline's in-memory list stood in
for (D-04).

### 11.1 Store

- Location: `$NEURAI_INTEGRITY_DIR/audit/cc-audit.jsonl` on the installed
  system (the integrity volume; `NEURAI_INTEGRITY_DIR` is already exported by
  `rootfs/init-disk-system`), a configured path elsewhere. One JSON object per
  line, canonical form (§5.2).
- Writes happen through exactly one component, `AuditLogWriter`
  (`neurai/governance/audit.py`). It is part of the factored integrity
  machinery: it imports nothing from hemisphere, model, or core tool code,
  and nothing else in the system holds a write path to the log file (§13,
  AT-17).
- `append()` writes the line, flushes, and `os.fsync`s **before** returning
  success. An append that did not reach durable storage did not happen, and
  the pipeline stage that needed it does not proceed.

### 11.2 Hash chain

Each entry: `{seq, timestamp, kind, decision_id (nullable), entry, prev_hash,
entry_hash}` where `entry` is the complete record being logged (§11.3) and

```
entry_hash = sha256(canonical({seq, timestamp, kind, decision_id, entry, prev_hash}))
```

Genesis `prev_hash` is 64 zero characters. `verify_chain()` replays the file,
recomputing every hash and linkage; it returns `(ok, first_bad_seq | None)`.
Modification, deletion, reordering, or truncation of history is detectable
and localizable to a sequence number (AT-09). `head_hash()` returns the
current head for operator-facing digests, so a chain rewritten wholesale
would diverge from any previously surfaced head — the anchor that makes
tamper-evidence meaningful rather than self-referential.

Scope honesty: the chain provides **tamper-evidence and controlled writes**,
not magic. An attacker with raw write access to the volume and no external
anchor could rewrite history wholesale; ADR-001's actual control — the
machinery living on a physically separate volume behind a factored,
gated component — is what Phase 4 implements. The specification claims
exactly that and no more.

### 11.3 Completeness

Every entry embeds the **complete** record for its event — for
`deliberation_completed`, the full DeliberationRecord including both
InterpretationRecords by value; for `decision_committed`, the full
DecisionRecord with its invariant checks and authorization reference. A
projection like the baseline's `{hemisphere, intent, confidence}` is
prohibited as the sole record of anything (D-05). Event kinds (closed set):

`intake_rejected`, `deliberation_completed`, `invariant_check_failed`,
`authorization_decided`, `operator_authorization`, `operator_resolution`,
`decision_committed`, `decision_deferred`, `decision_rejected`,
`execution_completed`, `verification_completed`, `objection_recorded`,
`policy_loaded`, `policy_changed`, `authorization_halted`,
`authorization_resumed`.

The writer's public surface is `append`, `verify_chain`, `head_hash`, and
read iteration returning **copies**. There is no update, delete, or truncate
operation, and mutating a returned entry cannot alter stored bytes (AT-10).

### 11.4 Startup verification

At CC startup: load policy (§9.6), then verify the audit chain. A failed
verification puts authorization into halted mode (§9.5) until the operator
files an acknowledgment record — the system notices its own memory of
decisions is suspect and stops granting new ones. That, not a Python
underscore, is what "protected" means at this scale.

## 12. Step 4.9 — Structural validation (acceptance tests)

Tests are Python `unittest`, stdlib only, synthetic hemisphere records, a
fixture policy, an in-memory store double, and a spy executor. They live in
`tests/test_phase4_*.py` with a runner `tests/run_phase4.sh`, alongside the
existing suites. Every test names the step and deviation/invariant it proves.

| ID | Test | Proves |
|---|---|---|
| AT-01 | **Divergence preservation** (the supplied baseline test, strengthened). A: `write_config {mode: aggressive}`, conf 0.91, evidence [metric_01]; B: `write_config {mode: conservative}`, conf 0.88, evidence [metric_02, metric_03]. Expect state `DELIBERATION_REQUIRED`; the audit entry's DeliberationRecord contains both payloads, both assumption lists, both evidence sets, both alternatives — asserted field by field, not `len(log) == 1`. (Against baseline v1 this same input *commits* A-dominant; against v2 it deliberates but logs a lossy projection. AT-01 fails against both, by design.) | 4.4, 4.8; D-05, D-06 |
| AT-02 | **Commitment is not execution.** Identical free-tier payloads both sides → decision `COMMITTED`; spy executor records zero calls; no ExecutionRecord exists; the status tuple reads (COMMITTED, not_started, —). | 4.7; D-01 |
| AT-03 | **Self-classification attack.** Payload carries `classification_hint` implying free, but its `action_type` maps to `gated_irreversible` in the fixture policy → `AUTHORIZATION_REQUIRED`, `derived_tier = gated_irreversible`, `hint_agrees = false` recorded. | 4.6; D-03 |
| AT-04 | **Missing hint.** No `classification_hint` at all → tier still derived from policy; a gated action is still gated. | 4.6; D-03, D-11 |
| AT-05 | **Unknown action type.** `action_type` absent from policy → `DEFERRED(unknown_action_type)`; executor untouched; audit entry written. | 4.6; D-11 |
| AT-06 | **Malformed intake** (four sub-cases: missing provenance field; wrong field type; unknown extra field; wrong major schema version) → `IntakeRejection` with the right §5.4 codes; no DecisionRecord; `intake_rejected` audit entry carries the raw-bytes hash. | 4.2, 4.3 |
| AT-07 | **Evidence-count neutrality.** Divergent payloads with evidence counts 3-vs-1, then 1-vs-3 → `DELIBERATION_REQUIRED` both ways. Agreement case with counts swapped → byte-identical selected payload and outcome. | 4.4; D-02 |
| AT-08 | **Consensus does not skip invariants.** Unanimous, high-confidence agreement on an action whose fixture policy entry is `tier: free` + `reversibility_class: irreversible` → `DEFERRED(policy_inconsistent)` via CHK-REV-01; executor untouched. | 4.5 |
| AT-09 | **Chain tamper detection** (three sub-cases: mutate one stored entry's field; delete a middle entry; swap two entries) → `verify_chain()` fails and names the first bad `seq` in each case. | 4.8; D-04, D-09 |
| AT-10 | **Append-only surface.** `AuditLogWriter` exposes no mutation methods; mutating a dict returned by read iteration leaves stored bytes (and chain verification) unaffected. | 4.8; D-04 |
| AT-11 | **Execution failure honesty.** Executor raises during perform → ExecutionRecord `failed`; decision remains `COMMITTED`; status tuple never reports success; a `verification` is not fabricated. | 4.7; D-01 |
| AT-12 | **Serialization round-trip.** Records containing disagreement vectors, unresolved questions, and empty lists: `deserialize(serialize(r)) == r`; canonical bytes stable across key-order permutations of the source dict; hashes equal. | 4.1, 4.2 |
| AT-13 | **Provenance separation.** After a full pipeline run, both hemisphere originals hash-identical to their intake receipts; the DeliberationRecord is `cc_derived` with `derived_from` = both originals (id + hash). | 4.1, 4.3; P-1, P-2 |
| AT-14 | **REJECTED is terminal.** Force a CHK-PROV-01 failure (fetch-time hash mismatch) → decision `REJECTED`; authorization and executor spies record zero calls. Regression for D-08. | 4.5; D-08 |
| AT-15 | **Gated flow end to end.** `gated_integrity` action (via target escalation, §9.3) → `AUTHORIZATION_REQUIRED`. Run 1: operator denial → `REJECTED`, terminal, logged. Run 2: operator grant → `COMMITTED` → executor re-validates the grant (E-1) → executed, verified; all transitions audit-logged in order. | 4.6, 4.7 |
| AT-16 | **Policy change detectability.** Decisions under policy v1 cite its hash; the policy file is edited and reloaded; `policy_loaded` entries bracket the change and new decisions cite the new hash. History alone reconstructs which policy authorized which decision. | 4.6 |
| AT-17 | **Dependency direction.** Static import scan: `governance/audit.py` and `governance/authorization.py` import nothing from hemisphere/model/core-tool modules; hemisphere-facing code holds no import of `governance/execution.py`'s executor implementation. | 4.6–4.8; ADR-001 factoring |

Phase 4 is complete when AT-01…AT-17 pass against the implementation —
including AT-01 failing against the preserved baselines, which is the
point of keeping them.

## 13. Module layout and implementation directives

```
neurai/governance/
    records.py         # §4 dataclasses + field tables (4.1)
    serialization.py   # §5 boundary: serialize/deserialize/record_hash, validators (4.2)
    intake.py          # §6 (4.3)
    deliberation.py    # §7 (4.4)
    invariants.py      # §8 check registry (4.5)
    authorization.py   # §9 policy load + tier derivation + enforce_authority (4.6)
    execution.py       # §10 executor protocol + dispatch + verification (4.7)
    audit.py           # §11 AuditLogWriter, hash chain (4.8)
    governor.py        # pipeline orchestration: the corrected successor of the
                       # baseline's CorpusCallosumGovernor.evaluate, driven by
                       # the §4.6 state machine
tests/test_phase4_*.py, tests/run_phase4.sh   # §12 (4.9)
```

Directives:

- **M-1.** `audit.py` and `authorization.py` are the factored integrity
  machinery (ADR-001's Phase-4 follow-up): separate modules, no imports from
  the AI core's tool/model layer, and the audit file path is configurable
  only through `NEURAI_INTEGRITY_DIR` or explicit operator configuration —
  never through a hemisphere-supplied value.
- **M-2.** The v2.0.0 core (`neurai/neurai.py`) is not rewritten by Phase 4.
  Integration is one-directional: when the core wants an action governed, it
  submits records through intake and dispatches only `COMMITTED` decisions
  through §10. Wiring the core's existing tools (self_patch, sys_write, …)
  through the governor is the integration step that follows AT-suite
  completion, using the policy table ratified per OQ-2.
- **M-3.** No new runtime dependencies. No network access in any governance
  module. Deterministic behaviour under test: clock and ID generation are
  injectable for tests, never decision inputs (§0).

## 14. Traceability

**Roadmap PHASE-4 acceptance criteria** → this specification:

| Roadmap criterion | Satisfied by |
|---|---|
| Original input remains accessible | §4.2 `original_input_reference`, §6 I-3, AT-13 |
| Material disagreement is preserved | §4.4, §7.4, §11.3, AT-01 |
| The CC cannot bypass enforced permissions through ordinary reasoning output | §9 (policy-derived tiers), §10 E-1/E-3, AT-03, AT-15, AT-17 |
| Proposed changes are distinguishable from committed changes | §4.6 state model, AT-02 (and committed from *executed*: §4.7, AT-11) |
| Governance-critical changes require the stricter review path | §9.3, §9.4, AT-15 |

**Roadmap PHASE-4 deliverables**: routing_and_coordination (§6, basic form;
adaptive modes are Phase 5), interpretation_reconciliation (§7),
disagreement_records (§4.4), permission_enforcement (§9),
update_registry (the §9.1 action policy), commit_and_rollback_service (§10.3
with SVC-007). **self_model_verification: deferred to a Phase 4 addendum** —
it is in the roadmap's deliverable list but not in the operator's 4.1–4.9
sequence; recorded here as a known, deliberate gap (OQ-4), not an oversight.

**ADR-001 requirements**: free tier controlled by staging/validation/backup/
records rather than approval (§9.2, §10.3); gated_integrity machinery factored
and protected (§9.3, §11, M-1); gated_irreversible per-action-class operator
authorization (§9.4); governance changes as append-only decision-log entries
with prior/new text (§9.4); tier assignments as inspectable data whose changes
are themselves logged (§9.6, AT-16). INV-007's "no *silent* expansion" is
CHK-AUTH-01 + §9.6 working together.

## 15. Open questions (operator decisions — not implementer discretion)

Until each is decided, the stated interim behaviour is mandatory.

- **OQ-1 — Deliberation termination.** §7 gives material divergence exactly
  two exits: convergence or operator resolution. There is deliberately no
  round limit and no auto-escalation. *Interim: as specified.* Decide: keep,
  or set a round count after which the CC must escalate to the operator?
- **OQ-2 — Initial action policy.** Appendix B's table for the current core's
  tool surface is a **proposal**. It takes effect only when the operator
  ratifies it (recorded as a governance decision). Tests use their own
  fixture policies and do not depend on ratification. *Interim: no production
  policy file exists; the CC runs halted for authorization (§9.5), which is
  the correct posture for an unratified policy.*
- **OQ-3 — Audit anchor cadence.** `head_hash()` exists (§11.2); how often
  the head is surfaced to the operator (per session, daily digest, on
  demand) is an operator-workflow choice. *Interim: on demand + included in
  any decision digest the operator requests.*
- **OQ-4 — verify_self_model addendum.** Scope and schedule for the deferred
  roadmap deliverable (§14). *Interim: not built; the deferral is on record.*

## Appendix A — Supplied baselines

Preserved verbatim in this directory, transcribed from the operator's
messages of 2026-11-07 and verified by execution (the v2 test passes against
the transcribed v2 baseline, 2026-11-07):

| File | SHA-256 (at preservation) |
|---|---|
| `supplied-baseline-v1.py` | `97ca4da7d63067495eb21475d265fff2ab194a407280d1c1107e56338def43ca` |
| `supplied-baseline-v2.py` | `e6aa1be100f1974d7030bc34eadec1b86b9d64e7a5ca1db4a8e2adf37e98240f` |
| `supplied-baseline-v2-test.py` | `197c8a2c8e454a9188893234dc1704848795e4e8d1a853f642796cf562091187` |

(Hashes cover the files as preserved, including their transcription header
comments.) The baselines are lineage and test-fodder (AT-01), never imports
of the implementation.

## Appendix B — Proposed initial action policy (REQUIRES OPERATOR RATIFICATION — OQ-2)

For the v2.0.0 core's existing tool surface, under ADR-001. **Not in force**
until ratified.

| action_type | tier | reversibility_class | Basis |
|---|---|---|---|
| `self_read`, `sys_read`, file/memory reads, calculation | free | readonly | No effects. |
| `self_patch` (own source) | free | reversible | ADR-001 explicit: own code is free-tier with staging, validation, backup, change record. Snapshot before execution (§10.3). |
| `self_rollback` | free | reversible | The recovery path must not itself be gated into uselessness. |
| `sys_write` — own code/config paths | free | reversible | As `self_patch`. |
| `sys_write` — integrity-machinery targets | gated_integrity | — | §9.3 target escalation, regardless of this table. |
| `sys_write` — other system paths | gated_irreversible | irreversible | Effects outside versioned state (ADR-001). |
| `memory_write` (own memory content) | free | reversible | ADR-001 lists memory content in the free scope. |
| `spawn_agent` | free | conditional | Resource use only; bounded by SVC-006 budgets (Phase 7 concern). |
| `shell_exec` | gated_irreversible | irreversible | **Weakest point in this table, flagged honestly:** arbitrary shell cannot be statically classified. A policy-maintained read-only command allowlist may be carved out as free/readonly; everything else defaults gated. Ratification should focus here. |
| `external_send` (communicating as the operator) | gated_irreversible | irreversible | ADR-001 explicit. (Consistent with the operator's standing approve-then-send rule.) |
| `spend_money` | gated_irreversible | irreversible | ADR-001 explicit. |
| `disk_destructive` (format, partition, raw device write) | gated_irreversible | irreversible | ADR-001 explicit ("destructive disk-level operations"). |
| `delete_unversioned` | gated_irreversible | irreversible | ADR-001 explicit ("destroying unversioned data"). |

— End of PHASE4-SPEC-001 v1.0.0 —
