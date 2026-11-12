"""IMU gesture classifier.

`LinearGestureBackend` — a softmax (multinomial logistic regression) trained
with NumPy gradient descent on the 30 interpretable features in
`imu.preprocessing`. This is the default because it trains in seconds on a
CPU, is fully reproducible, and is small enough to ship to the UNO Q as a
weight matrix.

Implements `InferenceBackend`, so the application is unaware of internal details.

Temperature scaling
-------------------
Raw softmax output is not a calibrated probability. `calibrate_scores` applies a
fitted temperature so that a reported 0.8 means roughly 80% empirical accuracy,
which is the precondition for thresholding at all.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from intentlock.common.errors import ModelError
from intentlock.imu.preprocessing import FEATURE_NAMES, extract_features
from intentlock.imu.vocabulary import GESTURE_CLASSES
from intentlock.interfaces.backend import InferenceOutput


@dataclass(frozen=True, slots=True)
class ModelMetadata:
    """Version metadata recorded with every trained model.

    Required by the specification: a model file with no provenance cannot be
    reproduced or safely replaced.
    """

    model_version: str
    dataset_version: str
    training_config: Mapping[str, Any]
    git_commit: str
    timestamp: str
    metrics: Mapping[str, float]
    input_schema: str
    output_schema: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "dataset_version": self.dataset_version,
            "training_config": dict(self.training_config),
            "git_commit": self.git_commit,
            "timestamp": self.timestamp,
            "metrics": dict(self.metrics),
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ModelMetadata:
        try:
            return cls(
                model_version=str(payload["model_version"]),
                dataset_version=str(payload["dataset_version"]),
                training_config=dict(payload["training_config"]),
                git_commit=str(payload["git_commit"]),
                timestamp=str(payload["timestamp"]),
                metrics={k: float(v) for k, v in dict(payload["metrics"]).items()},
                input_schema=str(payload["input_schema"]),
                output_schema=str(payload["output_schema"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelError(f"invalid model metadata: {exc}") from exc


def softmax(logits: np.ndarray) -> np.ndarray:
    """Numerically stable softmax over a 1-D or 2-D array's last axis."""
    shifted = logits - np.max(logits, axis=-1, keepdims=True)
    exponent = np.exp(shifted)
    return exponent / np.sum(exponent, axis=-1, keepdims=True)


def calibrate_scores(scores: np.ndarray, temperature: float) -> np.ndarray:
    """Apply temperature scaling to probabilities.

    Parameters
    ----------
    scores:
        Probability vector (or matrix) summing to 1 along the last axis.
    temperature:
        Fitted on the validation split. ``T > 1`` softens an overconfident model;
        ``T < 1`` sharpens an underconfident one. ``T = 1`` is a no-op.

    Notes
    -----
    Temperature is applied in log space, so this is a genuine recalibration
    rather than a rescaling of the maximum.
    """
    if temperature <= 0:
        raise ModelError(f"temperature must be positive, got {temperature}")
    if temperature == 1.0:
        return scores
    epsilon = 1e-12
    logits = np.log(np.clip(scores, epsilon, 1.0)) / temperature
    return softmax(logits)


def fit_temperature(
    probabilities: np.ndarray, labels: np.ndarray, *, max_iter: int = 100
) -> float:
    """Fit a single temperature by minimising validation NLL (grid + refine).

    A one-parameter grid search is used rather than gradient descent because the
    objective is unimodal in log-temperature and the search is exhaustive enough
    to be exact for reporting purposes.

    The grid spans 0.05 to 20.0. The upper bound matters: a linear model trained
    on few samples produces very peaked logits, and a grid that stops at 8 can
    hit its ceiling and silently report a miscalibrated model as calibrated. The
    bound is wide enough that the optimum is interior in practice, and the value
    returned is verified to improve on no calibration before it is accepted.
    """
    if probabilities.ndim != 2:
        raise ModelError("probabilities must be 2-D (n_samples, n_classes)")
    if len(probabilities) != len(labels):
        raise ModelError(
            f"probabilities ({len(probabilities)}) and labels ({len(labels)}) length mismatch"
        )
    if len(probabilities) == 0:
        return 1.0

    indices = np.arange(len(labels))

    def nll(temperature: float) -> float:
        calibrated = calibrate_scores(probabilities, temperature)
        correct = calibrated[indices, labels]
        return float(-np.mean(np.log(np.clip(correct, 1e-12, 1.0))))

    baseline = nll(1.0)
    grid = np.exp(np.linspace(math.log(0.05), math.log(20.0), max_iter))
    scores = np.array([nll(float(t)) for t in grid])
    best = float(grid[int(np.argmin(scores))])
    # Only accept the fit if it genuinely improves on no calibration, so this
    # step can never make a model worse than leaving it alone.
    return best if nll(best) < baseline else 1.0


class LinearGestureBackend:
    """Multinomial logistic regression over the interpretable feature vector.

    Trained with full-batch gradient descent plus L2 regularisation. This is
    deliberately a *linear* model: it is the honest baseline for the IMU
    modality, and its weights are directly inspectable, so a reviewer can see
    which features drive each gesture.

    Implements `InferenceBackend`.
    """

    #: Input contract: feature vectors of this length.
    INPUT_SCHEMA = f"features[{len(FEATURE_NAMES)}]"

    def __init__(
        self,
        weights: np.ndarray,
        bias: np.ndarray,
        *,
        metadata: ModelMetadata,
        temperature: float = 1.0,
        class_names: tuple[str, ...] = GESTURE_CLASSES,
    ) -> None:
        if weights.ndim != 2:
            raise ModelError(f"weights must be 2-D, got shape {weights.shape}")
        if weights.shape[1] != len(FEATURE_NAMES):
            raise ModelError(
                f"weights expect {len(FEATURE_NAMES)} features, got {weights.shape[1]}"
            )
        if weights.shape[0] != len(class_names):
            raise ModelError(
                f"weights have {weights.shape[0]} classes but {len(class_names)} names given"
            )
        if bias.shape != (weights.shape[0],):
            raise ModelError(f"bias shape {bias.shape} does not match classes {weights.shape[0]}")
        if temperature <= 0:
            raise ModelError(f"temperature must be positive, got {temperature}")
        self._weights = weights.astype(np.float64, copy=True)
        self._bias = bias.astype(np.float64, copy=True)
        self._metadata = metadata
        self._temperature = float(temperature)
        self._class_names = tuple(class_names)

    # -- InferenceBackend protocol ----------------------------------------

    @property
    def model_version(self) -> str:
        return self._metadata.model_version

    @property
    def input_shape(self) -> tuple[int, ...]:
        return (len(FEATURE_NAMES),)

    @property
    def class_names(self) -> tuple[str, ...]:
        return self._class_names

    @property
    def metadata(self) -> ModelMetadata:
        """Full version metadata for telemetry and the model inventory."""
        return self._metadata

    @property
    def temperature(self) -> float:
        """Fitted calibration temperature."""
        return self._temperature

    def predict(self, window: np.ndarray) -> InferenceOutput:
        """Score one feature vector or one raw window.

        Accepts either a ``(n_features,)`` feature vector or a ``(n_samples, 6)``
        raw window, because callers legitimately have both to hand. An ambiguous
        shape is an error rather than a guess.
        """
        started = time.perf_counter()
        features = self._as_features(window)
        logits = features @ self._weights.T + self._bias
        raw = softmax(logits)
        scores = calibrate_scores(raw, self._temperature)
        latency_ms = (time.perf_counter() - started) * 1000.0
        return InferenceOutput(
            scores=np.asarray(scores, dtype=np.float64),
            class_names=self._class_names,
            model_version=self.model_version,
            latency_ms=latency_ms,
        )

    def _as_features(self, window: np.ndarray) -> np.ndarray:
        """Coerce input into a single feature vector of the expected length."""
        if not isinstance(window, np.ndarray):
            raise ModelError(f"input must be a numpy array, got {type(window).__name__}")
        flat = window.astype(np.float64, copy=False)
        if flat.ndim == 1:
            if flat.shape[0] != len(FEATURE_NAMES):
                raise ModelError(
                    f"feature vector must have {len(FEATURE_NAMES)} elements, got {flat.shape[0]}"
                )
            return np.nan_to_num(flat, nan=0.0, posinf=0.0, neginf=0.0)
        if flat.ndim == 2:
            if flat.shape[1] != len(FEATURE_NAMES):
                if flat.shape[1] == 6:
                    # A raw (n_samples, 6) window: extract features on the fly.
                    return extract_features(flat)
                raise ModelError(
                    f"2-D input must be features[{len(FEATURE_NAMES)}] or raw (n, 6); "
                    f"got shape {flat.shape}"
                )
            # A batch of feature vectors: average them, which is the standard way
            # to fold overlapping windows into one window-level decision.
            return np.nan_to_num(flat.mean(axis=0), nan=0.0, posinf=0.0, neginf=0.0)
        raise ModelError(f"input must be 1-D or 2-D, got {flat.ndim}-D")

    # -- training ----------------------------------------------------------

    @classmethod
    def fit(
        cls,
        features: np.ndarray,
        labels: np.ndarray,
        *,
        metadata: ModelMetadata,
        learning_rate: float = 0.5,
        epochs: int = 400,
        l2: float = 1e-3,
        temperature: float = 1.0,
        class_names: tuple[str, ...] = GESTURE_CLASSES,
    ) -> LinearGestureBackend:
        """Train by full-batch gradient descent on softmax cross-entropy.

        Parameters
        ----------
        features:
            ``(n_samples, n_features)`` matrix of extracted feature vectors.
        labels:
            ``(n_samples,)`` integer class indices into `class_names`.
        metadata:
            Provenance record stored with the model. Required, not optional: a
            model without version metadata cannot be reproduced.
        learning_rate, epochs, l2:
            Optimisation hyperparameters. L2 excludes the bias, which must stay free.
        temperature:
            Calibration temperature, normally fitted afterwards on validation.

        Returns
        -------
        LinearGestureBackend
            The trained model.

        Raises
        ------
        ModelError
            If the inputs are inconsistent, or if a class in `class_names` has no
            training examples. A class with no examples would produce an untrained
            output row the model could still select, so this is a hard error.
        """
        n_samples, n_features = features.shape
        if n_features != len(FEATURE_NAMES):
            raise ModelError(f"expected {len(FEATURE_NAMES)} features, got {n_features}")
        if len(labels) != n_samples:
            raise ModelError(f"label count {len(labels)} != sample count {n_samples}")
        if n_samples == 0:
            raise ModelError("cannot train on an empty dataset")

        n_classes = len(class_names)
        invalid = sorted({int(v) for v in labels if int(v) < 0 or int(v) >= n_classes})
        if invalid:
            raise ModelError(f"labels out of range for {n_classes} classes: {invalid}")
        present = {int(v) for v in np.unique(labels)}
        missing = [class_names[i] for i in range(n_classes) if i not in present]
        if missing:
            raise ModelError(
                f"no training examples for classes {missing}; every output class needs "
                "examples or the model would emit an untrained row"
            )

        x = features.astype(np.float64, copy=True)
        y = labels.astype(np.int64, copy=False)
        weights = np.zeros((n_classes, n_features), dtype=np.float64)
        bias = np.zeros(n_classes, dtype=np.float64)
        one_hot = np.zeros((n_samples, n_classes), dtype=np.float64)
        one_hot[np.arange(n_samples), y] = 1.0

        for _ in range(epochs):
            probabilities = softmax(x @ weights.T + bias)
            error = (probabilities - one_hot) / n_samples
            grad_w = error.T @ x + l2 * weights
            grad_b = error.sum(axis=0)
            weights -= learning_rate * grad_w
            bias -= learning_rate * grad_b

        return cls(
            weights=weights,
            bias=bias,
            metadata=metadata,
            temperature=temperature,
            class_names=class_names,
        )

    def parameter_count(self) -> int:
        """Total trainable parameters, for the model inventory and benchmarks."""
        return int(self._weights.size + self._bias.size)

    # -- persistence -------------------------------------------------------

    def save(self, directory: str | Path) -> Path:
        """Persist weights, calibration and metadata into `directory`."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        # allow_pickle=False on load means the arrays must stay plain numeric or
        # object-of-str dtypes; that is a deliberate choice so a model file can
        # never execute code when loaded (no insecure deserialization surface).
        np.savez(
            target / "imu_linear.npz",
            weights=self._weights,
            bias=self._bias,
            temperature=np.asarray([self._temperature]),
            class_names=np.asarray(self._class_names, dtype="<U32"),
            feature_names=np.asarray(FEATURE_NAMES, dtype="<U32"),
        )
        (target / "imu_linear.metadata.json").write_text(
            json.dumps(self._metadata.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )
        return target

    @classmethod
    def load(cls, directory: str | Path) -> LinearGestureBackend:
        """Load a previously saved model.

        Raises
        ------
        ModelError
            If any artefact is missing or inconsistent. A partially-loaded model
            would be silently wrong, so this refuses rather than defaults.
        """
        target = Path(directory)
        weights_path = target / "imu_linear.npz"
        meta_path = target / "imu_linear.metadata.json"
        for path in (weights_path, meta_path):
            if not path.exists():
                raise ModelError(f"model artefact not found: {path}")

        try:
            with np.load(weights_path, allow_pickle=False) as payload:
                weights = payload["weights"]
                bias = payload["bias"]
                temperature = float(payload["temperature"][0])
                class_names = tuple(str(c) for c in payload["class_names"])
                feature_names = tuple(str(f) for f in payload["feature_names"])
        except (OSError, KeyError, ValueError) as exc:
            raise ModelError(f"cannot read {weights_path}: {exc}") from exc

        if feature_names != FEATURE_NAMES:
            raise ModelError(
                "model was trained on a different feature set than this code produces; "
                f"model has {len(feature_names)} features, code has {len(FEATURE_NAMES)}"
            )

        try:
            metadata = ModelMetadata.from_dict(json.loads(meta_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelError(f"cannot read {meta_path}: {exc}") from exc

        return cls(
            weights=weights,
            bias=bias,
            metadata=metadata,
            temperature=temperature,
            class_names=class_names,
        )

    def export_linear_weights(self) -> dict[str, Any]:
        """Raw weight dictionary for export to ONNX / TFLite / a hand-written kernel.

        Returned as plain lists so it is JSON-serialisable and can be embedded in
        generated C without a numerical library on the MCU side.
        """
        return {
            "weights": [[float(v) for v in row] for row in self._weights],
            "bias": [float(v) for v in self._bias],
            "temperature": self._temperature,
            "class_names": list(self._class_names),
            "feature_names": list(FEATURE_NAMES),
        }
