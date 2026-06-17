"""Plugin registry — discover, register, and run swarm-test plugins."""

from __future__ import annotations

import inspect
import logging
import time
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any

from swarm_test.plugins.base import BasePlugin, PluginResult

if TYPE_CHECKING:
    from swarm_test.config import SwarmConfig
    from swarm_test.core.graph import SwarmGraph
    from swarm_test.core.models import AgentNode, InteractionEvent

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "swarm_test.plugins"

# Built-in test names — plugin names must not collide with these.
BUILTIN_TEST_NAMES: frozenset[str] = frozenset(
    {
        "cascade",
        "cascade_failure",
        "context_leakage",
        "intent_drift",
        "collusion",
        "collusion_detection",
        "blast_radius",
        "timeout",
        "timeout_resilience",
        "sensitive_data",
        "contract_violation",
    }
)


class PluginRegistry:
    """Holds registered swarm-test plugins and runs them against a graph."""

    def __init__(self) -> None:
        self._plugins: dict[str, BasePlugin] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, plugin: BasePlugin) -> None:
        """Register a plugin instance.

        Raises:
            ValueError: If the plugin's name collides with a built-in test
                name, is empty, or is already registered.
        """
        if not isinstance(plugin, BasePlugin):
            raise TypeError(
                f"register() expected a BasePlugin instance, got {type(plugin).__name__}"
            )
        name = plugin.name
        if not name:
            raise ValueError("Plugin must have a non-empty 'name' attribute")
        if name in BUILTIN_TEST_NAMES:
            raise ValueError(
                f"Plugin name '{name}' conflicts with a built-in swarm-test "
                f"test. Pick a different name."
            )
        if name in self._plugins:
            raise ValueError(f"Plugin '{name}' is already registered")
        self._plugins[name] = plugin
        logger.debug("Registered plugin: %s (v%s)", name, plugin.version)

    def unregister(self, name: str) -> None:
        """Remove a plugin from the registry. No-op if absent."""
        self._plugins.pop(name, None)

    def get(self, name: str) -> BasePlugin | None:
        """Return the plugin with the given name, or ``None``."""
        return self._plugins.get(name)

    def list_plugins(self) -> list[dict[str, str]]:
        """Return a list of dicts describing each registered plugin."""
        return [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "author": p.author,
            }
            for p in self._plugins.values()
        ]

    @property
    def plugins(self) -> dict[str, BasePlugin]:
        return dict(self._plugins)

    def __len__(self) -> int:
        return len(self._plugins)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._plugins

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self) -> None:
        """Discover plugins via the ``swarm_test.plugins`` entry-point group.

        Each entry point should resolve to a ``BasePlugin`` subclass. The
        class is instantiated and registered. Errors per-plugin are logged
        and never propagate.
        """
        try:
            eps = entry_points(group=ENTRY_POINT_GROUP)
        except TypeError:
            # Older Python (<3.10) returns a dict-like SelectableGroups
            eps = entry_points().get(ENTRY_POINT_GROUP, [])  # type: ignore[assignment]

        for ep in eps:
            try:
                obj: Any = ep.load()
            except Exception as exc:
                logger.warning("Failed to load plugin entry-point %s: %s", ep.name, exc)
                continue

            plugin = _instantiate(obj)
            if plugin is None:
                logger.warning(
                    "Plugin entry-point %s did not resolve to a BasePlugin "
                    "subclass or instance (got %r)",
                    ep.name,
                    obj,
                )
                continue

            try:
                self.register(plugin)
                logger.info(
                    "Discovered plugin: %s (v%s) — %s",
                    plugin.name,
                    plugin.version,
                    plugin.description,
                )
            except ValueError as exc:
                logger.warning("Skipping plugin %s: %s", ep.name, exc)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run_all(
        self,
        graph: SwarmGraph,
        agents: list[AgentNode],
        edges: list[InteractionEvent],
        config: SwarmConfig | None = None,
    ) -> list[PluginResult]:
        """Run every registered plugin and return their results.

        Exceptions raised by a single plugin are caught and turned into an
        ``error``-status result so one bad plugin can't crash the run.
        """
        results: list[PluginResult] = []
        # Respect config-driven enable/disable filtering for plugin names too
        active = None
        if config is not None:
            try:
                base = (
                    set(config.enabled_tests)
                    if getattr(config, "enabled_tests", None) is not None
                    else None
                )
                disabled = set(getattr(config, "disabled_tests", []) or [])
                active = (base, disabled)
            except Exception:
                active = None

        for plugin in self._plugins.values():
            if active is not None:
                base, disabled = active
                if base is not None and plugin.name not in base:
                    logger.debug("Plugin %s skipped (not in enabled_tests)", plugin.name)
                    continue
                if plugin.name in disabled:
                    logger.debug("Plugin %s skipped (in disabled_tests)", plugin.name)
                    continue

            start = time.perf_counter()
            try:
                result = plugin.run(graph, agents, edges, config)
                if not isinstance(result, PluginResult):
                    raise TypeError(
                        f"Plugin {plugin.name}.run() must return a PluginResult, "
                        f"got {type(result).__name__}"
                    )
                if result.duration_ms <= 0.0:
                    result.duration_ms = (time.perf_counter() - start) * 1000
                results.append(result)
            except Exception as exc:
                logger.exception("Plugin %s raised an exception", plugin.name)
                results.append(
                    PluginResult(
                        test_name=plugin.name,
                        status="error",
                        score=0.0,
                        findings=[],
                        duration_ms=(time.perf_counter() - start) * 1000,
                    )
                )
        return results


def _instantiate(obj: Any) -> BasePlugin | None:
    """Coerce an entry-point payload into a BasePlugin instance."""
    if isinstance(obj, BasePlugin):
        return obj
    if inspect.isclass(obj) and issubclass(obj, BasePlugin):
        try:
            return obj()  # type: ignore[abstract]
        except Exception as exc:
            logger.warning("Failed to instantiate plugin class %s: %s", obj.__name__, exc)
            return None
    return None


# ----------------------------------------------------------------------
# Convenience
# ----------------------------------------------------------------------


def discover_plugins() -> PluginRegistry:
    """Build a new ``PluginRegistry`` and populate it via entry-point discovery."""
    reg = PluginRegistry()
    reg.discover()
    return reg
