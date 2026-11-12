"""Temporal event engine.

Responsibilities
----------------
1. **Buffer** events in monotonic order, absorbing inter-device jitter.
2. **Deduplicate** so one physical gesture cannot be counted twice.
3. **Expire** events past their TTL, so a stale wake word cannot authorise a
   gesture minutes later.
4. **Match** ordered patterns and report *why* a sequence failed.

The engine holds no policy: it reports matches and violations; `intent` and
`safety` decide what to do about them.
"""

from __future__ import annotations

from dataclasses import dataclass

from intentlock.common.events import Event, EventType
from intentlock.configuration.schema import TemporalConfig
from intentlock.synchronization.patterns import (
    SequencePattern,
    SequenceStep,
    ViolationKind,
    build_patterns,
)

_NS_PER_MS = 1_000_000


@dataclass(frozen=True, slots=True)
class SequenceMatch:
    """Result of matching a pattern against the buffered events.

    Attributes
    ----------
    pattern_name:
        Which pattern was attempted.
    matched:
        True when every required step was satisfied in order and within budget.
    events:
        The matched events, in observed order. For an unmatched sequence this is
        the best partial prefix, which is what telemetry uses to explain failure.
    violation:
        Why the match failed, or None on success.
    detail:
        Human-readable explanation naming the offending step.
    duration_ns:
        Span from first to last matched event.
    """

    pattern_name: str
    matched: bool
    events: tuple[Event, ...]
    violation: ViolationKind | None = None
    detail: str = ""
    duration_ns: int = 0

    @property
    def is_partial(self) -> bool:
        """True when some but not all required steps matched (candidate for RETRY)."""
        return not self.matched and bool(self.events)

    def gesture_labels(self) -> list[str]:
        """Labels of matched gesture events, in order."""
        return [
            e.label
            for e in self.events
            if e.label and e.event_type is EventType.GESTURE_DETECTED
        ]


class TemporalEventEngine:
    """Ordered buffer of events with deduplication, expiry and pattern matching.

    The engine is deterministic: given the same sequence of `push` calls it
    produces the same matches and the same violation reasons.
    """

    def __init__(
        self,
        temporal: TemporalConfig,
        patterns: dict[str, SequencePattern] | None = None,
    ) -> None:
        self._temporal = temporal
        self._patterns = patterns if patterns is not None else build_patterns(temporal)
        self._buffer: list[Event] = []
        self._seen_ids: set[str] = set()
        self._duplicates_dropped = 0
        self._expired = 0

    # -- ingestion ---------------------------------------------------------

    def push(self, event: Event) -> bool:
        """Add an event to the buffer.

        Returns
        -------
        bool
            False if the event was rejected as a duplicate, True otherwise.

        Notes
        -----
        Insertion position is found by timestamp with a jitter tolerance: an
        event whose timestamp is within `ordering_jitter_ms` of an existing entry
        is placed *after* it, preserving arrival order. Without this, two devices
        with slightly different clocks would shuffle a handshake that happened in
        the right order.
        """
        if event.event_id:
            if event.event_id in self._seen_ids:
                self._duplicates_dropped += 1
                return False
            self._seen_ids.add(event.event_id)

        jitter_ns = self._temporal.ordering_jitter_ms * _NS_PER_MS
        insert_at = 0
        for index in range(len(self._buffer) - 1, -1, -1):
            if self._buffer[index].timestamp_ns <= event.timestamp_ns + jitter_ns:
                insert_at = index + 1
                break
        self._buffer.insert(insert_at, event)
        return True

    def push_all(self, events: list[Event]) -> int:
        """Push several events, returning the count accepted."""
        return sum(1 for event in events if self.push(event))

    def prune(self, now_ns: int) -> int:
        """Drop events older than the TTL relative to `now_ns`.

        Returns the number of events dropped. Also releases their dedup keys, so
        `_seen_ids` cannot grow without bound over a long session.
        """
        ttl_ns = self._temporal.event_ttl_ms * _NS_PER_MS
        keep: list[Event] = []
        dropped = 0
        for event in self._buffer:
            if now_ns - event.timestamp_ns > ttl_ns:
                dropped += 1
                if event.event_id:
                    self._seen_ids.discard(event.event_id)
            else:
                keep.append(event)
        self._buffer = keep
        self._expired += dropped
        return dropped

    # -- inspection --------------------------------------------------------

    @property
    def buffer(self) -> tuple[Event, ...]:
        """Current buffered events, oldest first."""
        return tuple(self._buffer)

    @property
    def pattern_names(self) -> tuple[str, ...]:
        """Names of the configured patterns."""
        return tuple(self._patterns)

    def stats(self) -> dict[str, int]:
        """Counters for telemetry."""
        return {
            "buffered": len(self._buffer),
            "duplicates_dropped": self._duplicates_dropped,
            "expired": self._expired,
        }

    def clear(self) -> None:
        """Reset the buffer and dedup state (used on cooldown / session restart)."""
        self._buffer.clear()
        self._seen_ids.clear()

    def latest(self, event_type: EventType) -> Event | None:
        """Most recent buffered event of `event_type`, or None."""
        for event in reversed(self._buffer):
            if event.event_type is event_type:
                return event
        return None

    # -- matching ----------------------------------------------------------

    def match(self, pattern_name: str, now_ns: int) -> SequenceMatch:
        """Match one named pattern against buffered events.

        The algorithm walks the buffered events once, advancing a cursor through
        the pattern's steps. It is greedy and left-to-right, which is the correct
        semantics for a handshake: the *earliest* valid ordered occurrence wins,
        so a stray gesture that occurred before the wake word cannot be reused as
        the command gesture.

        Raises
        ------
        KeyError
            If `pattern_name` is not configured.
        """
        pattern = self._patterns[pattern_name]
        self.prune(now_ns)

        matched_required: list[Event] = []
        matched_optional: list[Event] = []
        step_index = 0
        previous: Event | None = None
        violation: tuple[ViolationKind, str] | None = None

        cursor = 0
        events = self._buffer
        while step_index < len(pattern.steps):
            step = pattern.steps[step_index]
            outcome = self._find_step(events, cursor, step, previous, pattern)
            if outcome is None:
                if step.optional:
                    step_index += 1
                    continue
                # A required step has no candidate at all. If we already matched
                # part of the pattern this is a reportable MISSING_STEP (the
                # sequence started but cannot complete); if nothing matched, the
                # sequence simply has not begun and no violation is reported.
                if matched_required:
                    violation = (
                        ViolationKind.MISSING_STEP,
                        f"no {step.event_type.value} after "
                        f"{(previous.event_type.value if previous else 'start')} "
                        f"within {step.max_gap_ms}ms",
                    )
                break

            event, step_violation, detail, cursor = outcome
            if step_violation is not None:
                violation = (step_violation, detail)
                break

            if event is None:  # pragma: no cover - defensive, see _find_step contract
                break
            if step.optional:
                matched_optional.append(event)
            else:
                matched_required.append(event)
            previous = event
            step_index += 1

        complete = violation is None and step_index >= len(pattern.steps)
        ordered = tuple(
            sorted(matched_required + matched_optional, key=lambda e: e.timestamp_ns)
        )

        if complete and len(ordered) >= 2:
            span_ns = ordered[-1].timestamp_ns - ordered[0].timestamp_ns
            if span_ns > pattern.max_duration_ms * _NS_PER_MS:
                complete = False
                violation = (
                    ViolationKind.SEQUENCE_TOO_LONG,
                    f"sequence span {span_ns // _NS_PER_MS}ms exceeds "
                    f"budget {pattern.max_duration_ms}ms",
                )

        span = ordered[-1].timestamp_ns - ordered[0].timestamp_ns if len(ordered) >= 2 else 0
        return SequenceMatch(
            pattern_name=pattern.name,
            matched=complete,
            events=ordered,
            violation=violation[0] if violation else None,
            detail=violation[1] if violation else "",
            duration_ns=span,
        )

    def _find_step(
        self,
        events: list[Event],
        cursor: int,
        step: SequenceStep,
        previous: Event | None,
        pattern: SequencePattern,
    ) -> tuple[Event | None, ViolationKind | None, str, int] | None:
        """Search from `cursor` for an event satisfying `step`.

        Returns
        -------
        None
            No candidate event of this type exists in the buffer at all.
        tuple
            ``(event, violation, detail, new_cursor)``. A non-None violation means
            a candidate existed but was out of order or outside its gap budget;
            in that case `event` is None and the data explains the failure.
        """
        candidate_index: int | None = None
        for index in range(cursor, len(events)):
            if events[index].event_type is step.event_type:
                candidate_index = index
                break

        if candidate_index is None:
            return None

        candidate = events[candidate_index]

        if previous is not None:
            gap_ns = candidate.timestamp_ns - previous.timestamp_ns
            if gap_ns < 0:
                return (
                    None,
                    ViolationKind.ORDER_INVERTED,
                    f"{candidate.event_type.value} precedes "
                    f"{previous.event_type.value} in time",
                    candidate_index,
                )
            # SequencePattern guarantees every non-first step has max_gap_ms set.
            max_gap_ms = step.max_gap_ms or 0
            if gap_ns > max_gap_ms * _NS_PER_MS:
                return (
                    None,
                    ViolationKind.GAP_TOO_LARGE,
                    f"gap {gap_ns // _NS_PER_MS}ms between "
                    f"{previous.event_type.value} and {candidate.event_type.value} "
                    f"exceeds {max_gap_ms}ms",
                    candidate_index,
                )
            if gap_ns < step.min_gap_ms * _NS_PER_MS:
                return (
                    None,
                    ViolationKind.GAP_TOO_SMALL,
                    f"gap {gap_ns // _NS_PER_MS}ms between "
                    f"{previous.event_type.value} and {candidate.event_type.value} "
                    f"is below {step.min_gap_ms}ms (probably one detection, not two)",
                    candidate_index,
                )

            if pattern.require_same_subject:
                mismatch = subject_mismatch(previous, candidate)
                if mismatch is not None:
                    return (None, ViolationKind.SUBJECT_MISMATCH, mismatch, candidate_index)

        return (candidate, None, "", candidate_index + 1)


def subject_mismatch(left: Event, right: Event) -> str | None:
    """Return a message when two events name different subjects, else None.

    Events without a subject (e.g. an unattributed wake word) are exempt: absence
    of identity evidence is handled by the safety gate, not here.
    """
    if left.subject_id and right.subject_id and left.subject_id != right.subject_id:
        return (
            f"{left.event_type.value} concerns {left.subject_id} but "
            f"{right.event_type.value} concerns {right.subject_id}"
        )
    return None


def subject_consistency(events: tuple[Event, ...] | list[Event]) -> tuple[bool, str]:
    """Check that all identity-bearing events agree on a subject.

    Returns
    -------
    tuple
        ``(consistent, detail)``. This is the check that implements rejection of
        "Person A activates, Person B gestures".
    """
    subjects = {e.subject_id for e in events if e.subject_id}
    if len(subjects) <= 1:
        return True, ""
    return False, f"events disagree on subject: {sorted(subjects)}"
