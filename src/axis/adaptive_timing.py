from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class AdaptivePlaybackTiming:
    """Learn a playback timing profile for the current emulator session."""

    pulse_seconds: float
    min_pulse_seconds: float
    max_pulse_seconds: float
    frame_period_seconds: float = 1.0 / 30.0
    largest_frame_jump: int = 1
    largest_step_jump: int = 1
    input_latency_seconds: float = 0.0
    peak_input_latency_seconds: float = 0.0
    no_progress_pulses: int = 0
    _last_frame: int | None = None
    _last_frame_at: float | None = None

    @classmethod
    def from_config(cls, config: Any) -> "AdaptivePlaybackTiming":
        initial = max(0.0, float(config.FRAME_STEP_PULSE_INITIAL))
        minimum = max(0.0, float(config.FRAME_STEP_PULSE_MIN))
        maximum = max(minimum, float(config.FRAME_STEP_PULSE_MAX))
        return cls(
            pulse_seconds=min(maximum, max(minimum, initial)),
            min_pulse_seconds=minimum,
            max_pulse_seconds=maximum,
        )

    def observe_frame(self, frame: int, observed_at: float | None = None) -> None:
        now = time.perf_counter() if observed_at is None else float(observed_at)
        frame = int(frame)
        previous_frame = self._last_frame
        previous_at = self._last_frame_at

        if previous_frame is not None and frame < previous_frame:
            self._last_frame = frame
            self._last_frame_at = now
            self.largest_frame_jump = 1
            return

        if previous_frame is not None and frame > previous_frame:
            delta = frame - previous_frame
            self.largest_frame_jump = max(self.largest_frame_jump, delta)
            if previous_at is not None:
                sample = (now - previous_at) / delta
                if 1.0 / 240.0 <= sample <= 0.25:
                    self.frame_period_seconds = (
                        self.frame_period_seconds * 0.8 + sample * 0.2
                    )

        if previous_frame != frame:
            self._last_frame = frame
            self._last_frame_at = now

    def observe_input_latency(self, elapsed_seconds: float) -> None:
        elapsed = max(0.0, float(elapsed_seconds))
        if self.input_latency_seconds <= 0:
            self.input_latency_seconds = elapsed
        else:
            self.input_latency_seconds = self.input_latency_seconds * 0.75 + elapsed * 0.25
        self.peak_input_latency_seconds = max(self.peak_input_latency_seconds, elapsed)

    def precise_pause_lead(self, base_frames: int, maximum_frames: int) -> int:
        base = max(1, int(base_frames))
        maximum = max(base, int(maximum_frames))
        period = max(1.0 / 240.0, self.frame_period_seconds)
        latency = max(self.input_latency_seconds, self.peak_input_latency_seconds)
        latency_frames = max(0, math.ceil(latency / period) - 1)
        jump_margin = max(0, self.largest_frame_jump - 1) * 2
        return min(maximum, base + latency_frames + jump_margin)

    def record_step(self, frame_delta: int) -> None:
        delta = int(frame_delta)
        if delta <= 0:
            self.no_progress_pulses += 1
            self.pulse_seconds = min(
                self.max_pulse_seconds,
                max(self.pulse_seconds + 0.002, self.pulse_seconds * 1.15),
            )
            return

        self.no_progress_pulses = 0
        self.largest_step_jump = max(self.largest_step_jump, delta)
        if delta > 1:
            self.pulse_seconds = max(
                self.min_pulse_seconds,
                self.pulse_seconds / float(delta),
            )

    def snapshot(self) -> Dict[str, Any]:
        return {
            "pulse_ms": round(self.pulse_seconds * 1000.0, 2),
            "frame_period_ms": round(self.frame_period_seconds * 1000.0, 2),
            "largest_frame_jump": self.largest_frame_jump,
            "largest_step_jump": self.largest_step_jump,
            "input_latency_ms": round(self.input_latency_seconds * 1000.0, 2),
            "peak_input_latency_ms": round(self.peak_input_latency_seconds * 1000.0, 2),
            "no_progress_pulses": self.no_progress_pulses,
        }
