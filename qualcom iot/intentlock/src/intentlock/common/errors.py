"""IntentLock exception hierarchy.

A single root exception lets callers distinguish *our* failures from bugs in
third-party code, and lets the top-level runner fail safely without a bare
`except Exception`.
"""

from __future__ import annotations


class IntentLockError(Exception):
    """Root of every error raised deliberately by IntentLock."""


class ConfigurationError(IntentLockError):
    """Raised when configuration is missing, malformed, or self-inconsistent.

    IntentLock refuses to start with an invalid configuration rather than
    silently falling back to defaults.
    """


class SensorError(IntentLockError):
    """Base class for sensor/perception failures."""


class SensorUnavailableError(SensorError):
    """A required sensor cannot be opened or has disappeared mid-session.

    This is a *degradable* condition: the pipeline must record the loss and
    continue with reduced evidence, never assume a value for the missing
    modality.
    """


#: Backwards-compatible alias. The canonical name carries the `Error` suffix the
#: project lint rules require; the short name is kept because it reads naturally
#: at call sites that catch it as a condition.
SensorUnavailable = SensorUnavailableError


class MalformedInputError(SensorError):
    """A packet failed validation (length, range, timestamp, NaN, ...).

    Sensor input is untrusted; this is the boundary where bad data is rejected.
    """


class SequenceError(IntentLockError):
    """A temporal sequence is impossible, expired, or out of order."""


class ModelError(IntentLockError):
    """A model could not be loaded, or its I/O schema does not match the contract."""


class ActionError(IntentLockError):
    """An action was requested that is not in the executor's allowlist,
    or an action failed to execute.
    """
