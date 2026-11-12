"""Ordered interaction patterns: the behavioural handshake, expressed as data.

Design note
-----------
The handshake is *not* a special case in code. It is a `SequencePattern`: an
ordered list of steps with per-gap temporal budgets. Stating it as data means

* the temporal constraints have one source of truth (configuration),
* a violation can be reported with the specific step that failed, which is what
  makes REJECT reasons explainable in telemetry,
* and a new interaction pattern (e.g. adding a second confirmation) is a
  configuration-shaped change rather than a code change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from intentlock.common.events import EventType
from intentlock.configuration.schema import TemporalConfig


class ViolationKind(str, Enum):
    """Why an event sequence was rejected. Machine-readable for telemetry."""

    ORDER_INVERTED = "order_inverted"
    GAP_TOO_LARGE = "gap_too_large"
    GAP_TOO_SMALL = "gap_too_small"
    MISSING_STEP = "missing_step"
    EXPIRED = "expired"
    SEQUENCE_TOO_LONG = "sequence_too_long"
    SUBJECT_MISMATCH = "subject_mismatch"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class SequenceStep:
    """One required step in an ordered interaction pattern.

    Attributes
    ----------
    event_type:
        The event that satisfies this step.
    max_gap_ms:
        Maximum allowed gap from the *previous matched step*. None means
        unbounded, which is only appropriate for the first step.
    min_gap_ms:
        Minimum separation from the previous step. Guards against two events
        that are really one detection artefact (e.g. a wake word whose echo is
        re-detected 20 ms later).
    optional:
        When True the step may be absent without invalidating the sequence, but
        if present it must appear in this position.
    """

    event_type: EventType
    max_gap_ms: int | None
    min_gap_ms: int = 0
    optional: bool = False


@dataclass(frozen=True, slots=True)
class SequencePattern:
    """A named, ordered interaction pattern.

    Attributes
    ----------
    name:
        Identifier used in telemetry (`"handshake"`, `"wake_gesture"`).
    steps:
        Ordered steps. Order is the point: the pattern does not match if the
        observed order differs.
    max_duration_ms:
        Overall budget from first to last matched step.
    require_same_subject:
        When True, every step that carries a `subject_id` must agree. Steps with
        `subject_id is None` are exempt, because a wake word often cannot be
        attributed to a person.
    """

    name: str
    steps: tuple[SequenceStep, ...]
    max_duration_ms: int
    require_same_subject: bool = True

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError(f"pattern {self.name!r} must have at least one step")
        if self.steps[0].max_gap_ms is not None:
            raise ValueError(
                f"pattern {self.name!r}: the first step must have max_gap_ms=None "
                "(it is matched on its own, with no predecessor)"
            )
        for step in self.steps[1:]:
            if step.max_gap_ms is None:
                raise ValueError(
                    f"pattern {self.name!r}: only the first step may have max_gap_ms=None"
                )


# --------------------------------------------------------------------------
# Built-in patterns.
#
# The canonical handshake is person -> wake -> gesture. It is the pattern the
# safety gate requires for EXECUTE when `require_verified_identity` is set.
# --------------------------------------------------------------------------


def build_patterns(temporal: TemporalConfig) -> dict[str, SequencePattern]:
    """Construct the standard patterns from temporal configuration.

    Patterns are derived from configuration rather than module constants so the
    timing budgets have exactly one source of truth.
    """
    person_to_wake = temporal.person_to_wake_max_ms
    wake_to_gesture = temporal.wake_to_gesture_max_ms
    gesture_to_confirm = temporal.gesture_to_confirm_max_ms
    min_gap = temporal.wake_to_gesture_min_ms

    return {
        # The full behavioural handshake, with optional explicit confirmation.
        # The confirmation step exists so that a two-gesture sequence
        # (command + confirm) is representable without a code change.
        "handshake": SequencePattern(
            name="handshake",
            steps=(
                SequenceStep(EventType.PERSON_PRESENT, max_gap_ms=None),
                SequenceStep(EventType.WAKE_DETECTED, max_gap_ms=person_to_wake),
                SequenceStep(
                    EventType.GESTURE_DETECTED,
                    max_gap_ms=wake_to_gesture,
                    min_gap_ms=min_gap,
                ),
                SequenceStep(
                    EventType.GESTURE_DETECTED,
                    max_gap_ms=gesture_to_confirm,
                    min_gap_ms=min_gap,
                    optional=True,
                ),
            ),
            max_duration_ms=temporal.max_sequence_ms,
            require_same_subject=True,
        ),
        # Wake then gesture, with no identity evidence. Used by the
        # "no identity" baseline and by the gate when identity is not required.
        "wake_gesture": SequencePattern(
            name="wake_gesture",
            steps=(
                SequenceStep(EventType.WAKE_DETECTED, max_gap_ms=None),
                SequenceStep(
                    EventType.GESTURE_DETECTED,
                    max_gap_ms=wake_to_gesture,
                    min_gap_ms=min_gap,
                ),
            ),
            max_duration_ms=temporal.max_sequence_ms,
            require_same_subject=False,
        ),
        # Gesture with no wake word: the accidental-command case the system
        # exists to reject. Modelled as a pattern so the failure is reported as
        # MISSING_STEP rather than as a special case in code.
        "gesture_only": SequencePattern(
            name="gesture_only",
            steps=(SequenceStep(EventType.GESTURE_DETECTED, max_gap_ms=None),),
            max_duration_ms=temporal.max_sequence_ms,
            require_same_subject=False,
        ),
    }
