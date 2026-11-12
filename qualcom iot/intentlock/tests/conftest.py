"""Pytest fixtures for IntentLock test suites."""

from __future__ import annotations

import numpy as np
import pytest

from intentlock.common.events import Event, EventType, Source
from intentlock.interfaces.backend import InferenceBackend, InferenceOutput
from intentlock.synchronization.clock import SessionClock, VirtualClock


class DeterministicFakeBackend(InferenceBackend):
    """Deterministic fake inference backend for testing."""

    def __init__(
        self,
        class_names: tuple[str, ...] = ("not_wake", "wake"),
        default_scores: tuple[float, ...] = (0.1, 0.9),
        version: str = "fake-v1.0",
        input_shape: tuple[int, ...] = (10,),
    ) -> None:
        self._class_names = class_names
        self._default_scores = np.array(default_scores, dtype=np.float64)
        self._version = version
        self._input_shape = input_shape

    @property
    def model_version(self) -> str:
        return self._version

    @property
    def input_shape(self) -> tuple[int, ...]:
        return self._input_shape

    @property
    def class_names(self) -> tuple[str, ...]:
        return self._class_names

    def predict(self, window: np.ndarray) -> InferenceOutput:
        return InferenceOutput(
            scores=self._default_scores,
            class_names=self._class_names,
            model_version=self._version,
            latency_ms=1.5,
        )


@pytest.fixture
def fake_backend() -> DeterministicFakeBackend:
    return DeterministicFakeBackend()


@pytest.fixture
def virtual_clock() -> VirtualClock:
    return VirtualClock(start_ns=1_000_000_000)


@pytest.fixture
def session_clock(virtual_clock: VirtualClock) -> SessionClock:
    return SessionClock(source_clock=virtual_clock, session_id="test-session")


@pytest.fixture
def event_factory():
    def _create(
        event_type: EventType = EventType.WAKE_DETECTED,
        timestamp_ns: int = 1000,
        confidence: float = 0.9,
        source: Source = Source.AUDIO,
        subject_id: str | None = "user_0",
        label: str | None = None,
        event_id: str = "evt-1",
        metadata: dict | None = None,
    ) -> Event:
        return Event(
            event_type=event_type,
            timestamp_ns=timestamp_ns,
            confidence=confidence,
            source=source,
            subject_id=subject_id,
            label=label,
            event_id=event_id,
            metadata=metadata or {},
        )

    return _create


@pytest.fixture
def tmp_model_dir(tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    return d
