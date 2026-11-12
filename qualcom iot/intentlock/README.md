# IntentLock

Temporal multimodal intent verification for edge AI.

## Core Hypothesis
Edge-AI assistants and IoT controllers fail not from lack of model accuracy, but from context-agnostic single-modality triggering (false positives from ambient speech or random motion) and clock desynchronization across sensors. IntentLock solves this via a deterministic, low-latency, multi-modal temporal event synchronization and safety-gate engine.

## Quick Start
```bash
pip install -e .[dev]
pytest
python scripts/dev_check_imu.py
```

## Repository Layout
- `src/intentlock/`: Core pipeline modules (audio, vision, imu, synchronization, context, fusion, intent, safety, state_machine, actions, telemetry, simulator, cli)
- `tests/`: Unit, integration, and system test suites
- `configs/`: Default system configuration YAML
- `scripts/`: Developer smoke checks and demo runners
- `docs/`: Architecture, evaluations, datasets, security, and ADRs

## Documentation
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and spec references in §42.
