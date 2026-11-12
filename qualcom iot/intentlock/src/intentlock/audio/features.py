"""Audio feature extraction: framing, windowing, log-mel and MFCC, noise estimate.

Design note on representation choice
------------------------------------
The specification says "Do not blindly use MFCC if experiments show a better
representation. Benchmark alternatives where reasonable."

Both representations are implemented and selectable so the comparison can be run
rather than asserted (`training/benchmark_representations.py`):

* `log_mel` — 40 log-mel filterbank energies. Preserves the spectral envelope
  with no decorrelation step; better for a small CNN.
* `mfcc` — DCT of the log-mel spectrum. Decorrelates and compresses, which is
  the classic choice when the input to a small feature-based classifier must be a
  few dozen numbers.

**Default is `mfcc`**, because the deployed wake-word model is a small
feature-based classifier rather than a CNN, and decorrelation matters more than
the discarded detail at that size. This is a documented default backed by a
runnable comparison, not an assumption.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from intentlock.common.errors import MalformedInputError

_EPS = 1e-10


class AudioRepresentation(str, Enum):
    """Which spectral representation to produce."""

    LOG_MEL = "log_mel"
    MFCC = "mfcc"


@dataclass(frozen=True, slots=True)
class AudioFeatures:
    """Spectral features plus the quality metadata the pipeline needs.

    Attributes
    ----------
    features:
        ``(n_frames, n_coefficients)`` matrix.
    noise_estimate:
        Robust estimate of the background noise level (dBFS). The safety gate uses
        it: a wake detection in a very noisy environment is less trustworthy even
        at the same model confidence.
    snr_db:
        Estimated signal-to-noise ratio in dB. Negative means the signal is below
        the noise floor.
    rms:
        Overall root-mean-square amplitude.
    """

    features: np.ndarray
    noise_estimate: float
    snr_db: float
    rms: float

    @property
    def n_frames(self) -> int:
        """Number of frames."""
        return int(self.features.shape[0])

    @property
    def n_coefficients(self) -> int:
        """Number of coefficients per frame."""
        return int(self.features.shape[1])

    def to_vector(self) -> np.ndarray:
        """Flatten to a fixed-length vector, averaging frames.

        Averaging over frames turns a frame sequence into a single fixed-length
        input for a small classifier. It deliberately loses fine temporal detail,
        which is acceptable for a *keyword* detector because the keyword occupies
        most of the analysis window. A CNN backend can consume `features` directly
        instead.
        """
        return self.features.mean(axis=0)


def preprocess_audio(
    samples: np.ndarray,
    *,
    sample_rate: int,
    frame_ms: int,
    hop_ms: int,
    n_mels: int,
    representation: AudioRepresentation = AudioRepresentation.MFCC,
    n_mfcc: int = 13,
    n_fft: int = 512,
) -> AudioFeatures:
    """Run the full audio preprocessing chain.

    ``raw -> validate -> DC-remove -> frame -> window -> power spectrum ->
     mel filterbank -> log -> (optionally DCT -> MFCC) -> utterance normalise``

    Parameters
    ----------
    samples:
        Mono float waveform in [-1, 1]. Values outside that range are an error:
        they mean the caller skipped normalisation, and silently clipping would
        hide a gain-staging bug.
    sample_rate:
        Sampling rate in Hz.
    frame_ms, hop_ms:
        25 ms / 10 ms is the standard speech-analysis setting.
    n_mels:
        Number of mel filterbank channels.
    representation:
        `LOG_MEL` or `MFCC`.
    n_mfcc:
        Number of cepstral coefficients when `representation` is `MFCC`.
    n_fft:
        FFT size; must be at least the frame length.

    Raises
    ------
    MalformedInputError
        For non-finite samples, out-of-range values, or a buffer too short to
        frame. A short buffer is an error rather than a padded one because
        padding fabricates signal.
    """
    if not isinstance(samples, np.ndarray):
        raise MalformedInputError(f"audio must be a numpy array, got {type(samples).__name__}")
    if samples.ndim != 1:
        raise MalformedInputError(f"audio must be mono (1-D), got shape {samples.shape}")
    if not np.isfinite(samples).all():
        raise MalformedInputError("audio contains non-finite samples")
    if samples.size == 0:
        raise MalformedInputError("audio buffer is empty")
    peak = float(np.max(np.abs(samples)))
    if peak > 1.0 + 1e-6:
        raise MalformedInputError(
            f"audio samples must be in [-1, 1]; peak was {peak:.3f}. Normalise before "
            "calling (a clipped signal would hide a gain-staging bug)."
        )

    frame_length = round(sample_rate * frame_ms / 1000)
    hop_length = round(sample_rate * hop_ms / 1000)
    if n_fft < frame_length:
        raise MalformedInputError(
            f"n_fft ({n_fft}) must be >= frame length ({frame_length} samples)"
        )
    if samples.size < frame_length:
        raise MalformedInputError(
            f"audio buffer ({samples.size} samples) is shorter than one frame ({frame_length})"
        )

    signal = samples.astype(np.float64, copy=True)
    signal -= float(signal.mean())  # remove the ADC's DC offset
    rms = float(np.sqrt(np.mean(signal**2)))
    noise_estimate = _noise_floor_db(signal)

    frames = _frame(signal, frame_length, hop_length)
    windowed = frames * np.hanning(frame_length)
    power = np.abs(np.fft.rfft(windowed, n=n_fft, axis=1)) ** 2

    filters = mel_filterbank(sample_rate=sample_rate, n_fft=n_fft, n_mels=n_mels)
    log_mel = np.log(power @ filters.T + _EPS)

    if representation is AudioRepresentation.LOG_MEL:
        features = log_mel
    else:
        # DCT-II of the log-mel spectrum; orthonormal so coefficient magnitudes
        # are comparable across coefficient index.
        features = log_mel @ _dct_matrix(n_mels, n_mfcc).T

    # Per-utterance mean/variance normalisation removes gain and channel
    # differences between microphones, which is the largest source of
    # cross-device variation for a wake word.
    mean = features.mean(axis=0, keepdims=True)
    std = features.std(axis=0, keepdims=True)
    features = (features - mean) / np.maximum(std, _EPS)

    signal_db = 20.0 * np.log10(max(rms, _EPS))
    return AudioFeatures(
        features=np.ascontiguousarray(features),
        noise_estimate=noise_estimate,
        snr_db=float(signal_db - noise_estimate),
        rms=rms,
    )


# ---------------------------------------------------------------------------
# Signal-processing primitives
# ---------------------------------------------------------------------------


def _frame(signal: np.ndarray, frame_length: int, hop_length: int) -> np.ndarray:
    """Split a 1-D signal into overlapping frames.

    Uses stride tricks so no data is copied, then materialises the result because
    downstream FFTs need a contiguous array.
    """
    n_frames = 1 + (len(signal) - frame_length) // hop_length
    if n_frames <= 0:
        raise MalformedInputError(
            f"signal of {len(signal)} samples yields no frames at "
            f"frame_length={frame_length}, hop={hop_length}"
        )
    shape = (n_frames, frame_length)
    strides = (signal.strides[0] * hop_length, signal.strides[0])
    frames = np.lib.stride_tricks.as_strided(signal, shape=shape, strides=strides)
    return np.ascontiguousarray(frames)


def hz_to_mel(frequency_hz: np.ndarray | float) -> np.ndarray:
    """Convert Hz to the mel scale (HTK formula)."""
    return 2595.0 * np.log10(1.0 + np.asarray(frequency_hz, dtype=np.float64) / 700.0)


def mel_to_hz(mel: np.ndarray | float) -> np.ndarray:
    """Inverse of `hz_to_mel`."""
    return 700.0 * (10.0 ** (np.asarray(mel, dtype=np.float64) / 2595.0) - 1.0)


def mel_filterbank(
    *,
    sample_rate: int,
    n_fft: int,
    n_mels: int,
    f_min: float = 20.0,
    f_max: float | None = None,
) -> np.ndarray:
    """Triangular mel filterbank matrix of shape ``(n_mels, n_fft // 2 + 1)``.

    Implemented directly rather than taken from a library so the preprocessing
    chain has no optional dependency and the filter shapes are inspectable.
    `f_max` defaults to the Nyquist frequency.
    """
    if f_max is None:
        f_max = sample_rate / 2.0
    if not 0.0 < f_min < f_max <= sample_rate / 2.0:
        raise MalformedInputError(
            f"invalid mel range: f_min={f_min}, f_max={f_max}, nyquist={sample_rate / 2.0}"
        )
    if n_mels < 1:
        raise MalformedInputError(f"n_mels must be >= 1, got {n_mels}")

    n_bins = n_fft // 2 + 1
    hz_points = mel_to_hz(np.linspace(hz_to_mel(f_min), hz_to_mel(f_max), n_mels + 2))
    # Map each filter edge to the nearest FFT bin.
    bins = np.clip(np.floor((n_fft + 1) * hz_points / sample_rate).astype(int), 0, n_bins - 1)

    filters = np.zeros((n_mels, n_bins), dtype=np.float64)
    for index in range(n_mels):
        left, centre, right = bins[index], bins[index + 1], bins[index + 2]
        if centre <= left:
            centre = min(left + 1, n_bins - 1)
        if right <= centre:
            right = min(centre + 1, n_bins - 1)
        filters[index, left:centre] = (np.arange(left, centre) - left) / (centre - left)
        filters[index, centre:right] = (right - np.arange(centre, right)) / (right - centre)

    # Normalise each filter to unit area so energy comparisons across filter
    # indices are meaningful (otherwise high-frequency filters capture more bins).
    return filters / np.maximum(filters.sum(axis=1, keepdims=True), _EPS)


def _dct_matrix(n_inputs: int, n_outputs: int) -> np.ndarray:
    """Orthonormal DCT-II matrix of shape ``(n_outputs, n_inputs)``.

    Orthonormal scaling makes coefficient magnitudes comparable, which matters
    because the features are z-scored afterwards.
    """
    if n_outputs > n_inputs:
        raise MalformedInputError(
            f"cannot take {n_outputs} DCT coefficients from {n_inputs} inputs"
        )
    k = np.arange(n_outputs)[:, None]
    n = np.arange(n_inputs)[None, :]
    matrix = np.cos(np.pi * k * (2 * n + 1) / (2 * n_inputs))
    matrix *= np.sqrt(2.0 / n_inputs)
    matrix[0, :] *= np.sqrt(0.5)
    return matrix


def _noise_floor_db(signal: np.ndarray, *, percentile: float = 10.0) -> float:
    """Estimate the noise floor in dBFS from the quietest frames.

    Uses a low percentile of per-frame RMS rather than the overall RMS, so a loud
    utterance inside the buffer does not raise its own noise estimate. This is the
    robust estimate the safety gate needs: an overall-RMS figure would track the
    signal and therefore measure nothing useful.
    """
    frame_length = min(512, max(1, len(signal) // 4))
    if len(signal) < frame_length * 2:
        return 20.0 * np.log10(max(float(np.sqrt(np.mean(signal**2))), _EPS))
    n_frames = len(signal) // frame_length
    trimmed = signal[: n_frames * frame_length].reshape(n_frames, frame_length)
    per_frame_rms = np.sqrt(np.mean(trimmed**2, axis=1))
    return 20.0 * np.log10(max(float(np.percentile(per_frame_rms, percentile)), _EPS))


def resample_linear(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Resample by linear interpolation.

    ponytail: linear interpolation, adequate for matching a device's native rate
    to a model's expected rate at 8-48 kHz, where the energy relevant to a
    keyword sits well below the target Nyquist. It is not a high-fidelity
    anti-aliased resampler; upgrade to a polyphase filter if wideband content
    near or above the target Nyquist ever matters.
    """
    if source_rate == target_rate:
        return samples.astype(np.float64, copy=True)
    if source_rate <= 0 or target_rate <= 0:
        raise MalformedInputError(
            f"sample rates must be positive, got source={source_rate}, target={target_rate}"
        )
    if samples.size == 0:
        raise MalformedInputError("cannot resample an empty buffer")

    target_count = max(1, round((samples.size / source_rate) * target_rate))
    source_positions = np.arange(target_count) * (source_rate / target_rate)
    return np.interp(
        source_positions,
        np.arange(samples.size),
        samples.astype(np.float64, copy=False),
    )
