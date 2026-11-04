"""Properties of the recording-session statechart."""
import itertools
import unittest

from demo_director.session import (
    EDGES, FAILURES, Event, InvalidTransition, Phase, Session, StepSignals, run_step, transition,
)


class StatechartTests(unittest.TestCase):
    def test_only_capture_adds_a_beat(self):
        for (phase, event), (nxt, delta) in EDGES.items():
            if delta:
                self.assertEqual(event, Event.CAPTURE)
                self.assertEqual(nxt, Phase.CAPTURED)
                self.assertEqual(delta, 1)
            if phase in FAILURES or nxt in FAILURES:
                self.assertEqual(delta, 0)
        for phase in FAILURES:
            self.assertFalse(any(src is phase for (src, _event) in EDGES))

    def test_every_step_is_one_beat_or_a_typed_failure(self):
        timeouts = (None, "navigating", "acting", "settling")
        flags = (False, True)
        starts = (Session.idle(), Session(Phase.CAPTURED, beats=4))
        for start in starts:
            for needs, found, timeout_at, pii, pii_after in itertools.product(
                flags, flags, timeouts, flags, flags
            ):
                out = run_step(start, StepSignals(needs, found, timeout_at, pii, pii_after))
                captured = out.phase is Phase.CAPTURED
                failed = out.phase in FAILURES
                self.assertNotEqual(captured, failed)
                self.assertEqual(out.beats, start.beats + (1 if captured else 0))
                if failed:
                    self.assertEqual(out.failure, out.phase.value)
                    self.assertIn(out.failure, {"selector-missing", "timeout", "pii-blocked"})
                else:
                    self.assertIsNone(out.failure)
                    self.assertFalse(out.pii_seen)
                if pii or pii_after:
                    self.assertIs(out.phase, Phase.PII_BLOCKED)
                    self.assertTrue(out.pii_seen)
                    self.assertEqual(out.beats, start.beats)

    def test_pii_blocks_capture_permanently(self):
        blocked = run_step(Session.idle(), StepSignals(True, True, None, True, False))
        self.assertIs(blocked.phase, Phase.PII_BLOCKED)
        self.assertEqual(blocked.beats, 0)
        with self.assertRaises(InvalidTransition):
            transition(blocked, Event.CAPTURE)
        with self.assertRaises(InvalidTransition):
            run_step(blocked, StepSignals(False, True, None, False, False))

    def test_clean_steps_capture_one_each(self):
        first = run_step(Session.idle(), StepSignals(True, True, None, False, False))
        self.assertIs(first.phase, Phase.CAPTURED)
        self.assertEqual(first.beats, 1)
        second = run_step(first, StepSignals(False, False, None, False, False))
        self.assertEqual(second.beats, 2)
        self.assertIs(second.phase, Phase.CAPTURED)

    def test_pii_after_blocks_a_clean_start(self):
        out = run_step(Session.idle(), StepSignals(True, True, None, False, True))
        self.assertIs(out.phase, Phase.PII_BLOCKED)
        self.assertEqual(out.beats, 0)

    def test_illegal_edge_is_not_a_beat(self):
        with self.assertRaises(InvalidTransition):
            transition(Session.idle(), Event.CAPTURE)
