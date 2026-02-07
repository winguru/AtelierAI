"""Shim module to load config.example.py as a proper module."""

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path


_config_path = Path(__file__).with_name("config.example.py")
_loader = SourceFileLoader("config_example", str(_config_path))
_spec = spec_from_loader(_loader.name, _loader)
if _spec is None:
    raise RuntimeError(f"Failed to create spec for {_config_path}")
_module = module_from_spec(_spec)
_loader.exec_module(_module)

globals().update(
    {
        name: value
        for name, value in _module.__dict__.items()
        if not name.startswith("__")
    }
)

__all__ = [name for name in globals().keys() if not name.startswith("__")]  # type: ignore[assignment]
