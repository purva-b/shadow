"""Shadow connector plugins. See ARCHITECTURE.md for the design."""
from __future__ import annotations

from .base import Acl, Connector, SourceRecord, SourceRef, WebhookSpec
from .registry import autoload, available, load, load_enabled, register

# import connector modules so their @register runs on package import
autoload()

__all__ = [
    "Acl", "Connector", "SourceRecord", "SourceRef", "WebhookSpec",
    "available", "load", "load_enabled", "register",
]
