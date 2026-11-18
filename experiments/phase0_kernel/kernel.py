#!/usr/bin/env python3
"""Isolated Phase-0 cognitive-kernel experiment; not a production authority layer."""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Interpretation:
    component_id: str
    interpretation: str
    proposed_next_step: str
    confidence: float = 0.5  # Descriptive metadata only; never a selection rule.
    evidence_refs: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.component_id not in {"H-A", "H-B"}:
            raise ValueError("component_id must be H-A or H-B")
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be finite and between 0 and 1")


@dataclass(frozen=True)
class Event:
    event_id: str
    timestamp: str
    kind: str
    provenance: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class Disagreement:
    disagreement_id: str
    trigger_event_id: str
    interpretation_a: Interpretation
    interpretation_b: Interpretation
    status: str = "open"


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    events: tuple[Event, ...]


class EventLog:
    """Append-only writer API for JSONL events; not a tamper-evident audit log."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._events: list[Event] = []
        self._lock = threading.Lock()
        if path and path.exists():
            with path.open("r", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    try:
                        self._events.append(Event(**json.loads(line)))
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        raise ValueError(f"Invalid event log at line {line_number}: {exc}") from exc

    def append(self, kind: str, provenance: str, payload: dict[str, Any]) -> Event:
        event = Event(
            event_id=f"evt_{uuid.uuid4().hex}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            kind=kind,
            provenance=provenance,
            payload=copy.deepcopy(payload),
        )
        with self._lock:
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(asdict(event), ensure_ascii=False, sort_keys=True) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            self._events.append(event)
        return copy.deepcopy(event)

    def snapshot(self, limit: int = 20) -> Snapshot:
        if limit < 1:
            raise ValueError("limit must be positive")
        return Snapshot(f"snap_{len(self._events)}", tuple(copy.deepcopy(self._events[-limit:])))

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(copy.deepcopy(self._events))


class Interpreter(Protocol):
    def interpret(self, component_id: str, text: str, snapshot: Snapshot) -> Interpretation: ...


class OllamaInterpreter:
    """Small local Ollama adapter. Each instance may use a different model."""

    def __init__(self, model: str, endpoint: str = "http://127.0.0.1:11434/api/generate", timeout: int = 90) -> None:
        if not model.strip():
            raise ValueError("model name cannot be empty")
        self.model, self.endpoint, self.timeout = model, endpoint, timeout

    def interpret(self, component_id: str, text: str, snapshot: Snapshot) -> Interpretation:
        prompt = (
            "You are one independent cognitive component of NeurAI, a unified entity. "
            "Do not adopt a fixed persona, specialty, temperament, or authority based on your component ID. "
            "Analyze the original user input independently. Return only JSON with keys: "
            "interpretation, proposed_next_step, confidence, evidence_refs, uncertainties.\n"
            f"Component: {component_id}\nOriginal input: {text}\n"
            f"Recent shared events: {json.dumps([asdict(e) for e in snapshot.events[-8:]], ensure_ascii=False)}"
        )
        body = json.dumps({"model": self.model, "prompt": prompt, "stream": False, "format": "json"}).encode()
        request = urllib.request.Request(self.endpoint, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            data = json.loads(result["response"])
            return Interpretation(
                component_id=component_id,
                interpretation=str(data["interpretation"]),
                proposed_next_step=str(data["proposed_next_step"]),
                confidence=float(data.get("confidence", 0.5)),
                evidence_refs=tuple(map(str, data.get("evidence_refs", []))),
                uncertainties=tuple(map(str, data.get("uncertainties", []))),
            )
        except (urllib.error.URLError, TimeoutError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Local model {self.model!r} failed to return a valid interpretation: {exc}") from exc


class TrialKernel:
    """Routes identical input/snapshot to both interpreters and preserves divergence."""

    def __init__(self, interpreter_a: Interpreter, interpreter_b: Interpreter, log: EventLog) -> None:
        self.interpreter_a, self.interpreter_b, self.log = interpreter_a, interpreter_b, log
        self.disagreements: list[Disagreement] = []

    def process(self, text: str, provenance: str = "user_supplied") -> tuple[Interpretation, Interpretation, Disagreement | None]:
        trigger = self.log.append("input_received", provenance, {"text": text})
        snapshot = self.log.snapshot()
        # Both components receive the same original input and same snapshot.
        a = self.interpreter_a.interpret("H-A", text, snapshot)
        b = self.interpreter_b.interpret("H-B", text, snapshot)
        diverged = (a.interpretation, a.proposed_next_step) != (b.interpretation, b.proposed_next_step)
        disagreement = None
        if diverged:
            disagreement = Disagreement(
                disagreement_id=f"dis_{uuid.uuid4().hex}",
                trigger_event_id=trigger.event_id,
                interpretation_a=a,
                interpretation_b=b,
            )
            self.disagreements.append(disagreement)
            self.log.append("disagreement_recorded", "system_generated", asdict(disagreement))
        self.log.append("interpretations_completed", "system_generated", {"H-A": asdict(a), "H-B": asdict(b)})
        return a, b, disagreement


class FixedInterpreter:
    """Deterministic test/demo interpreter; replace with independent model adapters."""

    def __init__(self, interpretation: str, next_step: str, confidence: float = 0.5) -> None:
        self.interpretation, self.next_step, self.confidence = interpretation, next_step, confidence
        self.received: list[tuple[str, str, str]] = []

    def interpret(self, component_id: str, text: str, snapshot: Snapshot) -> Interpretation:
        self.received.append((component_id, text, snapshot.snapshot_id))
        return Interpretation(component_id, self.interpretation, self.next_step, self.confidence,
                              evidence_refs=(snapshot.snapshot_id,))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-a", required=True, help="Local Ollama model for H-A")
    parser.add_argument("--model-b", required=True, help="Local Ollama model for H-B")
    parser.add_argument("--log", type=Path, default=Path("phase0-events.jsonl"))
    args = parser.parse_args()
    kernel = TrialKernel(OllamaInterpreter(args.model_a), OllamaInterpreter(args.model_b), EventLog(args.log))
    print("Phase-0 experiment only. No tools or executive actions are available. Type /quit to exit.")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if text == "/quit":
            return 0
        if not text:
            continue
        try:
            a, b, disagreement = kernel.process(text)
            print("\nH-A:", a.interpretation, "\nNext:", a.proposed_next_step)
            print("\nH-B:", b.interpretation, "\nNext:", b.proposed_next_step)
            print("\nDivergence:", "preserved" if disagreement else "none detected", "\n")
        except RuntimeError as exc:
            print(f"Model error: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
