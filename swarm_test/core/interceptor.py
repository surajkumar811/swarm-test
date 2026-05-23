"""Agent method interceptor — monkey-patches agent instances to record events."""

from __future__ import annotations

import functools
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from swarm_test.core.models import EventType, InteractionEvent

logger = logging.getLogger(__name__)

# Patterns considered sensitive in agent payloads
_SENSITIVE_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(api[_-]?key|apikey|token|secret)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(bearer\s+[A-Za-z0-9\-._~+/]+=*)"),
    re.compile(r"(?i)(ssn|social.security)\s*[:=]\s*[\d\-]+"),
    re.compile(r"(?i)(credit.?card|cc.?number)\s*[:=]\s*[\d\s\-]+"),
    re.compile(r"\b\d{16}\b"),  # Raw 16-digit card numbers
    re.compile(r"(?i)(private.?key|-----BEGIN)"),
    re.compile(r"(?i)(aws.?access.?key|aws.?secret)"),
]


def check_sensitive_leakage(text: str) -> list[str]:
    """
    Scan text for patterns that indicate sensitive data leakage.
    Returns list of pattern descriptions that matched.
    """
    matches = []
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.search(text):
            matches.append(pattern.pattern)
    return matches


class AgentInterceptor:
    """
    Wraps agent callable methods to record interaction events.
    Supports CrewAI, LangChain, AutoGen, and generic callable agents.
    """

    def __init__(
        self,
        graph: Any,  # SwarmGraph
        source_agent_id: str,
        target_agent_id: str,
    ) -> None:
        self.graph = graph
        self.source_agent_id = source_agent_id
        self.target_agent_id = target_agent_id
        self._patched: list[tuple[Any, str, Callable]] = []

    def wrap(self, fn: Callable, event_type: EventType = EventType.AGENT_CALL) -> Callable:
        """Return a wrapped version of fn that records an interaction event."""

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            payload: dict[str, Any] = {}
            success = True
            error_msg: str | None = None

            # Capture input
            try:
                payload["args_repr"] = repr(args)[:500]
                payload["kwargs_keys"] = list(kwargs.keys())
            except Exception:
                pass

            try:
                result = fn(*args, **kwargs)
                # Capture output
                try:
                    result_repr = repr(result)[:1000]
                    payload["result_repr"] = result_repr
                    leaks = check_sensitive_leakage(result_repr)
                    if leaks:
                        payload["sensitive_patterns_detected"] = leaks
                except Exception:
                    pass
                return result
            except Exception as exc:
                success = False
                error_msg = str(exc)
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                event = InteractionEvent(
                    source_agent_id=self.source_agent_id,
                    target_agent_id=self.target_agent_id,
                    event_type=event_type,
                    payload=payload,
                    duration_ms=round(duration_ms, 3),
                    success=success,
                    error_message=error_msg,
                )
                self.graph.record_event(event)

        return wrapper

    def patch_method(self, obj: Any, method_name: str, event_type: EventType = EventType.AGENT_CALL) -> bool:
        """Monkey-patch a method on obj and record the original for restoration."""
        original = getattr(obj, method_name, None)
        if original is None or not callable(original):
            return False
        wrapped = self.wrap(original, event_type)
        try:
            setattr(obj, method_name, wrapped)
            self._patched.append((obj, method_name, original))
            logger.debug("Patched %s.%s", type(obj).__name__, method_name)
            return True
        except (AttributeError, TypeError):
            return False

    def restore_all(self) -> None:
        """Restore all monkey-patched methods."""
        for obj, method_name, original in self._patched:
            try:
                setattr(obj, method_name, original)
            except Exception as exc:
                logger.warning("Failed to restore %s: %s", method_name, exc)
        self._patched.clear()


class SwarmInterceptorRegistry:
    """
    Registry that manages interceptors for an entire swarm.
    Call attach() before running the swarm, detach() after.
    """

    def __init__(self, graph: Any) -> None:
        self.graph = graph
        self._interceptors: list[AgentInterceptor] = []
        self._active = False

    def create_interceptor(self, source_id: str, target_id: str) -> AgentInterceptor:
        interceptor = AgentInterceptor(self.graph, source_id, target_id)
        self._interceptors.append(interceptor)
        return interceptor

    def detach_all(self) -> None:
        for interceptor in self._interceptors:
            interceptor.restore_all()
        self._interceptors.clear()
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active
