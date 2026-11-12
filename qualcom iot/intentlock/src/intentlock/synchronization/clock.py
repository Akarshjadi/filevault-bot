"""Monotonic session clock.

Why a dedicated clock
---------------------
Sensor timestamps arrive from three independent devices whose clocks are not
synchronised, and wall-clock time can jump backwards (NTP step, DST). Every
temporal decision in IntentLock must be monotonic and comparable, so all
timestamps are converted to nanoseconds on one monotonic session origin.

The clock is also the injection point for the simulator: replaying a session
with a virtual clock makes "expired sequence" and "timeout" paths deterministic
and testable without sleeping.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol


class Clock(Protocol):
    """A monotonic nanosecond time source."""

    def now_ns(self) -> int:
        """Current time in nanoseconds. Never decreases."""
        ...


class RealClock:
    """Monotonic clock backed by `time.monotonic_ns`.

    `time.monotonic_ns` is immune to wall-clock adjustments, which is the
    property that matters for sequence timing.
    """

    __slots__ = ()

    def now_ns(self) -> int:
        return time.monotonic_ns()


@dataclass
class VirtualClock:
    """Manually advanced clock for replay and tests.

    Advanced only forwards, mirroring the monotonicity of the real clock.
    """

    _now_ns: int = 0

    def now_ns(self) -> int:
        return self._now_ns

    def advance_ns(self, delta_ns: int) -> int:
        if delta_ns < 0:
            raise ValueError("VirtualClock cannot move backwards")
        self._now_ns += delta_ns
        return self._now_ns

    def advance_ms(self, delta_ms: int) -> int:
        return self.advance_ns(delta_ms * 1_000_000)

    def set_ns(self, value_ns: int) -> int:
        """Jump forward to an absolute virtual time (never backwards)."""
        if value_ns < self._now_ns:
            raise ValueError(f"VirtualClock cannot move backwards ({value_ns} < {self._now_ns})")
        self._now_ns = value_ns
        return self._now_ns


# A device timestamp is treated as already session-relative only when it is
# close to session time relative to the magnitude of session time itself.
# A device-local counter starting near zero while the session is already seconds
# in is therefore correctly detected as device-local.
_ALIGNMENT_ABS_NS = 5_000_000  # 5 ms absolute floor, below which "close" is noise


def _looks_session_relative(raw_timestamp_ns: int, now_ns: int) -> bool:
    """Whether `raw_timestamp_ns` is plausibly already expressed in session time.

    Both a simulator emitting session time and a device-local counter starting at
    zero produce small numbers early in a session, so magnitude alone is not
    enough. The distinguishing test is *proportional* closeness: a timestamp is
    session-relative if it is within a small fraction of the elapsed session time
    (with an absolute floor for the very start of a session).
    """
    tolerance = max(_ALIGNMENT_ABS_NS, now_ns // 20)  # 5% of elapsed session time
    return abs(raw_timestamp_ns - now_ns) <= tolerance


@dataclass
class SessionClock:
    """Maps device timestamps onto a single monotonic session timeline.

    Each device has an independent origin; the first timestamp seen from a device
    establishes its offset, and all later timestamps from that device are
    expressed relative to the session origin.
    """

    clock: Clock = field(default_factory=RealClock)
    started_at_ns: int = 0
    _device_offsets: dict[str, int] = field(default_factory=dict, init=False)
    _observations: dict[str, int] = field(default_factory=dict, init=False)
    _started: bool = field(default=False, init=False)

    def start(self) -> int:
        """Begin the session. Idempotent; returns the session origin."""
        if not self._started:
            self.started_at_ns = self.clock.now_ns()
            self._device_offsets.clear()
            self._observations.clear()
            self._started = True
        return self.started_at_ns

    @property
    def started(self) -> bool:
        """Whether `start` has been called."""
        return self._started

    def now_ns(self) -> int:
        """Session-relative monotonic time in nanoseconds."""
        if not self._started:
            self.start()
        return max(0, self.clock.now_ns() - self.started_at_ns)

    def convert(self, raw_timestamp_ns: int, device: str, *, allow_late: bool = True) -> int:
        """Convert a device timestamp into session time.

        Parameters
        ----------
        raw_timestamp_ns:
            Timestamp as reported by the device. If it is within
            `_ALIGNMENT_WINDOW_NS` of the session origin it is treated as
            already session-relative; otherwise the first observation from this
            device establishes its offset.
        device:
            Device identifier used as the offset key.
        allow_late:
            When True, a timestamp that maps into the future is clamped to the
            current session time. Late/early arrivals are normal (a camera frame
            can be delivered after capture; an MCU timestamp can lead slightly)
            and clamping keeps ordering monotonic. When False the raw conversion
            is returned unchanged, which callers use to *detect* lateness.

        Returns
        -------
        int
            Session-relative nanoseconds, never negative.
        """
        if not self._started:
            self.start()

        if device not in self._device_offsets:
            # Decide whether this device's timestamps are already session-relative
            # or device-local, using proportional closeness rather than an
            # absolute window. See `_looks_session_relative`.
            now = self.now_ns()
            if _looks_session_relative(raw_timestamp_ns, now):
                self._device_offsets[device] = 0
            else:
                # Device-local origin: this observation defines the device epoch,
                # and it maps to the moment it was received.
                self._device_offsets[device] = raw_timestamp_ns - now

        converted = raw_timestamp_ns - self._device_offsets[device]
        converted = max(0, converted)
        self._observations[device] = self._observations.get(device, 0) + 1

        if allow_late:
            now = self.now_ns()
            if converted > now:
                # Timestamp claims the future: clamp rather than accept, so that
                # ordering stays monotonic and a bad clock cannot pre-empt events.
                return now
        return converted

    def drift_report(self) -> dict[str, int]:
        """Per-device observation counts, for telemetry and diagnostics."""
        return dict(self._observations)
