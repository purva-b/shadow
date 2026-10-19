"""Connector registry: discover and load the connectors enabled for a workspace.

A connector registers itself with @register; the backend loads only those named
in config/workspace.yaml (enabled_connectors). Adding a source is 'write the
file, add one line of config' with no core change.
"""
from __future__ import annotations

import importlib
import pkgutil

from .base import Connector

_REGISTRY: dict[str, type] = {}


def register(cls: type) -> type:
    """Class decorator: register a Connector implementation by its .name."""
    name = getattr(cls, "name", None)
    if not name:
        raise ValueError(f"{cls.__name__} must set a class-level `name`")
    _REGISTRY[name] = cls
    return cls


def available() -> list[str]:
    """Names of every registered connector."""
    return sorted(_REGISTRY)


def load(name: str, **kwargs) -> Connector:
    """Instantiate one connector by name."""
    if name not in _REGISTRY:
        raise KeyError(f"unknown connector {name!r}; available: {available()}")
    return _REGISTRY[name](**kwargs)


def load_enabled(names: list[str], **kwargs) -> dict[str, Connector]:
    """Instantiate the connectors a workspace has enabled."""
    return {n: load(n, **kwargs) for n in names if n in _REGISTRY}


def autoload() -> None:
    """Import every *_connector.py in this package so their @register runs.
    A new connector file is discovered automatically — no edit here."""
    import connectors

    for mod in pkgutil.iter_modules(connectors.__path__):
        if mod.name.endswith("_connector"):
            importlib.import_module(f"connectors.{mod.name}")
