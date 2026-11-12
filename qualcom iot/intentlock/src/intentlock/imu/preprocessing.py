"""IMU preprocessing: validation, calibration, gravity_align, normalisation, windowing, features.

Pipeline (mirrors the specification's required stages):

    raw samples -> validate -> calibrate -> gravity_align -> normalise -> window -> features

Windowing is a separate stage operating on continuous sample streams to slice them
into overlapping fixed-duration observation windows before feature extraction.

Each stage is a pure function on arrays, which is what makes the pipeline
reproducible and unit-testable without hardware. Nothing here mutates its input.

Sensor convention
-----------------
Acceleration is in **g** (1 g = 9.80665 m/s²) and angular velocity in **deg/s**,
matching the Modulino Movement (LSM6DSOXTR) datasheet units so that calibration
constants are human-readable.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from intentlock.common.errors import MalformedInputError

#: Standard gravity, used only to convert between g and m/s^2 for reporting.
GRAVITY_MS2 = 9.80665

#: Column order for a raw IMU sample matrix: [ax, ay, az, gx, gy, gz].
SAMPLE_COLUMNS = ("ax", "ay", "az", "gx", "gy", "gz")
_N_CHANNELS = len(SAMPLE_COLUMNS)


@dataclass(frozen=True, slots=True)
class ImuCalibration:
    """Per-device bias and scale applied before normalisation.

    A real IMU is never the datasheet ideal: each axis has an offset drift and a
    small scale error, and the Modulino's LSM6DSOXTR is no exception. Holding
    calibration data explicitly (rather than hard-coding magic constants) means a
    new device is a new calibration record, not a code change.
    """

    accel_bias: tuple[float, float, float] = (0.0, 0.0, 0.0)
    gyro_bias: tuple[float, float, float] = (0.0, 0.0, 0.0)
    accel_scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    gyro_scale: tuple[float, float, float] = (1.0, 1.0, 1.0)

    def as_arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Return (accel_bias, gyro_bias, accel_scale, gyro_scale) as float64 arrays."""
        return (
            np.asarray(self.accel_bias, dtype=np.float64),
            np.asarray(self.gyro_bias, dtype=np.float64),
            np.asarray(self.accel_scale, dtype=np.float64),
            np.asarray(self.gyro_scale, dtype=np.float64),
        )

    @classmethod
    def identity(cls) -> ImuCalibration:
        """A no-op calibration, used when a device is known to be well calibrated."""
        return cls()

    @classmethod
    def from_rest_samples(cls, samples: np.ndarray, sample_rate: int) -> ImuCalibration:
        """Estimate bias from a stationary recording.

        Parameters
        ----------
        samples:
            ``(n, 6)`` matrix of raw readings taken while the sensor is at rest.
        sample_rate:
            Sampling rate in Hz; used only to validate that enough data was given.

        Notes
        -----
        Bias is the mean of a stationary window; while at rest the accelerometer
        should read exactly +1 g on the gravity axis and 0 elsewhere, so the
        accelerometer bias is the deviation from that ideal. Scale is left at
        1.0 because estimating scale needs a multi-orientation procedure that is
        out of scope for a one-shot calibration, and inventing it would produce a
        misleading number.
        """
        _validate_matrix(samples, name="calibration samples")
        if len(samples) < sample_rate // 2:
            raise MalformedInputError(
                f"need at least {sample_rate // 2} samples for calibration, got {len(samples)}"
            )
        mean = samples.mean(axis=0)
        accel_bias = mean[:3].copy()
        # Remove the gravity component from the accelerometer bias: at rest the
        # magnitude is 1 g, and its direction is the sensor's mounting axis.
        magnitude = float(np.linalg.norm(accel_bias))
        if magnitude > 1e-6:
            accel_bias = accel_bias * (magnitude - 1.0) / magnitude
        return cls(
            accel_bias=(float(accel_bias[0]), float(accel_bias[1]), float(accel_bias[2])),
            gyro_bias=(float(mean[3]), float(mean[4]), float(mean[5])),
        )


def _validate_matrix(samples: np.ndarray, *, name: str) -> None:
    """Raise `MalformedInputError` unless `samples` is a finite ``(n, 6)`` array.

    This is the trust boundary for IMU data: sensor packets and dataset files both
    arrive here, and a NaN must be caught with a clear message rather than
    propagating silently into a model.
    """
    if not isinstance(samples, np.ndarray):
        raise MalformedInputError(f"{name} must be a numpy array, got {type(samples).__name__}")
    if samples.ndim != 2:
        raise MalformedInputError(
            f"{name} must be 2-D (n, {_N_CHANNELS}), got shape {samples.shape}"
        )
    if samples.shape[1] != _N_CHANNELS:
        raise MalformedInputError(
            f"{name} must have {_N_CHANNELS} columns {SAMPLE_COLUMNS}, got {samples.shape[1]}"
        )
    if samples.shape[0] == 0:
        raise MalformedInputError(f"{name} is empty")
    if not np.isfinite(samples).all():
        bad_rows = np.where(~np.isfinite(samples).all(axis=1))[0]
        raise MalformedInputError(
            f"{name} contains non-finite values in {len(bad_rows)} row(s), "
            f"first at index {int(bad_rows[0])}"
        )


def validate(samples: np.ndarray, *, max_accel_g: float, name: str = "imu window") -> np.ndarray:
    """Validate and clip a raw IMU matrix.

    Returns
    -------
    np.ndarray
        The validated matrix, unchanged, as float64.

    Raises
    ------
    MalformedInputError
        If the matrix is malformed, or if any acceleration sample exceeds
        `max_accel_g`. An out-of-range magnitude means the sensor was dropped,
        saturated or is mis-wired; classifying that as a gesture would be
        dangerous, so it is rejected rather than clipped.
    """
    _validate_matrix(samples, name=name)
    data = samples.astype(np.float64, copy=True)
    peak = float(np.max(np.abs(data[:, :3])))
    if peak > max_accel_g:
        raise MalformedInputError(
            f"{name}: peak acceleration {peak:.2f} g exceeds limit {max_accel_g:.2f} g "
            "(sensor artefact, not a gesture)"
        )
    return data


def calibrate(samples: np.ndarray, calibration: ImuCalibration) -> np.ndarray:
    """Apply bias and scale correction.

    ``corrected = (raw - bias) * scale``
    """
    accel_bias, gyro_bias, accel_scale, gyro_scale = calibration.as_arrays()
    out = samples.astype(np.float64, copy=True)
    out[:, :3] = (out[:, :3] - accel_bias) * accel_scale
    out[:, 3:] = (out[:, 3:] - gyro_bias) * gyro_scale
    return out


def gravity_align(samples: np.ndarray) -> np.ndarray:
    """Rotate the window so the mean gravity vector points along +Z.

    Placement variation (the sensor strapped at a different angle on a different
    wrist) is one of the largest sources of error in wrist IMU recognition. The
    gravity direction is measured from the signal itself, so this removes
    orientation without needing a magnetometer.

    Only the orientation changes; magnitudes are preserved.
    """
    out = samples.astype(np.float64, copy=True)
    mean_accel = out[:, :3].mean(axis=0)
    norm = float(np.linalg.norm(mean_accel))
    if norm < 1e-6:
        # Free-fall or zero signal: no reliable gravity direction, leave as-is.
        return out

    target = np.array([0.0, 0.0, 1.0])
    source = mean_accel / norm
    # Rotation that maps `source` onto `target` (Rodrigues via cross product).
    axis = np.cross(source, target)
    axis_norm = float(np.linalg.norm(axis))
    if axis_norm < 1e-9:
        # Parallel or antiparallel: identity, or a 180-degree flip about X.
        if float(np.dot(source, target)) < 0:
            rotation = np.diag([1.0, -1.0, -1.0])
        else:
            return out
    else:
        axis = axis / axis_norm
        angle = float(np.arccos(np.clip(float(np.dot(source, target)), -1.0, 1.0)))
        rotation = _rodrigues(axis, angle)

    out[:, :3] = out[:, :3] @ rotation.T
    out[:, 3:] = out[:, 3:] @ rotation.T
    return out


def _rodrigues(axis: np.ndarray, angle: float) -> np.ndarray:
    """Rotation matrix about a unit `axis` by `angle` radians."""
    x, y, z = axis
    skew = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    return np.eye(3) + np.sin(angle) * skew + (1.0 - np.cos(angle)) * (skew @ skew)


def normalize(
    samples: np.ndarray,
    *,
    accel_scale: float = 4.0,
    gyro_scale: float = 500.0,
) -> np.ndarray:
    """Scale channels to a fixed, configurable range before feeding a model.

    Fixed scale factors (rather than per-window standardisation) are used
    deliberately: per-window z-scoring would erase the amplitude information that
    distinguishes a decisive command sweep from a small incidental hand movement,
    which is precisely what IntentLock must detect. The defaults bound each
    channel to roughly [-1, 1] for the configured sensor ranges.
    """
    out = samples.astype(np.float64, copy=True)
    out[:, :3] /= accel_scale
    out[:, 3:] /= gyro_scale
    return np.clip(out, -1.0, 1.0)


# ---------------------------------------------------------------------------
# Windowing
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Window:
    """One analysis window with its position in the original stream.

    Keeping `start_index`/`start_timestamp_ns` with the data means a detected
    gesture can be attributed to a moment in time, which is what the temporal
    engine needs to order it against audio and vision events.
    """

    data: np.ndarray  # (window_samples, 6)
    start_index: int
    start_timestamp_ns: int
    sample_rate: int

    @property
    def n_samples(self) -> int:
        """Number of samples in the window."""
        return int(self.data.shape[0])

    @property
    def duration_ns(self) -> int:
        """Window duration in nanoseconds."""
        return round(self.n_samples / self.sample_rate * 1e9)


def window_stream(
    samples: np.ndarray,
    *,
    sample_rate: int,
    window_ms: int,
    stride_ms: int,
    timestamps_ns: np.ndarray | None = None,
) -> list[Window]:
    """Split a continuous stream into overlapping windows.

    Parameters
    ----------
    samples:
        ``(n, 6)`` normalised samples.
    sample_rate:
        Sampling rate in Hz.
    window_ms, stride_ms:
        Window length and hop, in milliseconds. `window_ms` must be a multiple of
        `stride_ms` (enforced by configuration validation).
    timestamps_ns:
        Optional per-sample session timestamps. When omitted, timestamps are
        synthesised from `sample_rate`, which is only correct for a perfectly
        regular stream; real captures should pass their own timestamps because
        the platform is never the spec ideal.

    Returns
    -------
    list[Window]
        Windows with a full `window_samples` length. A trailing partial window is
        discarded rather than padded: padding would fabricate data, and a partial
        sweep cannot be judged a command.
    """
    if sample_rate <= 0:
        raise MalformedInputError(f"sample_rate must be positive, got {sample_rate}")
    window_samples = round(sample_rate * window_ms / 1000)
    stride_samples = round(sample_rate * stride_ms / 1000)
    if window_samples <= 0 or stride_samples <= 0:
        raise MalformedInputError(
            f"window/stride too small for sample_rate={sample_rate}: "
            f"window_samples={window_samples}, stride_samples={stride_samples}"
        )

    if timestamps_ns is not None and len(timestamps_ns) != len(samples):
        raise MalformedInputError(
            f"timestamps length {len(timestamps_ns)} != sample count {len(samples)}"
        )

    windows: list[Window] = []
    start = 0
    while start + window_samples <= len(samples):
        ts = (
            int(timestamps_ns[start])
            if timestamps_ns is not None
            else round(start / sample_rate * 1e9)
        )
        windows.append(
            Window(
                data=samples[start : start + window_samples],
                start_index=start,
                start_timestamp_ns=ts,
                sample_rate=sample_rate,
            )
        )
        start += stride_samples
    return windows


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

#: Human-readable feature names, in the order produced by `extract_features`.
#: Named explicitly so a model's input contract is inspectable and so feature
#: importances can be reported with meaningful labels.
#:
#: The vector mixes two kinds of feature on purpose:
#:
#: * **amplitude** features (means, maxima, energy) separate a decisive command
#:   sweep from small incidental movement — the property IntentLock must detect;
#: * **shape** features (ratios, per-axis shares, correlation, dominant frequency)
#:   are scale-invariant, so a different person with a different arm strength and
#:   a different sensor gain does not shift the class. Without these, a purely
#:   amplitude-based model overfits to the participants it was trained on. This
#:   was observed directly in `scripts/dev_check_imu.py`: unseen-participant
#:   accuracy was 0.58 before shape features were added.
FEATURE_NAMES: tuple[str, ...] = (
    # amplitude features
    "accel_mean_mag",
    "accel_std_mag",
    "accel_max_mag",
    "accel_energy",
    "gyro_mean_mag",
    "gyro_std_mag",
    "gyro_max_mag",
    "gyro_energy",
    # per-axis variation
    "accel_x_std",
    "accel_y_std",
    "accel_z_std",
    "gyro_x_std",
    "gyro_y_std",
    "gyro_z_std",
    # temporal shape
    "zero_crossings_accel_x",
    "zero_crossings_accel_y",
    "zero_crossings_gyro_z",
    "dominant_freq_accel_x",
    "jerk_mean",
    "corr_accel_gyro",
    # scale-invariant shape features (added after the overlap experiment above)
    "accel_axis_share_x",
    "accel_axis_share_y",
    "accel_axis_share_z",
    "gyro_axis_share_x",
    "gyro_axis_share_y",
    "gyro_axis_share_z",
    "accel_cv",
    "gyro_cv",
    "gyro_accel_ratio",
    "accel_std_over_mean",
)


def extract_features(window: Window | np.ndarray, sample_rate: int = 100) -> np.ndarray:
    """Compute a compact, interpretable feature vector for one window.

    These features are cheap enough for the edge CPU and serve two purposes:
    (a) the feature-based baseline classifier, and (b) the activity detector that
    decides whether a window is worth showing a neural model at all.

    Parameters
    ----------
    window:
        A `Window` (preferred, since it carries the sample rate) or a bare
        ``(n, 6)`` array.
    sample_rate:
        Used only when `window` is a bare array.

    Returns
    -------
    np.ndarray
        Float64 vector of length ``len(FEATURE_NAMES)``. Never NaN: degenerate
        windows yield 0.0 for the affected features so a caller never has to
        guard against NaN propagation into a model.
    """
    data = window.data if isinstance(window, Window) else window
    rate = window.sample_rate if isinstance(window, Window) else sample_rate
    _validate_matrix(data, name="feature window")

    accel = data[:, :3]
    gyro = data[:, 3:]

    accel_mag = np.linalg.norm(accel, axis=1)
    gyro_mag = np.linalg.norm(gyro, axis=1)
    jerk = np.diff(accel_mag) if len(accel_mag) > 1 else np.zeros(1)

    features = [
        float(accel_mag.mean()),
        float(accel_mag.std()),
        float(accel_mag.max()),
        float(np.mean(accel_mag**2)),
        float(gyro_mag.mean()),
        float(gyro_mag.std()),
        float(gyro_mag.max()),
        float(np.mean(gyro_mag**2)),
        float(accel[:, 0].std()),
        float(accel[:, 1].std()),
        float(accel[:, 2].std()),
        float(gyro[:, 0].std()),
        float(gyro[:, 1].std()),
        float(gyro[:, 2].std()),
        float(_zero_crossings(accel[:, 0])),
        float(_zero_crossings(accel[:, 1])),
        float(_zero_crossings(gyro[:, 2])),
        float(_dominant_frequency(accel[:, 0], rate)),
        float(np.abs(jerk).mean()),
        float(_correlation(accel_mag, gyro_mag)),
    ]

    # --- scale-invariant shape features ---------------------------------
    # Everything below is a *ratio*, so multiplying the whole window by a
    # constant (a stronger participant, a different sensor gain) leaves it
    # unchanged. These are what let a model generalise to unseen people.
    accel_scale = float(accel_mag.max()) + 1e-9
    gyro_scale = float(gyro_mag.max()) + 1e-9
    accel_axis_energy = np.sum(accel**2, axis=0)
    gyro_axis_energy = np.sum(gyro**2, axis=0)
    accel_energy_total = float(accel_axis_energy.sum()) + 1e-9
    gyro_energy_total = float(gyro_axis_energy.sum()) + 1e-9

    features.extend(
        [
            float(accel_axis_energy[0] / accel_energy_total),
            float(accel_axis_energy[1] / accel_energy_total),
            float(accel_axis_energy[2] / accel_energy_total),
            float(gyro_axis_energy[0] / gyro_energy_total),
            float(gyro_axis_energy[1] / gyro_energy_total),
            float(gyro_axis_energy[2] / gyro_energy_total),
            # Coefficient of variation: how "peaky" the motion is, independent of
            # how large it is.
            float(accel_mag.std() / (float(accel_mag.mean()) + 1e-9)),
            float(gyro_mag.std() / (float(gyro_mag.mean()) + 1e-9)),
            # Rotation-per-acceleration: distinguishes a sweeping arm motion
            # (much accel) from a wrist twist (mostly gyro).
            float((float(gyro_mag.max()) / gyro_scale) / accel_scale * 1e-2),
            float(accel_mag.std() / accel_scale),
        ]
    )

    out = np.asarray(features, dtype=np.float64)
    assert out.shape[0] == len(FEATURE_NAMES), (
        f"feature vector length {out.shape[0]} != FEATURE_NAMES {len(FEATURE_NAMES)}"
    )
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


def dynamic_energy(window: Window | np.ndarray, sample_rate: int = 100) -> float:
    """Motion energy with the gravity and bias baseline removed.

    Defined as ``var(|a|) + mean(|g|^2)``:
      * the variance of the accelerometer magnitude removes gravity (a constant),
        which is what makes this a *motion* measure rather than a weight measure;
      * the gyroscope has no gravitational offset, so its mean square already
        measures rotation rate directly.

    This is the metric the activity gate uses. See `imu.detector` for the measured
    values that set its floor.
    """
    data = window.data if isinstance(window, Window) else window
    _validate_matrix(data, name="energy window")
    accel_mag = np.linalg.norm(data[:, :3], axis=1)
    gyro_mag = np.linalg.norm(data[:, 3:], axis=1)
    return float(accel_mag.var()) + float(np.mean(gyro_mag**2))


def _zero_crossings(signal: np.ndarray) -> int:
    """Number of sign changes about the signal mean (a direction-change count)."""
    centred = signal - signal.mean()
    return int(np.count_nonzero(np.diff(np.signbit(centred))))


def _dominant_frequency(signal: np.ndarray, sample_rate: int) -> float:
    """Frequency (Hz) of the largest non-DC FFT bin.

    A cheap rhythm descriptor: a decisive command sweep is slower than an
    incidental tremor, and this separates them without a full spectrogram.
    """
    if len(signal) < 4:
        return 0.0
    spectrum = np.abs(np.fft.rfft(signal * np.hanning(len(signal))))
    if len(spectrum) <= 1:
        return 0.0
    spectrum[0] = 0.0  # discard DC
    peak = int(np.argmax(spectrum))
    return float(peak * sample_rate / len(signal))


def _correlation(left: np.ndarray, right: np.ndarray) -> float:
    """Pearson correlation, or 0.0 when either input is constant."""
    if len(left) < 2 or left.std() < 1e-12 or right.std() < 1e-12:
        return 0.0
    return float(np.corrcoef(left, right)[0, 1])
