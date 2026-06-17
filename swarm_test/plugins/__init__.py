"""Plugin system for swarm-test.

Third-party developers can ship custom reliability tests as installable
packages that swarm-test discovers via the ``swarm_test.plugins`` entry
point group.
"""

from __future__ import annotations

from swarm_test.plugins.base import BasePlugin, PluginResult
from swarm_test.plugins.registry import PluginRegistry, discover_plugins

__all__ = [
    "BasePlugin",
    "PluginResult",
    "PluginRegistry",
    "discover_plugins",
]
