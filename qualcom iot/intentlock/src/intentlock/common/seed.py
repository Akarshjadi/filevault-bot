"""Deterministic randomness.

Every stochastic step in IntentLock (weight init, augmentation, simulated
sensor noise, dataset shuffling) draws from a seeded generator obtained here, so
that "run it twice, get the same numbers" is a property of the system rather
than of careful manual seeding.
"""

from __future__ import annotations

import os
import random

import numpy as np

# Chosen arbitrarily but fixed forever: changing it invalidates every published
# experiment result, which is exactly why it is a named constant.
DEFAULT_SEED = 1337


def resolve_seed(seed: int | None = None) -> int:
    """Return `seed`, or the value of INTENTLOCK_SEED, or the default.

    The environment variable exists so that CI can sweep seeds without editing
    code.
    """
    if seed is not None:
        return int(seed)
    raw = os.environ.get("INTENTLOCK_SEED")
    if raw is None or raw.strip() == "":
        return DEFAULT_SEED
    try:
        return int(raw)
    except ValueError:
        # A malformed env var must not silently change experiment results.
        return DEFAULT_SEED


def seed_everything(seed: int | None = None) -> int:
    """Seed Python's `random` and NumPy. Returns the seed actually used.

    Torch is seeded only if it is importable, so the core pipeline keeps working
    without the training extra installed.
    """
    resolved = resolve_seed(seed)
    random.seed(resolved)
    np.random.seed(resolved)
    os.environ["PYTHONHASHSEED"] = str(resolved)

    try:  # pragma: no cover - depends on optional dependency
        import torch

        torch.manual_seed(resolved)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(resolved)
    except ImportError:
        pass

    return resolved


def rng(seed: int | None = None) -> np.random.Generator:
    """A NumPy generator for call sites that want isolation from global state."""
    return np.random.default_rng(resolve_seed(seed))
