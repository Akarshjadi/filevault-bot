"""Integration tests for synchronization engine and end-to-end handshakes."""

from __future__ import annotations

from intentlock.common.events import Event, EventType, Source
from intentlock.configuration.schema import TemporalConfig
from intentlock.synchronization.engine import TemporalEventEngine


def test_temporal_engine_handshake() -> None:
    config = TemporalConfig()
    engine = TemporalEventEngine(config)

    t0 = 1_000_000_000
    events = [
        Event(
            event_type=EventType.PERSON_PRESENT,
            timestamp_ns=t0,
            confidence=0.9,
            source=Source.VISION,
            subject_id="user_1",
            event_id="e1",
        ),
        Event(
            event_type=EventType.WAKE_DETECTED,
            timestamp_ns=t0 + 500_000_000,
            confidence=0.95,
            source=Source.AUDIO,
            subject_id="user_1",
            event_id="e2",
        ),
        Event(
            event_type=EventType.GESTURE_DETECTED,
            timestamp_ns=t0 + 1_500_000_000,
            confidence=0.85,
            source=Source.IMU,
            subject_id="user_1",
            label="LEFT",
            event_id="e3",
        ),
    ]

    for ev in events:
        engine.push(ev)

    match_result = engine.match("handshake", now_ns=t0 + 2_000_000_000)
    assert match_result.matched is True
    assert len(match_result.events) == 3
    assert match_result.violation is None
