"""Abstract base class and result model for swarm-test plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from swarm_test.core.models import Finding

if TYPE_CHECKING:
    from swarm_test.config import SwarmConfig
    from swarm_test.core.graph import SwarmGraph
    from swarm_test.core.models import AgentNode, InteractionEvent


class PluginResult(BaseModel):
    """Result of running a single plugin."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    test_name: str
    status: str = "passed"
    score: float = 100.0
    findings: list[Finding] = Field(default_factory=list)
    duration_ms: float = 0.0


class BasePlugin(ABC):
    """Abstract base class for swarm-test plugins.

    Subclasses must set ``name``, ``version``, ``description`` and implement
    :meth:`run`. ``author`` is optional.
    """

    name: str = ""
    version: str = "0.0.0"
    description: str = ""
    author: str = ""

    @abstractmethod
    def run(
        self,
        graph: SwarmGraph,
        agents: list[AgentNode],
        edges: list[InteractionEvent],
        config: SwarmConfig | None,
    ) -> PluginResult:
        """Execute the plugin and return a :class:`PluginResult`."""
        ...

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r}, version={self.version!r})"

    def info(self) -> dict[str, Any]:
        """Return a dict describing this plugin."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
        }
