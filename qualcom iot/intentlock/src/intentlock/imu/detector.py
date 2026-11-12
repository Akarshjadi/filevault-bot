"""Turn a stream of IMU samples into `GESTURE_DETECTED` / `NO_GESTURE` events.

This is the boundary between "signal processing" and "the rest of IntentLock".
It owns three decisions the specification requires explicitly:

1. **When a window is a gesture at all** — an activity gate based on signal
   energy. Without it, ordinary hand movement would be handed to the classifier
   and the classifier would be forced to pick a gesture, which is exactly the
   "classify noise as a gesture" failure the specification forbids.
2. **When a prediction is too uncertain to name** — below `unknown_floor` the
   label becomes `UNKNOWN` rather than the argmax.
3. **When a window is a sensor artefact** — an out-of-range magnitude produces
   `IMU_ANOMALY`, which the gate treats as evidence *against* executing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from intentlock.common.errors import MalformedInputError
from intentlock.common.events import Event, EventType, Source
from intentlock.configuration.schema import GestureConfig
from intentlock.imu.preprocessing import (
    ImuCalibration,
    Window,
    calibrate,
    dynamic_energy,
    extract_features,
    gravity_align,
    normalize,
    validate,
    window_stream,
)
from intentlock.imu.vocabulary import Gesture, is_command_gesture
from intentlock.interfaces.backend import InferenceBackend

#: Default minimum *dynamic* inertial energy for a window to be considered motion.
#:
#: Calibrated from measurement, not guessed. Two candidate metrics were compared
#: in `scripts/dev_check_imu.py`:
#:
#: * total magnitude energy (``mean(|a|^2) + mean(|g|^2)``) — **unusable** as an
#:   activity gate, because gravity gives a stationary sensor ``mean(|a|^2) ≈ 1``,
#:   which dominates the motion signal entirely. A resting wrist scored 0.062 while
#:   a real sweep scored 0.036, i.e. the metric was *inverted*.
#: * dynamic energy (``var(|a|) + mean(|g|^2)``) — gravity is constant so it drops
#:   out of the variance, and gyroscope has no gravitational offset. Measured:
#:   resting ≈ 0.0002, decisive sweep ≈ 0.010-0.017. This is the metric used, and
#:   the floor sits at roughly one third of the weakest observed gesture.


@dataclass(frozen=True, slots=True)
class GestureDetection:
    """One classified window with everything needed to explain the decision."""

    label: str
    confidence: float
    window_start_ns: int
    window_end_ns: int
    is_gesture: bool
    model_version: str
    inference_latency_ms: float
    scores: dict[str, float]
    gated_as_activity: bool

    def to_event(self, *, event_id: str, subject_id: str | None = None) -> Event:
        """Convert into the pipeline's exchange type."""
        return Event(
            event_type=EventType.GESTURE_DETECTED if self.is_gesture else EventType.NO_GESTURE,
            timestamp_ns=self.window_end_ns,
            confidence=self.confidence,
            source=Source.IMU,
            subject_id=subject_id,
            label=self.label,
            event_id=event_id,
            metadata={
                "window_start_ns": self.window_start_ns,
                "window_end_ns": self.window_end_ns,
                "duration_ns": self.window_end_ns - self.window_start_ns,
                "model_version": self.model_version,
                "inference_latency_ms": self.inference_latency_ms,
                "scores": self.scores,
                "energy_gated": self.gated_as_activity,
            },
        )


class GestureDetector:
    """Classify IMU windows into the gesture vocabulary.

    Parameters
    ----------
    config:
        Gesture parameters (window length, thresholds, sensor ranges).
    backend:
        The classifier. Any `InferenceBackend` whose class names cover the
        gesture vocabulary.
    calibration:
        Per-device calibration. Defaults to identity, which is correct for a
        simulator or a factory-calibrated device, but the value in use is exposed
        so the provenance of a decision is never ambiguous.
    """

    def __init__(
        self,
        config: GestureConfig,
        backend: InferenceBackend,
        *,
        calibration: ImuCalibration | None = None,
    ) -> None:
        self._config = config
        self._backend = backend
        self._calibration = calibration or ImuCalibration.identity()
        unknown = Gesture.UNKNOWN.value
        if unknown not in backend.class_names:
            raise MalformedInputError(
                f"gesture backend must expose an {unknown!r} class; got {backend.class_names}"
            )

    @property
    def model_version(self) -> str:
        """Version of the loaded classifier."""
        return self._backend.model_version

    @property
    def calibration(self) -> ImuCalibration:
        """The calibration in use; recorded in telemetry with every decision."""
        return self._calibration

    def preprocess(self, raw_samples: np.ndarray) -> np.ndarray:
        """Run the documented preprocessing chain on a raw window.

        ``raw -> validate -> calibrate -> gravity_align -> normalize``

        Raises
        ------
        MalformedInputError
            If the window is malformed or out of range. Callers that must degrade
            rather than raise should use `detect_events`, which converts this into
            an `IMU_ANOMALY` event.
        """
        validated = validate(
            raw_samples, max_accel_g=self._config.max_accel_g, name="gesture window"
        )
        calibrated = calibrate(validated, self._calibration)
        aligned = gravity_align(calibrated)
        return normalize(
            aligned,
            accel_scale=self._config.accel_range_g,
            gyro_scale=self._config.gyro_range_dps,
        )

    def is_active(self, window: Window) -> bool:
        """Whether a window contains enough motion to be worth classifying.

        Uses `dynamic_energy` (gravity removed), which is orientation-independent
        and — unlike raw magnitude energy — is actually a *motion* measure. The
        floor comes from configuration so it can be re-tuned per deployment
        without a code change.
        """
        return dynamic_energy(window) >= self._config.activity_energy_floor

    def classify(self, window: Window) -> GestureDetection:
        """Classify one already-preprocessed window.

        Applies two abstention rules in this order:

        1. If the window carries insufficient motion it is `NO_GESTURE`. The
           classifier is still consulted so the recorded confidence is a real
           model output rather than an invented constant.
        2. If the top class is below `unknown_floor`, the label is `UNKNOWN`.
           Between `unknown_floor` and `confidence_threshold` the label is kept but
           carries genuine uncertainty, which the fusion layer sees directly.
        """
        started = time.perf_counter()
        features = extract_features(window)
        output = self._backend.predict(features)
        latency_ms = (time.perf_counter() - started) * 1000.0

        scores = {name: float(output.scores[i]) for i, name in enumerate(output.class_names)}
        top_label, top_prob = output.top()
        active = self.is_active(window)

        if not active:
            label = Gesture.NO_GESTURE.value
            confidence = scores.get(Gesture.NO_GESTURE.value, top_prob)
            is_gesture = False
        elif top_prob < self._config.unknown_floor:
            label = Gesture.UNKNOWN.value
            # Confidence in abstaining is the probability mass that is *not* the
            # single best class: saying "unknown" is justified to the degree the
            # model is unsure.
            confidence = max(scores.get(Gesture.UNKNOWN.value, 0.0), 1.0 - top_prob)
            is_gesture = False
        else:
            label = top_label
            confidence = top_prob
            is_gesture = is_command_gesture(label)

        return GestureDetection(
            label=label,
            confidence=float(confidence),
            window_start_ns=window.start_timestamp_ns,
            window_end_ns=window.start_timestamp_ns + window.duration_ns,
            is_gesture=is_gesture,
            model_version=self._backend.model_version,
            inference_latency_ms=latency_ms,
            scores=scores,
            gated_as_activity=active,
        )

    def detect(
        self,
        raw_stream: np.ndarray,
        *,
        timestamps_ns: np.ndarray | None = None,
    ) -> list[GestureDetection]:
        """Window and classify a raw stream, returning at most one detection.

        Collapsing overlapping windows here (rather than emitting one detection
        per window and letting the cooldown suppress them) is both correct and
        cheaper: one physical sweep must produce one event. The retained
        detection is the highest-confidence *command* gesture if any window is a
        command, otherwise the highest-confidence `NO_GESTURE`.
        """
        preprocessed = self.preprocess(raw_stream)
        windows = window_stream(
            preprocessed,
            sample_rate=self._config.sample_rate,
            window_ms=self._config.window_ms,
            stride_ms=self._config.stride_ms,
            timestamps_ns=timestamps_ns,
        )
        if not windows:
            return []

        detections = [self.classify(window) for window in windows]
        commands = [d for d in detections if d.is_gesture]
        pool = commands if commands else detections
        return [max(pool, key=lambda d: d.confidence)]

    def detect_events(
        self,
        raw_stream: np.ndarray,
        *,
        timestamps_ns: np.ndarray | None = None,
        subject_id: str | None = None,
        event_id_prefix: str = "imu",
    ) -> list[Event]:
        """Convenience wrapper returning pipeline events directly.

        A malformed or out-of-range stream produces a single `IMU_ANOMALY` event
        rather than raising: a corrupt packet must degrade the system safely
        instead of halting the interaction loop.
        """
        try:
            detections = self.detect(raw_stream, timestamps_ns=timestamps_ns)
        except MalformedInputError as exc:
            anomaly_ts = int(timestamps_ns[-1]) if timestamps_ns is not None else 0
            return [
                Event(
                    event_type=EventType.IMU_ANOMALY,
                    timestamp_ns=anomaly_ts,
                    confidence=1.0,
                    source=Source.IMU,
                    subject_id=subject_id,
                    label="SENSOR_ANOMALY",
                    event_id=f"{event_id_prefix}-anomaly-{anomaly_ts}",
                    metadata={"reason": str(exc)},
                )
            ]

        return [
            detection.to_event(
                # Deterministic id: replaying the same capture yields identical ids,
                # which is what makes deduplication and replay reproducible.
                event_id=(
                    f"{event_id_prefix}-{detection.label}-"
                    f"{detection.window_end_ns}-{index}"
                ),
                subject_id=subject_id,
            )
            for index, detection in enumerate(detections)
        ]
