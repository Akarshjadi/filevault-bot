"""Hardware and backend abstractions.

Application code depends on these protocols, never on concrete devices.
"""

from intentlock.interfaces.backend import InferenceBackend, InferenceOutput
from intentlock.interfaces.sources import (
    AudioSource,
    EventSource,
    MotionCalibration,
    MotionSource,
    VisionSource,
)

__all__ = [
    "AudioSource",
    "EventSource",
    "InferenceBackend",
    "InferenceOutput",
    "MotionCalibration",
    "MotionSource",
    "VisionSource",
]
