"""Inference backend abstraction.

The same model must be runnable:
  * in-process with PyTorch (development, training-time evaluation),
  * through Edge Impulse's `.eim` runner on the UNO Q (deployment),
  * and by a deterministic stub in tests (no GPU, no weights, no network).

`InferenceBackend` is that seam. It deliberately exchanges only NumPy arrays so
that no backend leaks a framework type into the pipeline.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True, slots=True)
class InferenceOutput:
    """Result of one model invocation.

    Attributes
    ----------
    scores:
        Class probability vector, shape ``(n_classes,)``, sums to 1 within
        floating-point tolerance. Ordering follows `class_names`.
    class_names:
        Label for each index of `scores`. Carried with the output rather than
        looked up globally so a model can never be mislabelled.
    model_version:
        Identifier of the weights that produced this output; recorded in
        telemetry for every decision.
    latency_ms:
        Wall-clock time for the forward pass, measured by the backend.
    """

    scores: np.ndarray
    class_names: tuple[str, ...]
    model_version: str
    latency_ms: float
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.scores.ndim != 1:
            raise ValueError(f"scores must be 1-D, got shape {self.scores.shape}")
        if self.scores.shape[0] != len(self.class_names):
            raise ValueError(
                f"scores length {self.scores.shape[0]} != class_names "
                f"length {len(self.class_names)}"
            )

    def top(self) -> tuple[str, float]:
        """Return the (label, probability) pair with the highest score."""
        idx = int(np.argmax(self.scores))
        return self.class_names[idx], float(self.scores[idx])

    def prob(self, label: str) -> float:
        """Probability of `label`, or 0.0 if the model has no such class."""
        try:
            return float(self.scores[self.class_names.index(label)])
        except ValueError:
            return 0.0

    def entropy(self) -> float:
        """Shannon entropy of the distribution, in nats.

        Used by the uncertainty engine as one signal that a prediction is
        genuinely ambiguous rather than merely below a threshold.
        """
        p = np.clip(self.scores.astype(np.float64), 1e-12, 1.0)
        return float(-np.sum(p * np.log(p)))


@runtime_checkable
class InferenceBackend(Protocol):
    """Runs one model over one input window."""

    @property
    def model_version(self) -> str:
        """Version string of the loaded weights (never empty)."""
        ...

    @property
    def input_shape(self) -> tuple[int, ...]:
        """Expected shape of a single input window, excluding the batch axis."""
        ...

    @property
    def class_names(self) -> tuple[str, ...]:
        """Output label ordering."""
        ...

    def predict(self, window: np.ndarray) -> InferenceOutput:
        """Score one window.

        Parameters
        ----------
        window:
            Array whose trailing dimensions match `input_shape`. Callers pass
            validated, normalised data; the backend must not assume otherwise
            but must not silently reshape for them either.

        Raises
        ------
        ModelError
            If the input shape does not match the model contract.
        """
        ...
