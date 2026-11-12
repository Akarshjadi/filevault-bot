"""Config ↔ YAML mapping and loading.

`load_config` is the single entry point. It validates the file, resolves paths,
and raises `ConfigurationError` with a readable message on any problem.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from intentlock.common.errors import ConfigurationError
from intentlock.configuration.schema import IntentLockConfig


def load_config(path: str | Path | None = None) -> IntentLockConfig:
    """Load and validate the runtime configuration.

    Resolution order for `path`:
      1. the explicit argument,
      2. the ``INTENTLOCK_CONFIG`` environment variable,
      3. ``./configs/intentlock.yaml``.

    A missing file is *not* an error when `path` was not given explicitly: the
    built-in defaults are complete and validated. This is what makes
    `intentlock replay session.json` work on a fresh clone. However, if
    `INTENTLOCK_CONFIG` is set in the environment, it is treated as explicit,
    so a missing file it names raises `ConfigurationError`.

    Raises
    ------
    ConfigurationError
        If the file exists but cannot be parsed, contains invalid values, or was
        named explicitly (or via `INTENTLOCK_CONFIG`) and does not exist.
    """
    env = os.environ.get("INTENTLOCK_CONFIG")
    explicit = path is not None or env is not None
    resolved = Path(path) if path is not None else (Path(env) if env else _default_config_path())

    if not resolved.exists():
        if explicit:
            raise ConfigurationError(f"configuration file not found: {resolved}")
        return IntentLockConfig()

    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid YAML in {resolved}: {exc}") from exc
    except OSError as exc:
        raise ConfigurationError(f"cannot read configuration {resolved}: {exc}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigurationError(
            f"configuration root must be a mapping, got {type(raw).__name__} in {resolved}"
        )

    return config_from_dict(raw, source=str(resolved))


def config_from_dict(raw: dict[str, Any], source: str = "<dict>") -> IntentLockConfig:
    """Validate a plain mapping into a config object.

    Raises
    ------
    ConfigurationError
        With every validation problem listed, so one run surfaces all typos.
    """
    try:
        return IntentLockConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError(
            f"invalid configuration from {source}:\n{_format_validation_error(exc)}"
        ) from exc


def to_yaml(config: IntentLockConfig) -> str:
    """Serialise a config for `intentlock config show --format yaml`."""
    return yaml.safe_dump(config.model_dump(mode="json"), sort_keys=True, indent=2)


def _default_config_path() -> Path:
    return Path("./configs/intentlock.yaml")


def _format_validation_error(exc: ValidationError) -> str:
    lines: list[str] = []
    for err in exc.errors():
        location = ".".join(str(part) for part in err["loc"]) or "<root>"
        lines.append(f"  - {location}: {err['msg']}")
    return "\n".join(lines)
