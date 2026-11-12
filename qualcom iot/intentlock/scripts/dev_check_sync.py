"""Ad-hoc smoke check for the synchronisation layer (not a pytest test).

Run: .venv/bin/python scripts/dev_check_sync.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from intentlock.common.events import Event, EventType, Source
from intentlock.configuration import load_config
from intentlock.synchronization import (
    SessionClock,
    TemporalEventEngine,
    ViolationKind,
    VirtualClock,
)

MS = 1_000_000


def _source_for(event_type: EventType) -> Source:
    if event_type in (EventType.GESTURE_DETECTED, EventType.NO_GESTURE, EventType.IMU_ANOMALY):
        return Source.IMU
    if event_type in (
        EventType.PERSON_PRESENT,
        EventType.PERSON_ABSENT,
        EventType.IDENTITY_KNOWN,
        EventType.ATTENTION_ENGAGED,
    ):
        return Source.VISION
    return Source.AUDIO


def ev(
    event_type: EventType,
    ms: int,
    *,
    confidence: float = 0.9,
    label: str | None = None,
    subject: str | None = None,
    eid: str | None = None,
) -> Event:
    return Event(
        event_type=event_type,
        timestamp_ns=ms * MS,
        confidence=confidence,
        source=_source_for(event_type),
        subject_id=subject,
        label=label,
        event_id=eid or f"{event_type.value}-{ms}",
    )


def main() -> int:
    cfg = load_config()

    print("--- 1. valid handshake ---")
    engine = TemporalEventEngine(cfg.temporal)
    engine.push_all(
        [
            ev(EventType.PERSON_PRESENT, 100, subject="u1"),
            ev(EventType.WAKE_DETECTED, 900, subject="u1"),
            ev(EventType.GESTURE_DETECTED, 2000, label="LEFT", subject="u1"),
        ]
    )
    match = engine.match("handshake", 3000 * MS)
    print(
        f"matched={match.matched} events={len(match.events)} "
        f"labels={match.gesture_labels()} violation={match.violation} "
        f"span_ms={match.duration_ns // MS}"
    )
    assert match.matched, "valid handshake must match"

    print("--- 2. gesture without wake ---")
    engine2 = TemporalEventEngine(cfg.temporal)
    engine2.push(ev(EventType.GESTURE_DETECTED, 500, label="LEFT"))
    print("gesture_only matched:", engine2.match("gesture_only", 600 * MS).matched)
    m2 = engine2.match("handshake", 600 * MS)
    print(f"handshake matched={m2.matched} violation={m2.violation} detail={m2.detail}")
    assert not m2.matched, "handshake without any person/wake evidence must not match"
    # No violation is reported because the pattern never started: the gate must
    # distinguish "nothing happened" from "an incomplete handshake".
    assert m2.violation is None and not m2.events, "unstarted pattern has no violation"

    print("--- 2b. partial handshake (person seen, then gesture, no wake) ---")
    engine2b = TemporalEventEngine(cfg.temporal)
    engine2b.push_all(
        [
            ev(EventType.PERSON_PRESENT, 100, subject="u1"),
            ev(EventType.GESTURE_DETECTED, 900, label="LEFT", subject="u1"),
        ]
    )
    m2b = engine2b.match("handshake", 1000 * MS)
    print(f"matched={m2b.matched} violation={m2b.violation} detail={m2b.detail}")
    assert not m2b.matched
    assert m2b.violation is ViolationKind.MISSING_STEP, (
        "a started-but-incomplete handshake must report MISSING_STEP so the gate can RETRY"
    )
    assert m2b.is_partial

    print("--- 3. wrong order (gesture then wake) ---")
    engine3 = TemporalEventEngine(cfg.temporal)
    engine3.push_all(
        [
            ev(EventType.GESTURE_DETECTED, 500, label="LEFT"),
            ev(EventType.WAKE_DETECTED, 900),
            ev(EventType.GESTURE_DETECTED, 1500, label="RIGHT"),
        ]
    )
    m3 = engine3.match("wake_gesture", 2000 * MS)
    print(f"matched={m3.matched} violation={m3.violation} detail={m3.detail}")
    assert m3.matched and m3.gesture_labels() == ["RIGHT"], (
        "the pre-wake gesture must not be reused as the command gesture"
    )

    print("--- 4. gap too large (wake -> gesture 9s) ---")
    engine4 = TemporalEventEngine(cfg.temporal)
    engine4.push_all(
        [
            ev(EventType.WAKE_DETECTED, 100),
            ev(EventType.GESTURE_DETECTED, 9100, label="LEFT"),
        ]
    )
    m4 = engine4.match("wake_gesture", 9200 * MS)
    print(f"matched={m4.matched} violation={m4.violation} detail={m4.detail}")
    assert m4.violation is not None

    print("--- 5. subject mismatch (person A wakes, person B gestures) ---")
    engine5 = TemporalEventEngine(cfg.temporal)
    engine5.push_all(
        [
            ev(EventType.PERSON_PRESENT, 100, subject="user_A"),
            ev(EventType.WAKE_DETECTED, 900, subject="user_A"),
            ev(EventType.GESTURE_DETECTED, 2000, label="LEFT", subject="user_B"),
        ]
    )
    m5 = engine5.match("handshake", 3000 * MS)
    print(f"matched={m5.matched} violation={m5.violation} detail={m5.detail}")
    assert m5.violation is not None, "cross-subject sequence must be rejected"

    print("--- 6. duplicate suppression ---")
    engine6 = TemporalEventEngine(cfg.temporal)
    first = engine6.push(ev(EventType.WAKE_DETECTED, 100, eid="dup-1"))
    second = engine6.push(ev(EventType.WAKE_DETECTED, 100, eid="dup-1"))
    print(f"first={first} second={second} stats={engine6.stats()}")
    assert first and not second

    print("--- 7. expiry ---")
    engine7 = TemporalEventEngine(cfg.temporal)
    engine7.push(ev(EventType.WAKE_DETECTED, 100))
    engine7.push(ev(EventType.GESTURE_DETECTED, 30000, label="LEFT"))
    m7 = engine7.match("wake_gesture", 30100 * MS)
    print(f"matched={m7.matched} buffered={len(engine7.buffer)} violation={m7.violation}")
    assert not m7.matched, "expired wake word must not authorise a late gesture"

    print("--- 8. session clock offsets ---")
    vclock = VirtualClock()
    sclock = SessionClock(clock=vclock)
    sclock.start()
    vclock.advance_ms(1000)
    # Device-local counter starting near zero while the session is already 1s in:
    # its first sample must map to "now", not to session time 5ms.
    a1 = sclock.convert(5_000_000, "imu")
    assert a1 == 1000 * MS, f"first device-local sample should map to 'now', got {a1 / MS}ms"
    # Advance real time alongside the device counter; the 200ms offset between
    # samples must be preserved exactly.
    vclock.advance_ms(200)
    a2 = sclock.convert(205_000_000, "imu")
    print(f"imu t0={a1 / MS:.0f}ms t1={a2 / MS:.0f}ms drift={sclock.drift_report()}")
    assert a2 == 1200 * MS, f"relative offsets must be preserved, got {a2 / MS}ms"

    print("--- 9. already-session-relative timestamps pass through ---")
    vclock2 = VirtualClock()
    sclock2 = SessionClock(clock=vclock2)
    sclock2.start()
    vclock2.advance_ms(500)
    assert sclock2.convert(500 * MS, "sim") == 500 * MS
    # A future timestamp is clamped to now, never accepted.
    assert sclock2.convert(9000 * MS, "sim") == 500 * MS
    print("pass-through and future-clamp OK")

    print("\nALL SYNC CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
