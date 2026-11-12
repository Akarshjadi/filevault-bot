"""The `Event` primitive: the single exchange type of the IntentLock pipeline.

Design notes
------------
Every event carries not only *what* was perceived but *when*, *how sure*, and
*who*. The `subject_id` field is what makes identity consistency expressible as
a temporal constraint: a fusion model can ask "are all events in this sequence
about the same pseudonymous subject?" without any raw biometric being present.

Timestamps are **monotonic nanoseconds since the owning clock's epoch**, taken
from a single session clock (`intentlock.synchronization.clock.SessionClock`).
Nanoseconds-as-int removes float drift when comparing events separated by
milliseconds, and keeps ordering exact.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from intentlock.common.errors import MalformedInputError


class Source(str, Enum):
    """Which perception subsystem produced an event."""

    AUDIO = "audio"
    VISION = "vision"
    IMU = "imu"
    SYSTEM = "system"  # state-machine / clock events, not a physical modality


class EventType(str, Enum):
    """The closed vocabulary of things the temporal engine understands.

    Keeping this an enum (rather than free-form strings) means an unknown event
    type is a programming error caught at test time, not a silent no-op at
    runtime.
    """

    # --- audio ---
    WAKE_DETECTED = "wake_detected"
    AUDIO_SILENCE = "audio_silence"
    AUDIO_NOISE_HIGH = "audio_noise_high"

    # --- vision ---
    PERSON_PRESENT = "person_present"
    PERSON_ABSENT = "person_absent"
    ATTENTION_ENGAGED = "attention_engaged"
    ATTENTION_DISENGAGED = "attention_disengaged"
    IDENTITY_KNOWN = "identity_known"
    IDENTITY_UNKNOWN = "identity_unknown"
    IDENTITY_AMBIGUOUS = "identity_ambiguous"
    VISION_OCCLUDED = "vision_occluded"

    # --- imu ---
    GESTURE_DETECTED = "gesture_detected"
    NO_GESTURE = "no_gesture"
    IMU_ANOMALY = "imu_anomaly"

    # --- system ---
    SENSOR_UNAVAILABLE = "sensor_unavailable"
    SENSOR_RECOVERED = "sensor_recovered"
    SESSION_START = "session_start"


# Event types that describe a modality observation and therefore may not be
# emitted by the generic SYSTEM source.
_BEHAVIOURAL_TYPES = frozenset(
    {
        EventType.WAKE_DETECTED,
        EventType.PERSON_PRESENT,
        EventType.PERSON_ABSENT,
        EventType.ATTENTION_ENGAGED,
        EventType.ATTENTION_DISENGAGED,
        EventType.IDENTITY_KNOWN,
        EventType.IDENTITY_UNKNOWN,
        EventType.IDENTITY_AMBIGUOUS,
        EventType.VISION_OCCLUDED,
        EventType.GESTURE_DETECTED,
        EventType.NO_GESTURE,
        EventType.IMU_ANOMALY,
        EventType.AUDIO_SILENCE,
        EventType.AUDIO_NOISE_HIGH,
    }
)

# Negative events express the *absence* of something. The safety gate treats
# them as active evidence against execution, not as missing data.
_NEGATIVE_TYPES = frozenset(
    {
        EventType.PERSON_ABSENT,
        EventType.NO_GESTURE,
        EventType.AUDIO_SILENCE,
        EventType.VISION_OCCLUDED,
        EventType.ATTENTION_DISENGAGED,
        EventType.IMU_ANOMALY,
    }
)

_MAX_CONFIDENCE_EPS = 1e-9


@dataclass(frozen=True, slots=True)
class Event:
    """An immutable, timestamped, confidence-scored observation.

    Attributes
    ----------
    event_type:
        What happened. Restricting this to `EventType` keeps the temporal
        engine's vocabulary closed and testable.
    timestamp_ns:
        Monotonic nanoseconds on the owning session clock.
    confidence:
        Calibrated probability in [0, 1] that the event genuinely occurred.
        For `NO_GESTURE`/`PERSON_ABSENT`/`AUDIO_SILENCE` the confidence is the
        probability of the *negative* observation.
    source:
        Producing subsystem.
    subject_id:
        Pseudonymous identity hash of the person this event is about, or None
        when the modality cannot attribute the observation to a person (e.g. a
        wake-word detection with no speaker identification).
    label:
        Optional discrete label, e.g. the gesture name for `GESTURE_DETECTED`.
        Stored as a plain string so models can add labels without a schema
        change, but validated against the configured vocabulary by the producer.
    event_id:
        Stable identifier used for deduplication. Deterministic by default so
        that replaying the same log produces identical events.
    metadata:
        Free-form, machine-readable extras (signal quality, noise estimate,
        model version, ...). Must be JSON-serialisable.
    """

    event_type: EventType
    timestamp_ns: int
    confidence: float
    source: Source
    subject_id: str | None = None
    label: str | None = None
    event_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # --- untrusted-input validation at the trust boundary ---
        if isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int):
            raise MalformedInputError(f"timestamp_ns must be int, got {type(self.timestamp_ns)!r}")
        if self.timestamp_ns < 0:
            raise MalformedInputError(f"timestamp_ns must be non-negative, got {self.timestamp_ns}")

        conf = self.confidence
        if isinstance(conf, bool) or not isinstance(conf, (int, float)):
            raise MalformedInputError(f"confidence must be a real number, got {type(conf)!r}")
        conf_f = float(conf)
        if math.isnan(conf_f) or math.isinf(conf_f):
            raise MalformedInputError("confidence must be finite")
        # Tolerate tiny floating-point overshoot from softmax implementations by
        # clamping instead of rejecting: a 1.0000000001 must not abort a session.
        if conf_f < -_MAX_CONFIDENCE_EPS or conf_f > 1.0 + _MAX_CONFIDENCE_EPS:
            raise MalformedInputError(f"confidence must be in [0, 1], got {conf_f}")
        object.__setattr__(self, "confidence", min(1.0, max(0.0, conf_f)))

        if self.subject_id is not None and not isinstance(self.subject_id, str):
            raise MalformedInputError("subject_id must be a string or None")
        if self.label is not None and not isinstance(self.label, str):
            raise MalformedInputError("label must be a string or None")
        if not isinstance(self.event_id, str):
            raise MalformedInputError("event_id must be a string")
        if not isinstance(self.metadata, Mapping):
            raise MalformedInputError("metadata must be a mapping")

        if self.source is Source.SYSTEM and self.event_type in _BEHAVIOURAL_TYPES:
            raise MalformedInputError(
                f"{self.event_type.value} cannot originate from the system source"
            )

    # -- derived properties -------------------------------------------------

    @property
    def duration_ns(self) -> int:
        """Declared span of the observation, or 0 for instantaneous events."""
        value = self.metadata.get("duration_ns", 0)
        return int(value) if isinstance(value, (int, float)) else 0

    def is_negative(self) -> bool:
        """True for events expressing the absence of something."""
        return self.event_type in _NEGATIVE_TYPES

    # -- helpers ------------------------------------------------------------

    def with_confidence(self, confidence: float) -> Event:
        """Return a copy with a different confidence (e.g. after calibration)."""
        return replace(self, confidence=confidence)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable representation, used by telemetry and the simulator."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp_ns": self.timestamp_ns,
            "confidence": self.confidence,
            "source": self.source.value,
            "subject_id": self.subject_id,
            "label": self.label,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Event:
        """Inverse of `to_dict`, with validation.

        Replay files and sensor packets both travel through here, so a malformed
        payload must fail loudly rather than produce a half-valid event.

        Raises
        ------
        MalformedInputError
            If the payload is missing required keys or holds invalid values.
        """
        try:
            event_type = EventType(payload["event_type"])
            source = Source(payload["source"])
            timestamp_ns = int(payload["timestamp_ns"])
            confidence = float(payload["confidence"])
        except KeyError as exc:
            raise MalformedInputError(f"event payload missing key: {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise MalformedInputError(f"event payload invalid: {exc}") from exc

        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, Mapping):
            raise MalformedInputError("event metadata must be a mapping")

        return cls(
            event_type=event_type,
            timestamp_ns=timestamp_ns,
            confidence=confidence,
            source=source,
            subject_id=payload.get("subject_id"),
            label=payload.get("label"),
            event_id=str(payload.get("event_id", "")),
            metadata=dict(metadata),
        )

    def __str__(self) -> str:  # concise form for logs and demo output
        label = f":{self.label}" if self.label else ""
        return (
            f"[{self.timestamp_ns}] {self.source.value}/{self.event_type.value}{label} "
            f"p={self.confidence:.3f}"
        )
