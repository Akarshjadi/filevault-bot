"""Audio: preprocessing, spectral features and wake-word detection."""

from intentlock.audio.detector import WakeDetection, WakeWordDetector
from intentlock.audio.features import (
    AudioFeatures,
    AudioRepresentation,
    hz_to_mel,
    mel_filterbank,
    mel_to_hz,
    preprocess_audio,
    resample_linear,
)

__all__ = [
    "AudioFeatures",
    "AudioRepresentation",
    "WakeDetection",
    "WakeWordDetector",
    "hz_to_mel",
    "mel_filterbank",
    "mel_to_hz",
    "preprocess_audio",
    "resample_linear",
]

