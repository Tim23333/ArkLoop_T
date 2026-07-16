import unittest
from types import SimpleNamespace

from src.axis.adaptive_timing import AdaptivePlaybackTiming


class AdaptivePlaybackTimingTests(unittest.TestCase):
    def _timing(self) -> AdaptivePlaybackTiming:
        config = SimpleNamespace(
            FRAME_STEP_PULSE_INITIAL=0.008,
            FRAME_STEP_PULSE_MIN=0.006,
            FRAME_STEP_PULSE_MAX=0.024,
        )
        return AdaptivePlaybackTiming.from_config(config)

    def test_slow_input_and_frame_jumps_enter_precise_pause_earlier(self):
        timing = self._timing()
        timing.observe_frame(10, observed_at=1.0)
        timing.observe_frame(14, observed_at=1.1)
        timing.observe_input_latency(0.12)

        lead = timing.precise_pause_lead(base_frames=10, maximum_frames=24)

        self.assertGreater(lead, 10)
        self.assertLessEqual(lead, 24)

    def test_empty_pulses_grow_and_multiframe_jump_shrinks_pulse(self):
        timing = self._timing()
        initial = timing.pulse_seconds

        timing.record_step(0)
        grown = timing.pulse_seconds
        timing.record_step(3)

        self.assertGreater(grown, initial)
        self.assertLess(timing.pulse_seconds, grown)
        self.assertGreaterEqual(timing.pulse_seconds, timing.min_pulse_seconds)


if __name__ == "__main__":
    unittest.main()
