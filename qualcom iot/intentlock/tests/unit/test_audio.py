"""Unit tests for audio features and detector."""

from __future__ import annotations

import numpy as np
import pytest

from intentlock.audio.detector import WakeWordDetector
from intentlock.audio.features import AudioRepresentation, preprocess_audio, resample_linear
from intentlock.common.errors import MalformedInputError
from intentlock.configuration.schema import AudioConfig
from intentlock.interfaces.backend import InferenceBackend, InferenceOutput


class DummyAudioBackend(InferenceBackend):
    def __init__(self, wake_prob: float = 0.92, version: str = "audio-v1.0-mock") -> None:
        self._wake_prob = wake_prob
        self._version = version

    @property
    def model_version(self) -> str:
        return self._version

    @property
    def input_shape(self) -> tuple[int, ...]:
        return (10,)

    @property
    def class_names(self) -> tuple[str, ...]:
        return ("not_wake", "wake")

    @property
    def parameter_count(self) -> int:
        return 1024

    def predict(self, features: np.ndarray) -> InferenceOutput:
        p = self._wake_prob
        scores = np.array([1.0 - p, p], dtype=np.float64)
        return InferenceOutput(
            scores=scores,
            class_names=self.class_names,
            model_version=self._version,
            latency_ms=2.5,
        )


def test_audio_preprocessing() -> None:
    sample_rate = 16000
    duration_sec = 1.0
    t = np.linspace(0, duration_sec, int(sample_rate * duration_sec), endpoint=False)
    waveform = 0.5 * np.sin(2 * np.pi * 440 * t) + 0.1 * np.sin(2 * np.pi * 880 * t)

    features_mfcc = preprocess_audio(
        waveform,
        sample_rate=sample_rate,
        frame_ms=25,
        hop_ms=10,
        n_mels=40,
        representation=AudioRepresentation.MFCC,
        n_mfcc=13,
    )
    assert features_mfcc.n_frames > 0
    assert features_mfcc.n_coefficients == 13
    assert np.isfinite(features_mfcc.features).all()

    features_logmel = preprocess_audio(
        waveform,
        sample_rate=sample_rate,
        frame_ms=25,
        hop_ms=10,
        n_mels=40,
        representation=AudioRepresentation.LOG_MEL,
    )
    assert features_logmel.n_coefficients == 40

    resampled = resample_linear(waveform, 16000, 8000)
    assert resampled.size == int(len(waveform) * 8000 / 16000)

    with pytest.raises(MalformedInputError):
        preprocess_audio(
            np.array([1.5, -2.0]),
            sample_rate=16000,
            frame_ms=25,
            hop_ms=10,
            n_mels=40,
        )


def test_wake_detector() -> None:
    config = AudioConfig(sample_rate=16000, wake_threshold=0.8)
    backend = DummyAudioBackend(wake_prob=0.92)
    detector = WakeWordDetector(config, backend)

    sample_rate = 16000
    t = np.linspace(0, 1.5, int(sample_rate * 1.5), endpoint=False)
    waveform = 0.4 * np.sin(2 * np.pi * 300 * t) + 0.2 * np.sin(2 * np.pi * 1200 * t)

    detection = detector.detect(waveform, timestamp_ns=123456789)
    assert detection.detected is True
    assert detection.wake_probability == 0.92

    event = detection.to_event(event_id="test-wake-1")
    assert event.event_type.value == "wake_detected"

    silence = np.zeros(16000, dtype=np.float64)
    silence_detection = detector.detect(silence, timestamp_ns=999)
    silence_event = silence_detection.to_event(event_id="test-silence")
    assert silence_event.event_type.value == "audio_silence"
