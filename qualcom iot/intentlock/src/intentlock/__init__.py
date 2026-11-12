"""IntentLock: temporal multimodal intent verification for edge AI.

The package is organised as a strict pipeline. Each stage has a single
responsibility and communicates with the next via explicit types:

    perception  -> Event            (audio/vision/imu)
    synchronise -> AlignedSequence  (synchronization)
    context     -> ContextState     (context)
    fuse        -> IntentEvidence   (fusion)
    decide      -> Decision         (intent + safety)
    act         -> ActionResult     (actions)

`Event` is the central exchange type: every modality reasons in events, never
in raw streams, past the point of perception.
"""

from intentlock.common.errors import (
    ActionError,
    ConfigurationError,
    IntentLockError,
    MalformedInputError,
    ModelError,
    SensorError,
    SensorUnavailable,
    SequenceError,
)
from intentlock.common.events import Event, EventType, Source

__all__ = [
    "ActionError",
    "ConfigurationError",
    "Event",
    "EventType",
    "IntentLockError",
    "MalformedInputError",
    "ModelError",
    "SensorError",
    "SensorUnavailable",
    "SequenceError",
    "Source",
]

__version__ = "0.1.0"
