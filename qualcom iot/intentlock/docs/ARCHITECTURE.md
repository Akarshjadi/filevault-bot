# Architecture & System Design

IntentLock is a deterministic, temporal multimodal intent verification pipeline for edge AI and IoT devices.

## System Architecture

```
                  +-----------------------------------+
                  │           Physical Sensors        │
                  │   (Audio, Vision, IMU, Clock)     │
                  +-----------------+-----------------+
                                    │
                                    v
                  +-----------------------------------+
                  │        Perception Subsystems      │
                  │   (Audio, Vision, IMU Detectors)  │
                  +-----------------+-----------------+
                                    │
                                    v
                  +-----------------------------------+
                  │         Event & Timestamp Engine   │
                  │        (Monotonic Session Clock)  │
                  +-----------------+-----------------+
                                    │
                                    v
                  +-----------------------------------+
                  │         Context & State Engine    │
                  │      (Tracking Active State & Health)
                  +-----------------+-----------------+
                                    │
                                    v
                  +-----------------------------------+
                  │         Temporal Matcher          │
                  │    (Enforces Handshake Sequences) │
                  +-----------------+-----------------+
                                    │
                                    v
                  +-----------------------------------+
                  │           Fusion Engine           │
                  │   (Evidence Weighting & Scoring)  │
                  +-----------------+-----------------+
                                    │
                                    v
                  +-----------------------------------+
                  │        Intent Uncertainty Gate    │
                  │     (Entropy & Abstention Rules)  │
                  +-----------------+-----------------+
                                    │
                                    v
                  +-----------------------------------+
                  │            Safety Gate            │
                  │   (Independent Permission Owner)  │
                  +-----------------+-----------------+
                                    │
                                    v
                  +-----------------------------------+
                  │        State Machine & Actions    │
                  │     (Cooldown, Executor, Safe)    │
                  +-----------------------------------+
```

## Component Table

| Component | Module Path | Responsibility |
| --- | --- | --- |
| Audio Subsystem | `intentlock.audio` | Wake-word detection, feature extraction, noise estimation |
| Vision Subsystem | `intentlock.vision` | Presence, identity hashing, attention estimation |
| IMU Subsystem | `intentlock.imu` | Inertial preprocessing, gesture classification |
| Synchronization | `intentlock.synchronization` | Monotonic session clock, temporal windowing, sequence matching |
| Context Engine | `intentlock.context` | Session state snapshots, sensor health monitoring |
| Fusion Engine | `intentlock.fusion` | Late and temporal fusion strategies |
| Intent Engine | `intentlock.intent` | Temperature scaling, uncertainty estimation, decision mapping |
| Safety Gate | `intentlock.safety` | Independent permission gating, cooldown tracking, rolling rate limits |
| State Machine | `intentlock.state_machine` | Deterministic interaction state transitions |
| Actions | `intentlock.actions` | Allowlisted command execution |
| Telemetry | `intentlock.telemetry` | Secure, privacy-preserving JSONL record logging |
| CLI & Simulator | `intentlock.cli`, `intentlock.simulator` | Command-line orchestration and session replay simulation |
