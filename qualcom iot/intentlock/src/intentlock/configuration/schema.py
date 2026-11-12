"""Configuration loading and validation.

Why Pydantic
------------
Thresholds and temporal budgets are the system's tuned parameters, and a silent
typo in a YAML file could disable the safety gate. Pydantic gives us range
checks, cross-field consistency rules and a readable error trace, all evaluated
*before* the pipeline starts, so the system refuses to run misconfigured rather
than behaving surprisingly.

Nothing in IntentLock reads a threshold from anywhere except this module.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    """Base model that rejects unknown keys.

    Unknown keys are almost always typos (`wake_threshhold`); failing loudly
    beats a silently-ignored safety parameter.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class AudioConfig(_StrictModel):
    """Wake-word detection parameters."""

    sample_rate: int = Field(default=16000, ge=8000, le=48000)
    frame_ms: int = Field(default=25, ge=10, le=100)
    hop_ms: int = Field(default=10, ge=5, le=50)
    n_mels: int = Field(default=40, ge=16, le=128)
    # Minimum calibrated wake probability to emit WAKE_DETECTED.
    wake_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    # Linear RMS of ambient noise above which the environment is considered too
    # noisy to trust a wake detection. 0.02 RMS is -34 dBFS: a busy room with
    # background conversation. Above this the audio-quality factor is zeroed, so
    # the safety gate discounts the detection rather than trusting it.
    max_noise_rms: float = Field(default=0.02, gt=0.0)
    backend: Literal["simulator", "sounddevice"] = "simulator"

    @model_validator(mode="after")
    def _hop_not_larger_than_frame(self) -> AudioConfig:
        if self.hop_ms > self.frame_ms:
            raise ValueError("audio.hop_ms must not exceed audio.frame_ms")
        return self


class VisionConfig(_StrictModel):
    """Presence, identity and attention parameters."""

    frame_rate: float = Field(default=5.0, gt=0.0, le=60.0)
    person_threshold: float = Field(default=0.60, ge=0.0, le=1.0)
    identity_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    attention_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
    # When two identities are within this margin the result is AMBIGUOUS, never
    # a coin-flip for the higher one.
    ambiguity_margin: float = Field(default=0.15, ge=0.0, le=1.0)
    # Seconds of no usable frame before VISION_OCCLUDED is emitted.
    occlusion_timeout_s: float = Field(default=1.5, gt=0.0)
    max_faces: int = Field(default=4, ge=1, le=16)
    backend: Literal["simulator", "opencv"] = "simulator"

    @model_validator(mode="after")
    def _identity_above_person(self) -> VisionConfig:
        if self.identity_threshold < self.person_threshold:
            raise ValueError(
                "vision.identity_threshold must be >= vision.person_threshold "
                "(identity is only meaningful once a person is present)"
            )
        return self


class GestureConfig(_StrictModel):
    """IMU gesture recognition parameters."""

    sample_rate: int = Field(default=100, ge=25, le=400)
    window_ms: int = Field(default=800, ge=200, le=3000)
    stride_ms: int = Field(default=200, ge=50, le=1000)
    accel_range_g: float = Field(default=8.0, gt=0.0, le=16.0)
    gyro_range_dps: float = Field(default=1000.0, gt=0.0, le=4000.0)
    confidence_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    # Below this confidence the window is UNKNOWN, never the argmax gesture.
    unknown_floor: float = Field(default=0.45, ge=0.0, le=1.0)
    # A window whose peak magnitude exceeds this is treated as a sensor artefact
    # rather than a gesture (e.g. a drop or a loose cable).
    max_accel_g: float = Field(default=12.0, gt=0.0)
    # Minimum *dynamic* normalised inertial energy (var(|a|) + mean(|g|^2)) for a
    # window to be treated as motion. Calibrated from measurement: a resting wrist
    # measures ~0.001, a decisive arm sweep ~0.057. See imu/detector.py for why the
    # gravity-removed form is required (raw magnitude energy is dominated by gravity
    # and would invert the gate).
    activity_energy_floor: float = Field(default=0.004, ge=0.0, le=1.0)
    backend: Literal["simulator", "arduino_bridge", "csv_replay"] = "simulator"

    @model_validator(mode="after")
    def _window_and_thresholds(self) -> GestureConfig:
        if self.stride_ms > self.window_ms:
            raise ValueError("gesture.stride_ms must not exceed gesture.window_ms")
        if self.unknown_floor > self.confidence_threshold:
            raise ValueError(
                "gesture.unknown_floor must be <= gesture.confidence_threshold "
                "(otherwise no window could ever be accepted)"
            )
        if self.window_ms % self.stride_ms != 0:
            raise ValueError("gesture.window_ms must be a multiple of gesture.stride_ms")
        return self
class TemporalConfig(_StrictModel):
    """Temporal constraints on interaction sequences.

    These encode the behavioural handshake. Each ordered pair is a *maximum*
    allowed gap in milliseconds; minimum gaps encode the smallest separation
    below which two events are considered simultaneous (and therefore not a valid
    ordered handshake).
    """

    # person -> wake: the user must be seen before, and shortly before, waking it.
    person_to_wake_max_ms: int = Field(default=5000, gt=0)
    # wake -> gesture: the command gesture must follow the wake word promptly.
    wake_to_gesture_max_ms: int = Field(default=4000, gt=0)
    wake_to_gesture_min_ms: int = Field(default=120, ge=0)
    # gesture -> confirmation (optional explicit confirmation).
    gesture_to_confirm_max_ms: int = Field(default=3000, gt=0)
    # Total budget for one whole interaction.
    max_sequence_ms: int = Field(default=12000, gt=0)
    # Events older than this are expired and cannot join a live sequence.
    event_ttl_ms: int = Field(default=15000, gt=0)
    # Tolerance used when ordering events; events closer than this are ordered by
    # arrival rather than by timestamp, absorbing jitter between clocks.
    ordering_jitter_ms: int = Field(default=40, ge=0)

    @model_validator(mode="after")
    def _budgets_consistent(self) -> TemporalConfig:
        if self.wake_to_gesture_min_ms >= self.wake_to_gesture_max_ms:
            raise ValueError("wake_to_gesture_min_ms must be < wake_to_gesture_max_ms")
        if self.max_sequence_ms < self.wake_to_gesture_max_ms:
            raise ValueError(
                "max_sequence_ms must be >= wake_to_gesture_max_ms "
                "(a valid handshake must fit inside the sequence budget)"
            )
        if self.event_ttl_ms < self.max_sequence_ms:
            raise ValueError(
                "event_ttl_ms must be >= max_sequence_ms "
                "(otherwise live events expire before a sequence can complete)"
            )
        return self


class IntentConfig(_StrictModel):
    """Fusion and evidence thresholds."""

    # Minimum calibrated probability of intent required for EXECUTE.
    intent_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    # Below this the result is REJECT rather than RETRY (hopeless vs. incomplete).
    reject_below: float = Field(default=0.35, ge=0.0, le=1.0)
    # Number of independent evidence sources that must contribute.
    minimum_evidence: int = Field(default=3, ge=1, le=4)
    # Identity consistency is a hard constraint by default: events must agree on subject.
    require_identity_consistency: bool = True
    # Weight of the temporal-ordering term in the fusion score.
    ordering_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    # Weight of the identity-consistency term.
    identity_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    # Weight of the context term (attention, noise, occlusion).
    context_weight: float = Field(default=0.10, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> IntentConfig:
        if self.reject_below >= self.intent_threshold:
            raise ValueError("intent.reject_below must be < intent.intent_threshold")
        total = self.ordering_weight + self.identity_weight + self.context_weight
        if total > 1.0 + 1e-9:
            raise ValueError(
                f"intent weights must sum to <= 1.0, got {total:.3f} "
                "(the remainder is carried by the modality evidence)"
            )
        return self


class SafetyConfig(_StrictModel):
    """Safety-gate and execution-control parameters."""

    # One physical gesture must not produce two commands.
    cooldown_ms: int = Field(default=1500, ge=0)
    # After a RETRY decision, how long the user has to complete the handshake.
    retry_timeout_ms: int = Field(default=5000, gt=0)
    max_retries: int = Field(default=2, ge=0, le=10)
    # Hard ceiling on executed commands per rolling minute.
    max_commands_per_minute: int = Field(default=12, ge=1)
    # If identity cannot be established, refuse rather than assume identity.
    require_verified_identity: bool = True
    # Treat sensor loss as a rejection reason (never silently assume a modality).
    reject_on_sensor_loss: bool = True
    executor: Literal["logging", "null", "uno_q"] = "logging"

    @model_validator(mode="after")
    def _cooldown_below_retry(self) -> SafetyConfig:
        if self.cooldown_ms >= self.retry_timeout_ms:
            raise ValueError(
                "safety.cooldown_ms must be < safety.retry_timeout_ms "
                "(otherwise the cooldown swallows the retry window)"
            )
        return self




class StateMachineConfig(_StrictModel):
    """State-machine timing."""

    # How long the system waits in a WAITING_* state before giving up.
    wake_timeout_ms: int = Field(default=4000, gt=0)
    gesture_timeout_ms: int = Field(default=4000, gt=0)
    verify_timeout_ms: int = Field(default=500, gt=0)
    # Presence is considered lost after this much silence from vision.
    person_timeout_ms: int = Field(default=3000, gt=0)


class TelemetryConfig(_StrictModel):
    """Telemetry emission."""

    enabled: bool = True
    output_dir: str = "./artifacts/telemetry"
    # Raw audio/frames are never written; this flag exists so the invariant is
    # explicit and can be asserted in tests.
    record_raw_media: Literal[False] = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class PrivacyConfig(_StrictModel):
    """Privacy controls.

    The defaults are the strict ones; loosening them requires an explicit edit,
    which is the point.
    """

    # Face embeddings are kept in memory only and never written to disk.
    persist_biometrics: Literal[False] = False
    # Identity is stored as a salted hash, never a name or image.
    identity_hash_salt: str = Field(default="intentlock-default-salt", min_length=8)
    # Maximum number of in-memory embeddings per session before LRU eviction.
    max_embeddings_in_memory: int = Field(default=64, ge=1)
    # Seconds that an interaction record is retained in the in-memory buffer.
    interaction_retention_s: int = Field(default=300, gt=0)


class DatasetConfig(_StrictModel):
    """Dataset locations and split policy."""

    root: str = "./data"
    # Participant-level splitting is mandatory to prevent subject leakage.
    split_policy: Literal["participant", "session"] = "participant"
    train_ratio: float = Field(default=0.6, gt=0.0, lt=1.0)
    val_ratio: float = Field(default=0.2, gt=0.0, lt=1.0)
    seed: int = Field(default=1337)

    @model_validator(mode="after")
    def _ratios_sum_below_one(self) -> DatasetConfig:
        if self.train_ratio + self.val_ratio >= 1.0:
            raise ValueError(
                "dataset.train_ratio + dataset.val_ratio must be < 1.0 "
                "(the test set is the remainder)"
            )
        return self


class IntentLockConfig(_StrictModel):
    """Root configuration object.

    This is the only configuration type the pipeline accepts. Load it with
    `load_config` (validated) and pass it down explicitly; there is no global
    config singleton.
    """

    audio: AudioConfig = Field(default_factory=AudioConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    gesture: GestureConfig = Field(default_factory=GestureConfig)
    temporal: TemporalConfig = Field(default_factory=TemporalConfig)
    intent: IntentConfig = Field(default_factory=IntentConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    state_machine: StateMachineConfig = Field(default_factory=StateMachineConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    dataset: DatasetConfig = Field(default_factory=DatasetConfig)

    @model_validator(mode="after")
    def _gesture_window_fits_temporal_budget(self) -> IntentLockConfig:
        """The gesture window must fit inside the wake->gesture budget."""
        window_ms = self.gesture.window_ms
        if window_ms > self.temporal.wake_to_gesture_max_ms:
            raise ValueError(
                f"gesture.window_ms ({window_ms}) exceeds "
                f"temporal.wake_to_gesture_max_ms ({self.temporal.wake_to_gesture_max_ms}); "
                "a gesture could never complete inside the allowed gap"
            )
        return self
