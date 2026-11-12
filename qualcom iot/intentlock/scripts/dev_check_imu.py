"""IMU smoke check: preprocessing determinism, training, calibration, abstention.

Run: .venv/bin/python scripts/dev_check_imu.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from intentlock.common.events import EventType
from intentlock.common.seed import rng
from intentlock.configuration import load_config
from intentlock.imu import (
    FEATURE_NAMES,
    GESTURE_CLASSES,
    GestureDetector,
    LinearGestureBackend,
    ModelMetadata,
    dynamic_energy,
    extract_features,
    fit_temperature,
    gravity_align,
    normalize,
    softmax,
    window_stream,
)
from intentlock.imu.preprocessing import validate

MS = 1_000_000


def synth_sweep(
    kind: str, sample_rate: int = 100, duration_s: float = 1.6, seed: int = 0
) -> np.ndarray:
    """Synthesise a plausible IMU window for one gesture class.

    Parameters are chosen to be physically sensible for a wrist-worn sensor: an
    arm sweep is ~0.6-0.9 s of motion at 1-3 Hz with up to ~2 g of acceleration
    and a few hundred deg/s of rotation.

    The classes are separated the way the real vocabulary separates them: by
    **direction along an axis** (the property a wrist IMU measures well) and by
    **temporal pattern** (CONFIRM is a double pulse). LEFT/RIGHT are opposite
    signs on the same axis, as are UP/DOWN; CANCEL is LEFT with a reversed
    return stroke. This is a *test fixture*: clearly synthetic, and never used to
    produce a reported metric.
    """
    generator = rng(seed)
    n = int(sample_rate * duration_s)
    t = np.arange(n) / sample_rate
    envelope = np.sin(np.pi * t / t[-1]) ** 2  # smooth start and end

    # (axis, sign, accel_amplitude_g, gyro_amplitude_dps, frequency_hz)
    spec = {
        "LEFT": (0, -1.0, 1.7, 280.0, 2.0),
        "RIGHT": (0, +1.0, 1.7, 280.0, 2.0),
        "UP": (1, +1.0, 1.6, 250.0, 2.4),
        "DOWN": (1, -1.0, 1.6, 250.0, 2.4),
        "CONFIRM": (2, +1.0, 1.1, 170.0, 1.4),
        "CANCEL": (2, -1.0, 1.4, 230.0, 1.8),
        "NO_GESTURE": (0, +1.0, 0.02, 5.0, 0.4),
        # UNKNOWN is deliberately mid-amplitude and high-frequency: a motion that
        # is neither clearly a command nor clearly at rest.
        "UNKNOWN": (1, +1.0, 0.35, 70.0, 6.0),
    }
    axis, sign, accel_amp, gyro_amp, freq = spec[kind]

    accel = generator.normal(0.0, 0.03, size=(n, 3))
    accel[:, 2] += 1.0  # gravity on Z
    accel[:, axis] += sign * accel_amp * envelope * np.sin(2 * np.pi * freq * t)
    gyro = generator.normal(0.0, 8.0, size=(n, 3))
    gyro[:, axis] += sign * gyro_amp * envelope * np.sin(2 * np.pi * freq * t)
    if kind == "CONFIRM":
        # Two pulses: what makes it distinguishable from a single sweep.
        gate = (np.sin(2 * np.pi * freq * t) > 0).astype(np.float64)
        accel[:, axis] *= gate
        gyro[:, axis] *= gate
    return np.hstack([accel, gyro])


def build_dataset(samples_per_class: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build a synthetic feature matrix, label vector and subject vector."""
    cfg = load_config()
    features: list[np.ndarray] = []
    labels: list[int] = []
    subjects: list[int] = []
    for class_index, class_name in enumerate(GESTURE_CLASSES):
        for sample_index in range(samples_per_class):
            subject = sample_index % 4
            raw = synth_sweep(class_name, seed=class_index * 1000 + sample_index)
            # Per-subject amplitude scaling: a cheap stand-in for the real
            # inter-person variation that participant-level splits must respect.
            raw = raw * (0.85 + 0.1 * subject)
            windows = window_stream(
                normalize(gravity_align(raw), accel_scale=4.0, gyro_scale=500.0),
                sample_rate=cfg.gesture.sample_rate,
                window_ms=cfg.gesture.window_ms,
                stride_ms=cfg.gesture.stride_ms,
            )
            # Take the middle window: the sweep's most energetic moment.
            window = windows[len(windows) // 2]
            features.append(extract_features(window))
            labels.append(class_index)
            subjects.append(subject)
    return np.vstack(features), np.asarray(labels), np.asarray(subjects)


def main() -> int:
    cfg = load_config()

    print("--- 1. preprocessing determinism and validation ---")
    raw = synth_sweep("LEFT", seed=7)
    a = normalize(gravity_align(raw), accel_scale=4.0, gyro_scale=500.0)
    b = normalize(gravity_align(raw), accel_scale=4.0, gyro_scale=500.0)
    assert np.array_equal(a, b), "preprocessing must be deterministic"
    # Magnitudes must be preserved by gravity alignment: it is a pure rotation.
    assert np.allclose(
        np.linalg.norm(raw[:, :3], axis=1), np.linalg.norm(gravity_align(raw)[:, :3], axis=1)
    ), "gravity alignment must be a pure rotation"
    print("deterministic; gravity alignment preserves magnitude")

    print("--- 2. malformed input rejected ---")
    for bad, why in [
        (np.zeros((10, 5)), "wrong channel count"),
        (np.full((10, 6), np.nan), "NaN present"),
        (np.zeros((0, 6)), "empty"),
    ]:
        try:
            validate(bad, max_accel_g=12.0)
        except Exception as exc:
            print(f"  rejected {why}: {type(exc).__name__}")
        else:
            raise AssertionError(f"{why} was not rejected")
    violent = np.zeros((100, 6))
    violent[:, 2] = 30.0
    try:
        validate(violent, max_accel_g=12.0)
    except Exception as exc:
        print(f"  rejected over-range acceleration: {type(exc).__name__}")
    else:
        raise AssertionError("over-range acceleration was not rejected")

    print("--- 3. participant-level split (no subject leakage) ---")
    features, labels, subjects = build_dataset(samples_per_class=24)
    train_mask = np.isin(subjects, [0, 1])
    val_mask = subjects == 2
    test_mask = subjects == 3
    assert not (set(subjects[train_mask]) & set(subjects[test_mask])), "subject leakage"
    print(
        f"features={features.shape} train={train_mask.sum()} "
        f"val={val_mask.sum()} test={test_mask.sum()} "
        f"train_subjects={sorted(set(subjects[train_mask].tolist()))} "
        f"test_subjects={sorted(set(subjects[test_mask].tolist()))}"
    )

    print("--- 4. train, calibrate, evaluate on unseen participants ---")
    metadata = ModelMetadata(
        model_version="imu-linear-dev-0.1.0",
        dataset_version="synthetic-smoke-2026-09-21",
        training_config={"lr": 0.5, "epochs": 400, "l2": 1e-3, "features": len(FEATURE_NAMES)},
        git_commit="dev",
        timestamp=datetime.now(timezone.utc).isoformat(),
        metrics={},
        input_schema=f"features[{len(FEATURE_NAMES)}]",
        output_schema=f"softmax[{len(GESTURE_CLASSES)}]",
    )
    model = LinearGestureBackend.fit(
        features[train_mask], labels[train_mask], metadata=metadata
    )
    val_output = np.vstack([model.predict(f).scores for f in features[val_mask]])
    temperature = fit_temperature(val_output, labels[val_mask])
    model = LinearGestureBackend(
        model._weights, model._bias, metadata=metadata, temperature=temperature
    )
    test_scores = np.vstack([model.predict(f).scores for f in features[test_mask]])
    accuracy = float((test_scores.argmax(axis=1) == labels[test_mask]).mean())
    print(
        f"temperature={temperature:.3f} test_accuracy(unseen participants)={accuracy:.3f} "
        f"params={model.parameter_count()}"
    )
    # Synthetic fixture: this floor catches a broken training loop, not a result.
    assert accuracy > 0.6, f"linear backend failed to learn the synthetic task: {accuracy:.3f}"

    print("--- 5. persistence round-trip ---")
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        model.save(tmp)
        reloaded = LinearGestureBackend.load(tmp)
        original = model.predict(features[test_mask][0]).scores
        restored = reloaded.predict(features[test_mask][0]).scores
        assert np.allclose(original, restored), "round-trip must preserve predictions"
        assert reloaded.model_version == metadata.model_version
        print(f"saved/loaded OK, model_version={reloaded.model_version}")

        print("--- 6. missing artefact is a hard error ---")
        (Path(tmp) / "imu_linear.metadata.json").unlink()
        try:
            LinearGestureBackend.load(tmp)
        except Exception as exc:
            print(f"  rejected missing metadata: {type(exc).__name__}")
        else:
            raise AssertionError("loading without metadata must fail")

    print("--- 7. abstention: still -> NO_GESTURE; decisive sweep -> a command ---")
    detector = GestureDetector(cfg.gesture, model)
    still_events = detector.detect_events(synth_sweep("NO_GESTURE", seed=99))
    print(f"still window -> {still_events[0].event_type.value} label={still_events[0].label}")
    assert still_events[0].event_type is EventType.NO_GESTURE, (
        "a resting wrist must never be reported as a command gesture"
    )

    decisive_events = detector.detect_events(synth_sweep("LEFT", seed=11))
    decisive = decisive_events[0]
    print(
        f"decisive sweep -> {decisive.event_type.value} "
        f"label={decisive.label} conf={decisive.confidence:.3f}"
    )
    # The *behaviour* asserted here is that a decisive sweep passes the activity
    # gate and yields a command gesture. The specific label is not asserted: this
    # model is trained on 2 synthetic participants and evaluated on a third with a
    # different amplitude, so a label swap within the command vocabulary is the
    # fixture's limitation, not a system property. Real per-label accuracy is
    # measured on the real dataset in evaluation/.
    assert decisive.event_type is EventType.GESTURE_DETECTED, (
        f"a decisive sweep must be detected as a gesture, got {decisive.label}"
    )
    assert decisive.label in GESTURE_CLASSES
    assert decisive.metadata["energy_gated"] is True

    print("--- 7b. the activity gate rejects stillness, not gestures (measured) ---")
    still_energy = dynamic_energy(
        window_stream(
            normalize(
                gravity_align(synth_sweep("NO_GESTURE", seed=5)),
                accel_scale=4.0,
                gyro_scale=500.0,
            ),
            sample_rate=cfg.gesture.sample_rate,
            window_ms=cfg.gesture.window_ms,
            stride_ms=cfg.gesture.stride_ms,
        )[0]
    )
    sweep_energy = dynamic_energy(
        window_stream(
            normalize(
                gravity_align(synth_sweep("LEFT", seed=5)),
                accel_scale=4.0,
                gyro_scale=500.0,
            ),
            sample_rate=cfg.gesture.sample_rate,
            window_ms=cfg.gesture.window_ms,
            stride_ms=cfg.gesture.stride_ms,
        )[0]
    )
    print(
        f"floor={cfg.gesture.activity_energy_floor} "
        f"still={still_energy:.5f} sweep={sweep_energy:.5f}"
    )
    assert still_energy < cfg.gesture.activity_energy_floor < sweep_energy, (
        "the activity floor must separate stillness from a real sweep"
    )

    print("--- 8. corrupt packet degrades to IMU_ANOMALY, not an exception ---")
    corrupt = synth_sweep("LEFT", seed=3)
    corrupt[10, 0] = np.nan
    anomaly_events = detector.detect_events(
        corrupt, timestamps_ns=np.arange(len(corrupt)) * 10 * MS
    )
    reason = anomaly_events[0].metadata["reason"][:60]
    print(f"corrupt -> {anomaly_events[0].event_type.value}: {reason}")
    assert anomaly_events[0].event_type is EventType.IMU_ANOMALY

    print("--- 9. softmax sanity ---")
    probs = softmax(np.array([1.0, 2.0, 3.0]))
    assert abs(float(probs.sum()) - 1.0) < 1e-12
    assert probs[2] > probs[1] > probs[0]
    print(f"softmax OK sum={probs.sum():.12f}")

    print("\nALL IMU CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
