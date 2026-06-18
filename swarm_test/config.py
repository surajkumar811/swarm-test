"""Configuration loading and merging for swarm-test.

Supports loading a YAML config file (``.swarmtest.yml`` / ``.swarmtest.yaml`` /
``swarmtest.yml``) or a ``[tool.swarmtest]`` section in ``pyproject.toml``.
CLI flags always win over config-file values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover - py3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef,import-not-found]

import yaml

VALID_SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low", "info", "none")
VALID_OUTPUT_FORMATS: tuple[str, ...] = (
    "console",
    "json",
    "markdown",
    "html",
    "mermaid",
    "dot",
    "png",
)
VALID_OUTPUT_VERBOSITY: tuple[str, ...] = ("quiet", "normal", "verbose")
VALID_TEST_NAMES: tuple[str, ...] = (
    "cascade",
    "context_leakage",
    "intent_drift",
    "collusion",
    "blast_radius",
    "timeout",
    "sensitive_data",
    "contract_violation",
)

CONFIG_FILENAMES: tuple[str, ...] = (".swarmtest.yml", ".swarmtest.yaml", "swarmtest.yml")

# Map config short-names → SwarmProbe attack class names
TEST_NAME_TO_ATTACK: dict[str, str] = {
    "cascade": "CascadeFailureAttack",
    "context_leakage": "ContextLeakageAttack",
    "intent_drift": "IntentDriftAttack",
    "collusion": "CollusionDetectionAttack",
    "blast_radius": "BlastRadiusAttack",
    "timeout": "TimeoutResilienceAttack",
    "sensitive_data": "ContextLeakageAttack",  # sensitive_data is a sub-feature of context_leakage
}


class SwarmConfig(BaseModel):
    """User-facing configuration for a swarm-test run."""

    model_config = ConfigDict(extra="forbid")

    fail_on_severity: str = Field(
        default="critical",
        description="Minimum severity that causes exit code 1.",
    )
    max_blast_radius: float = Field(
        default=1.0,
        description="Blast-radius threshold (0.0-1.0). Findings above this trigger failure.",
    )
    enabled_tests: list[str] | None = Field(
        default=None,
        description="Whitelist of test names. None means all tests run.",
    )
    disabled_tests: list[str] = Field(
        default_factory=list,
        description="Blacklist of test names. Applied after enabled_tests.",
    )
    sensitive_patterns: list[str] = Field(
        default_factory=list,
        description="Extra regex patterns for sensitive data detection.",
    )
    output_format: str = Field(
        default="console",
        description="Output format: console, json, markdown, html.",
    )
    output_path: str | None = Field(
        default=None,
        description="File path for json/markdown/html output.",
    )
    quick_scan: bool = Field(
        default=False,
        description="Run in quick scan mode.",
    )
    timeout_seconds: float = Field(
        default=30.0,
        description="Timeout for the timeout-resilience test (seconds).",
    )
    strict: bool = Field(
        default=False,
        description="Treat warnings as failures.",
    )
    contracts_path: str | None = Field(
        default=None,
        description="Path to a YAML file of agent output contracts.",
    )
    output_verbosity: str = Field(
        default="normal",
        description="Console verbosity: quiet, normal, or verbose.",
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("fail_on_severity")
    @classmethod
    def _check_severity(cls, v: str) -> str:
        v_lower = v.lower()
        if v_lower not in VALID_SEVERITIES:
            raise ValueError(
                f"fail_on_severity must be one of: " f"{', '.join(VALID_SEVERITIES)} — got '{v}'"
            )
        return v_lower

    @field_validator("max_blast_radius")
    @classmethod
    def _check_blast(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"max_blast_radius must be between 0.0 and 1.0 — got {v}")
        return float(v)

    @field_validator("output_format")
    @classmethod
    def _check_output_format(cls, v: str) -> str:
        v_lower = v.lower()
        if v_lower not in VALID_OUTPUT_FORMATS:
            raise ValueError(
                f"output_format must be one of: " f"{', '.join(VALID_OUTPUT_FORMATS)} — got '{v}'"
            )
        return v_lower

    @field_validator("output_verbosity")
    @classmethod
    def _check_output_verbosity(cls, v: str) -> str:
        v_lower = v.lower()
        if v_lower not in VALID_OUTPUT_VERBOSITY:
            raise ValueError(
                f"output_verbosity must be one of: "
                f"{', '.join(VALID_OUTPUT_VERBOSITY)} — got '{v}'"
            )
        return v_lower

    @field_validator("enabled_tests")
    @classmethod
    def _check_enabled(cls, v: list[str] | None) -> list[str] | None:
        # Names outside VALID_TEST_NAMES are assumed to refer to third-party
        # plugins and are passed through without error.
        return v

    @field_validator("disabled_tests")
    @classmethod
    def _check_disabled(cls, v: list[str]) -> list[str]:
        # Plugin names are allowed alongside built-in test names.
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def _check_timeout(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"timeout_seconds must be > 0 — got {v}")
        return float(v)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def active_test_names(self) -> set[str]:
        """Resolve which tests should actually run after enabled/disabled filtering."""
        base = set(self.enabled_tests) if self.enabled_tests is not None else set(VALID_TEST_NAMES)
        return base - set(self.disabled_tests)


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a YAML mapping at the top level.")
    return data


def _load_pyproject(path: Path) -> dict[str, Any] | None:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    tool = data.get("tool", {})
    section = tool.get("swarmtest")
    if not section:
        return None
    if not isinstance(section, dict):
        raise ValueError("[tool.swarmtest] in pyproject.toml must be a table.")
    return section


def _discover(cwd: Path) -> Path | None:
    for name in CONFIG_FILENAMES:
        p = cwd / name
        if p.is_file():
            return p
    pyproject = cwd / "pyproject.toml"
    if pyproject.is_file():
        try:
            section = _load_pyproject(pyproject)
        except Exception:
            return None
        if section is not None:
            return pyproject
    return None


def load_config(path: str | None = None) -> SwarmConfig:
    """Load a SwarmConfig from a YAML file, pyproject.toml, or fall back to defaults.

    Args:
        path: Explicit path to a config file. If ``None``, auto-discover in cwd.

    Returns:
        A validated SwarmConfig.

    Raises:
        FileNotFoundError: If ``path`` is provided but does not exist.
        ValueError: If the file is malformed or contains invalid values.
    """
    if path is not None:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Config file not found: {path}")
        data: dict[str, Any]
        if p.name == "pyproject.toml":
            section = _load_pyproject(p)
            data = section or {}
        else:
            data = _load_yaml(p)
        return SwarmConfig(**data)

    discovered = _discover(Path.cwd())
    if discovered is None:
        return SwarmConfig()

    if discovered.name == "pyproject.toml":
        section = _load_pyproject(discovered)
        return SwarmConfig(**(section or {}))
    return SwarmConfig(**_load_yaml(discovered))


def find_config_path(cwd: Path | None = None) -> Path | None:
    """Return the path of the auto-discovered config file, if any."""
    return _discover(cwd or Path.cwd())


# ----------------------------------------------------------------------
# CLI merging
# ----------------------------------------------------------------------


def merge_cli_args(config: SwarmConfig, cli_args: dict[str, Any]) -> SwarmConfig:
    """Return a new SwarmConfig with non-None CLI overrides applied.

    Only keys present in ``cli_args`` with a non-None value override the config.
    """
    overrides = {k: v for k, v in cli_args.items() if v is not None}
    if not overrides:
        return config
    merged = config.model_dump()
    merged.update(overrides)
    return SwarmConfig(**merged)
