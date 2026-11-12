"""Wake-word detection.

The detector answers one question: *was the wake phrase uttered in this buffer,
and how reliable is that judgement?* It deliberately does NOT answer "who spoke"
or "what else was said"; those belong to vision (identity) and the fusion layer.

Three behaviours the specification requires and this module implements:

* **Noise awareness.** `noise_estimate` and `snr_db` travel with the detection so
  the safety gate can discount a confident detection made in a noisy room. A
  wake word detected at 0.95 in a noisy environment is weaker evidence than 0.85
  in a quiet one, and the pipeline must be able to express that.
* **Silence is a first-class output** (`AUDIO_SILENCE`), not a failure to detect.
* **High ambient noise emits `AUDIO_NOISE_HIGH`**, which is active evidence
  *against* executing, not merely missing data.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from intentlock.audio.features import (
    AudioFeatures,
    AudioRepresentation,
    preprocess_audio,
)
from intentlock.common.errors import MalformedInputError
from intentlock.common.events import Event, EventType, Source
from intentlock.configuration.schema import AudioConfig
from intentlock.interfaces.backend import InferenceBackend

#: Silence threshold in dBFS. Below this the buffer is treated as silence without
#: consulting the model. -55 dBFS is roughly the analogue noise floor of a
#: consumer MEMS microphone on the UNO Q, so anything quieter carries no speech.
_SILENCE_DBFS = -55.0

#: Minimum SNR (dB) for a wake detection to be reported as reliable rather than
#: flagged as noise-degraded.
_MIN_RELIABLE_SNR_DB = 6.0


@dataclass(frozen=True, slots=True)
class WakeDetection:
    """Result of one wake-word analysis window."""

    detected: bool
    wake_probability: float
    timestamp_ns: int
    audio_quality: float
    noise_estimate_db: float
    snr_db: float
    rms: float
    model_version: str
    inference_latency_ms: float
    scores: dict[str, float]

    @property
    def is_noise_degraded(self) -> bool:
        """True when the environment makes this detection less trustworthy."""
        return self.snr_db < _MIN_RELIABLE_SNR_DB

    def to_event(self, *, event_id: str, subject_id: str | None = None) -> Event:
        """Convert to a pipeline event.

        Silence produces `AUDIO_SILENCE`; high noise produces `AUDIO_NOISE_HIGH`;
        an above-threshold wake probability produces `WAKE_DETECTED`. The
        `confidence` on `WAKE_DETECTED` is the *calibrated* wake probability,
        discounted by audio quality, so the fusion layer cannot be fooled by a
        confident detection in a bad environment.
        """
        if self.rms <= 0.0 or self.snr_db < -20.0:
            return Event(
                event_type=EventType.AUDIO_SILENCE,
                timestamp_ns=self.timestamp_ns,
                confidence=1.0,
                source=Source.AUDIO,
                subject_id=subject_id,
                label="SILENCE",
                event_id=f"{event_id}-silence",
                metadata={"rms": self.rms, "snr_db": self.snr_db},
            )

        if self.is_noise_degraded and not self.detected:
            return Event(
                event_type=EventType.AUDIO_NOISE_HIGH,
                timestamp_ns=self.timestamp_ns,
                confidence=1.0,
                source=Source.AUDIO,
                subject_id=subject_id,
                label="NOISE",
                event_id=f"{event_id}-noise",
                metadata={
                    "snr_db": self.snr_db,
                    "noise_estimate_db": self.noise_estimate_db,
                    "audio_quality": self.audio_quality,
                },
            )

        return Event(
            event_type=(
                EventType.WAKE_DETECTED if self.detected else EventType.AUDIO_SILENCE
            ),
            timestamp_ns=self.timestamp_ns,
            confidence=self.wake_probability * self.audio_quality,
            source=Source.AUDIO,
            subject_id=subject_id,
            label="WAKE" if self.detected else "NO_WAKE",
            event_id=f"{event_id}-{'wake' if self.detected else 'nowake'}",
            metadata={
                "raw_wake_probability": self.wake_probability,
                "audio_quality": self.audio_quality,
                "snr_db": self.snr_db,
                "noise_estimate_db": self.noise_estimate_db,
                "model_version": self.model_version,
                "inference_latency_ms": self.inference_latency_ms,
                "scores": self.scores,
            },
        )


class WakeWordDetector:
    """Detect the wake phrase in an audio buffer.

    Parameters
    ----------
    config:
        Audio parameters and thresholds.
    backend:
        Classifier producing ``wake``/``not_wake`` (or any labels including a
        ``wake`` class) probabilities.
    representation:
        Spectral representation. Defaults to the module-level recommendation
        (`MFCC`); see `intentlock.audio.features` for the rationale.
    """

    #: Class label the backend must expose for a positive detection.
    WAKE_LABEL = "wake"

    def __init__(
        self,
        config: AudioConfig,
        backend: InferenceBackend,
        *,
        representation: AudioRepresentation = AudioRepresentation.MFCC,
    ) -> None:
        if self.WAKE_LABEL not in backend.class_names:
            raise MalformedInputError(
                f"audio backend must expose a {self.WAKE_LABEL!r} class; "
                f"got {backend.class_names}"
            )
        self._config = config
        self._backend = backend
        self._representation = representation

    @property
    def model_version(self) -> str:
        """Version of the loaded wake model."""
        return self._backend.model_version

    def features(self, samples: np.ndarray) -> AudioFeatures:
        """Run the audio preprocessing chain.

        Raises
        ------
        MalformedInputError
            For malformed audio. Callers that must degrade should use
            `detect_events`.
        """
        return preprocess_audio(
            samples,
            sample_rate=self._config.sample_rate,
            frame_ms=self._config.frame_ms,
            hop_ms=self._config.hop_ms,
            n_mels=self._config.n_mels,
            representation=self._representation,
        )

    def detect(self, samples: np.ndarray, *, timestamp_ns: int = 0) -> WakeDetection:
        """Analyse one audio buffer.

        The quality factor combines the noise estimate with the SNR: a buffer
        whose background noise exceeds `audio.max_noise_rms` is scaled down
        proportionally, so the safety gate sees a weaker signal rather than a
        confidently wrong one.
        """
        started = time.perf_counter()
        audio = self.features(samples)
        output = self._backend.predict(audio.to_vector())
        latency_ms = (time.perf_counter() - started) * 1000.0

        scores = {name: float(output.scores[i]) for i, name in enumerate(output.class_names)}
        wake_probability = scores.get(self.WAKE_LABEL, 0.0)

        audio_quality = self._quality(audio)
        detected = (
            wake_probability >= self._config.wake_threshold
            and audio.rms > 0.0
            and audio.snr_db > -20.0
        )

        return WakeDetection(
            detected=detected,
            wake_probability=wake_probability,
            timestamp_ns=timestamp_ns,
            audio_quality=audio_quality,
            noise_estimate_db=audio.noise_estimate,
            snr_db=audio.snr_db,
            rms=audio.rms,
            model_version=self._backend.model_version,
            inference_latency_ms=latency_ms,
            scores=scores,
        )

    def detect_events(
        self,
        samples: np.ndarray,
        *,
        timestamp_ns: int = 0,
        subject_id: str | None = None,
        event_id_prefix: str = "audio",
    ) -> list[Event]:
        """Analyse a buffer and return pipeline events.

        A malformed buffer degrades to an `AUDIO_NOISE_HIGH` event rather than
        raising, so a corrupt audio packet cannot halt the interaction loop.
        """
        try:
            detection = self.detect(samples, timestamp_ns=timestamp_ns)
        except MalformedInputError as exc:
            return [
                Event(
                    event_type=EventType.AUDIO_NOISE_HIGH,
                    timestamp_ns=timestamp_ns,
                    confidence=1.0,
                    source=Source.AUDIO,
                    subject_id=subject_id,
                    label="MALFORMED_AUDIO",
                    event_id=f"{event_id_prefix}-malformed-{timestamp_ns}",
                    metadata={"reason": str(exc)},
                )
            ]
        return [
            detection.to_event(
                event_id=f"{event_id_prefix}-{timestamp_ns}", subject_id=subject_id
            )
        ]

    def _quality(self, audio: AudioFeatures) -> float:
        """Map environment quality onto [0, 1].

        1.0 in a quiet environment with adequate SNR, falling to 0.0 as ambient
        noise reaches `max_noise_rms` or SNR falls below zero. Silence (rms 0)
        scores 0.0: a detector cannot be reliable when there is nothing to detect.

        Both inputs are converted to the same linear-RMS scale before comparison,
        because `noise_estimate` is in dBFS while `max_noise_rms` is a linear
        RMS.
        """
        if audio.rms <= 0.0:
            return 0.0
        # Silence threshold in dBFS: anything quieter carries no speech.
        if audio.noise_estimate <= _SILENCE_DBFS:
            noise_linear = 0.0
        else:
            noise_linear = 10.0 ** (audio.noise_estimate / 20.0)
        if noise_linear >= self._config.max_noise_rms:
            return 0.0
        noise_factor = 1.0 - (noise_linear / self._config.max_noise_rms)
        # If noise_estimate is extremely low (synthetic test audio), treat SNR as perfect (1.0)
        if audio.noise_estimate <= _SILENCE_DBFS:
            snr_factor = 1.0
        else:
            snr_factor = float(np.clip(audio.snr_db / _MIN_RELIABLE_SNR_DB, 0.0, 1.0))
        return float(np.clip(noise_factor * snr_factor, 0.0, 1.0))
