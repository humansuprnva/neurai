import tempfile
import unittest
from pathlib import Path

from experiments.phase0_kernel.kernel import EventLog, FixedInterpreter, TrialKernel


class Phase0KernelTests(unittest.TestCase):
    def test_both_components_receive_same_input_and_snapshot(self):
        a = FixedInterpreter("A view", "probe A", 0.99)
        b = FixedInterpreter("B view", "probe B", 0.01)
        kernel = TrialKernel(a, b, EventLog())
        _, _, disagreement = kernel.process("same original input")
        self.assertEqual(a.received[0][1:], b.received[0][1:])
        self.assertIsNotNone(disagreement)
        self.assertEqual(disagreement.status, "open")

    def test_confidence_does_not_resolve_disagreement(self):
        a = FixedInterpreter("A view", "probe A", 0.99)
        b = FixedInterpreter("B view", "probe B", 0.01)
        _, _, disagreement = TrialKernel(a, b, EventLog()).process("question")
        self.assertIsNotNone(disagreement)
        self.assertEqual(disagreement.interpretation_a.confidence, 0.99)
        self.assertEqual(disagreement.interpretation_b.confidence, 0.01)

    def test_event_log_reloads_and_uses_unique_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            first = EventLog(path)
            one = first.append("one", "user_supplied", {"text": "a"})
            two = first.append("two", "user_supplied", {"text": "b"})
            self.assertNotEqual(one.event_id, two.event_id)
            loaded = EventLog(path)
            self.assertEqual([event.kind for event in loaded.events], ["one", "two"])

    def test_event_payload_access_is_copied(self):
        log = EventLog()
        event = log.append("input", "user_supplied", {"nested": {"x": 1}})
        event.payload["nested"]["x"] = 9
        self.assertEqual(log.events[0].payload["nested"]["x"], 1)

    def test_agreement_does_not_create_disagreement(self):
        a = FixedInterpreter("same", "same next step", 0.2)
        b = FixedInterpreter("same", "same next step", 0.9)
        _, _, disagreement = TrialKernel(a, b, EventLog()).process("question")
        self.assertIsNone(disagreement)


if __name__ == "__main__":
    unittest.main()
