"""Smoke check for audio preprocessing and wake-word detector."""

from __future__ import annotations

import numpy as np

from intentlock.audio.detector import WakeWordDetector
from intentlock.audio.features import AudioRepresentation, preprocess_audio, resample_linear
from intentlock.common.errors import MalformedInputError
from intentlock.configuration.schema import AudioConfig
from intentlock.interfaces.backend import InferenceBackend, InferenceOutput


class DummyAudioBackend(InferenceBackend):
    """Mock backend returning fixed probabilities for wake vs not_wake."""

    def __init__(self, wake_prob: float = 0.92, version: str = "audio-v1.0-mock") -> None:
        self._wake_prob = wake_prob
        self._version = version

    @property
    def model_version(self) -> str:
        return self._version

    @property
    def class_names(self) -> list[str]:
        return ["not_wake", "wake"]

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
    print("Testing audio preprocessing & feature extraction...")
    sample_rate = 16000
    duration_sec = 1.0
    t = np.linspace(0, duration_sec, int(sample_rate * duration_sec), endpoint=False)
    # 440 Hz tone with some harmonic content
    waveform = 0.5 * np.sin(2 * np.pi * 440 * t) + 0.1 * np.sin(2 * np.pi * 880 * t)

    # Test MFCC representation
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
    print(f"  -> MFCC shape: {features_mfcc.features.shape}, SNR: {features_mfcc.snr_db:.1f} dB")

    # Test Log-Mel representation
    features_logmel = preprocess_audio(
        waveform,
        sample_rate=sample_rate,
        frame_ms=25,
        hop_ms=10,
        n_mels=40,
        representation=AudioRepresentation.LOG_MEL,
    )
    assert features_logmel.n_coefficients == 40
    print(f"  -> Log-Mel shape: {features_logmel.features.shape}")

    # Test resampler
    resampled = resample_linear(waveform, 16000, 8000)
    assert resampled.size == int(len(waveform) * 8000 / 16000)
    print(f"  -> Resampled from 16kHz to 8kHz: {resampled.size} samples")

    # Test malformed input rejections
    try:
        preprocess_audio(
            np.array([1.5, -2.0]),
            sample_rate=16000,
            frame_ms=25,
            hop_ms=10,
            n_mels=40,
        )
        raise AssertionError("Expected MalformedInputError for out-of-range samples")
    except MalformedInputError:
        print("  -> Correctly rejected out-of-range samples")


def test_wake_detector() -> None:
    print("Testing WakeWordDetector...")
    config = AudioConfig(sample_rate=16000, wake_threshold=0.8)
    backend = DummyAudioBackend(wake_prob=0.92)
    detector = WakeWordDetector(config, backend)

    sample_rate = 16000
    t = np.linspace(0, 1.5, int(sample_rate * 1.5), endpoint=False)
    # Speech-like active signal
    waveform = 0.4 * np.sin(2 * np.pi * 300 * t) + 0.2 * np.sin(
        2 * np.pi * 1200 * t
    )

    detection = detector.detect(waveform, timestamp_ns=123456789)
    assert detection.detected is True
    assert detection.wake_probability == 0.92
    print(
        f"  -> Wake detected: prob={detection.wake_probability}, "
        f"quality={detection.audio_quality:.2f}"
    )

    event = detection.to_event(event_id="test-wake-1")
    assert event.event_type.value == "wake_detected"
    print(f"  -> Event generated: {event.event_type.value} with confidence {event.confidence:.2f}")

    # Test silence buffer
    silence = np.zeros(16000, dtype=np.float64)
    silence_detection = detector.detect(silence, timestamp_ns=999)
    silence_event = silence_detection.to_event(event_id="test-silence")
    assert silence_event.event_type.value == "audio_silence"
    print(f"  -> Silence handled correctly: {silence_event.event_type.value}")


if __name__ == "__main__":
    test_audio_preprocessing()
    test_wake_detector()
    print("All audio smoke checks passed successfully!")
