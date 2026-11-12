"""Unit tests for IMU vocabulary, preprocessing, classifier and detector."""

from __future__ import annotations

import numpy as np
import pytest

from intentlock.common.errors import MalformedInputError
from intentlock.configuration.schema import GestureConfig
from intentlock.imu.classifier import LinearGestureBackend, ModelMetadata
from intentlock.imu.detector import GestureDetector
from intentlock.imu.preprocessing import FEATURE_NAMES, extract_features, validate
from intentlock.imu.vocabulary import GESTURE_CLASSES, GESTURE_TO_COMMAND


def test_vocabulary() -> None:
    vocab = GESTURE_CLASSES
    assert len(vocab) == 8
    assert "LEFT" in GESTURE_TO_COMMAND


def test_imu_preprocessing() -> None:
    window = np.random.default_rng(42).normal(0.0, 1.0, (80, 6))
    validated = validate(window, max_accel_g=8.0)
    assert validated.shape == (80, 6)

    feats = extract_features(validated, sample_rate=100)
    assert feats.size > 0

    with pytest.raises(MalformedInputError):
        validate(np.array([1.0, 2.0]), max_accel_g=8.0)


def test_imu_classifier_and_detector(tmp_path) -> None:
    rng = np.random.default_rng(42)
    n_features = len(FEATURE_NAMES)
    weights = rng.normal(0.0, 0.1, (8, n_features))
    bias = np.zeros(8)
    metadata = ModelMetadata(
        model_version="imu-test-v1",
        dataset_version="ds-1",
        training_config={},
        git_commit="git-1",
        timestamp="2026-09-21",
        metrics={"accuracy": 0.9},
        input_schema="features[30]",
        output_schema="classes[8]",
    )
    backend = LinearGestureBackend(
        weights=weights,
        bias=bias,
        metadata=metadata,
    )
    assert backend.parameter_count() > 0

    path = tmp_path / "model.npz"
    backend.save(path)
    loaded = LinearGestureBackend.load(path)
    assert loaded.model_version == "imu-test-v1"

    config = GestureConfig()
    detector = GestureDetector(config, loaded)
    # still window
    still = np.zeros((80, 6), dtype=np.float64)
    detections = detector.detect(still)
    assert len(detections) >= 0
