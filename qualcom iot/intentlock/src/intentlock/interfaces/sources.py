"""Sensor source protocols: the hardware abstraction boundary.

Every physical device is behind one of these protocols. Application code depends
only on the protocol, so replacing the Modulino IMU with another IMU, or the
microphone with a file reader, is a constructor change rather than a rewrite.

Two rules hold for every implementation:

1. `poll()` must never block indefinitely and must never raise for a *degradable*
   condition (sensor missing, corrupt packet). It returns an event with reduced
   confidence, or `None`. It raises only `ConfigurationError` for a genuinely
   misconfigured backend.
2. `available` reflects the last known health, so the state machine can react to
   sensor loss rather than hanging.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from intentlock.common.events import Event


@runtime_checkable
class EventSource(Protocol):
    """Anything that produces `Event`s on demand.

    This is the seam that makes the simulator, the replay loader and the real
    hardware interchangeable.
    """

    @property
    def name(self) -> str:
        """Short identifier used in logs and telemetry (e.g. ``"imu"``)."""
        ...

    @property
    def available(self) -> bool:
        """Whether the underlying device is currently usable."""
        ...

    def start(self) -> None:
        """Acquire the device. Raises `SensorUnavailable` if it cannot be acquired."""
        ...

    def poll(self) -> list[Event]:
        """Return zero or more events since the previous call.

        Returns an empty list when nothing of interest happened. Must not block
        longer than one processing frame.
        """
        ...

    def stop(self) -> None:
        """Release the device. Must be safe to call twice."""
        ...


@runtime_checkable
class AudioSource(EventSource, Protocol):
    """Microphone-backed source capable of wake-word detection."""

    @property
    def sample_rate(self) -> int:
        """Native sample rate in Hz."""
        ...


@runtime_checkable
class VisionSource(EventSource, Protocol):
    """Camera-backed source producing presence/identity/attention events."""

    @property
    def frame_rate(self) -> float:
        """Native capture rate in frames per second."""
        ...


@runtime_checkable
class MotionSource(EventSource, Protocol):
    """IMU-backed source producing gesture events."""

    @property
    def sample_rate(self) -> int:
        """Native accelerometer/gyroscope sample rate in Hz."""
        ...

    @property
    def calibration(self) -> MotionCalibration:
        """Static per-device calibration (bias and scale) applied to raw samples."""
        ...


@runtime_checkable
class MotionCalibration(Protocol):
    """Per-device calibration applied before feature extraction.

    A real IMU has non-zero bias on every axis; the platform is never the spec
    ideal. Keeping calibration behind an interface means a calibration routine
    can be added for a new device without touching the model.
    """

    @property
    def accel_bias(self) -> tuple[float, float, float]:
        """Accelerometer bias in g."""
        ...

    @property
    def gyro_bias(self) -> tuple[float, float, float]:
        """Gyroscope bias in deg/s."""
        ...
