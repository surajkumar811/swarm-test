"""Base class for all swarm chaos attacks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from swarm_test.core.models import TestResult

if TYPE_CHECKING:
    from swarm_test.core.graph import SwarmGraph


class BaseAttack(ABC):
    """
    Abstract base class that all attack modules must implement.

    Subclasses should override ``name``, ``description``, and ``run()``.
    """

    name: str = "base_attack"
    description: str = "Base chaos attack."

    @abstractmethod
    def run(self, graph: SwarmGraph) -> TestResult:
        """
        Execute the attack against the provided SwarmGraph.

        Args:
            graph: A ``SwarmGraph`` instance containing agent nodes and events.

        Returns:
            A ``TestResult`` with findings, metrics, and status.
        """
        ...

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r})"
