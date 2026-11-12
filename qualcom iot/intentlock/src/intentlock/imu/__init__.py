"""IMU: gesture vocabulary, preprocessing, classification and detection."""

from intentlock.imu.classifier import (
    LinearGestureBackend,
    ModelMetadata,
    calibrate_scores,
    fit_temperature,
    softmax,
)
from intentlock.imu.detector import GestureDetection, GestureDetector
from intentlock.imu.preprocessing import (
    FEATURE_NAMES,
    GRAVITY_MS2,
    SAMPLE_COLUMNS,
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
from intentlock.imu.vocabulary import (
    COMMAND_GESTURES,
    GESTURE_CLASSES,
    GESTURE_TO_COMMAND,
    INGESTURE_TO_INTENTLOCK,
    NON_COMMAND_GESTURES,
    Gesture,
    is_command_gesture,
)

__all__ = [
    "COMMAND_GESTURES",
    "FEATURE_NAMES",
    "GESTURE_CLASSES",
    "GESTURE_TO_COMMAND",
    "GRAVITY_MS2",
    "INGESTURE_TO_INTENTLOCK",
    "NON_COMMAND_GESTURES",
    "SAMPLE_COLUMNS",
    "Gesture",
    "GestureDetection",
    "GestureDetector",
    "ImuCalibration",
    "LinearGestureBackend",
    "ModelMetadata",
    "Window",
    "calibrate",
    "calibrate_scores",
    "dynamic_energy",
    "extract_features",
    "fit_temperature",
    "gravity_align",
    "is_command_gesture",
    "normalize",
    "softmax",
    "validate",
    "window_stream",
]
