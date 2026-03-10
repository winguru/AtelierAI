"""Unified configuration access for AtelierAI modules.

This module centralizes config imports so callers can consistently use
`atelierai.config` across local venv, VS Code dev containers, and Docker.
"""

from importlib import import_module
import os
from types import ModuleType


def _load_config_module() -> ModuleType:
    """Load the first available runtime config module."""
    module_names = [
        "backend.config",
        "app.backend.config",
        "config",
    ]
    for module_name in module_names:
        try:
            return import_module(module_name)
        except ModuleNotFoundError:
            continue
    raise ModuleNotFoundError(
        "Unable to load configuration module. Expected one of: "
        "backend.config, app.backend.config, config"
    )


_config = _load_config_module()

# Re-export public attributes from the loaded config module.
for _name in dir(_config):
    if _name.startswith("_"):
        continue
    globals()[_name] = getattr(_config, _name)

# Legacy fallback used by some scripts/tests.
if "MY_SESSION_COOKIE" not in globals():
    globals()["MY_SESSION_COOKIE"] = os.getenv("MY_SESSION_COOKIE", "")


__all__ = [name for name in globals() if not name.startswith("_")]
