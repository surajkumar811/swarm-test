# swarm-test plugin template

Build a custom swarm-test reliability test in 5 minutes.

## What's in here

```
plugin_template/
├── pyproject.toml                       # package metadata + entry point
├── README.md                            # this file
└── swarm_test_plugin_example/
    ├── __init__.py                      # re-exports ExamplePlugin
    └── plugin.py                        # the plugin implementation
```

## How it works

swarm-test discovers plugins via the `swarm_test.plugins` Python
[entry-point group](https://packaging.python.org/en/latest/specifications/entry-points/).
The single line in `pyproject.toml` that wires it up:

```toml
[project.entry-points."swarm_test.plugins"]
example = "swarm_test_plugin_example:ExamplePlugin"
```

## Build your own in 5 minutes

1. **Copy this directory** to a new location and rename
   `swarm_test_plugin_example` to your own package name.
2. **Edit `plugin.py`** — subclass `BasePlugin`, set `name`, `version`,
   `description`, `author`, and implement `run(graph, agents, edges, config)`
   so it returns a `PluginResult`.
3. **Update `pyproject.toml`** — change `name`, the entry-point line, and
   the `[tool.hatch.build.targets.wheel] packages` list.
4. **Install it** in the same environment as swarm-test:

   ```bash
   pip install -e .
   ```

5. **Verify it loaded**:

   ```bash
   swarm-test plugins list
   swarm-test plugins info example
   ```

   The plugin will now run alongside every `swarm-test run`,
   `swarm-test probe`, and `swarm-test scan` invocation.

## API summary

```python
from swarm_test.plugins import BasePlugin, PluginResult
from swarm_test.core.models import Finding, Severity


class MyPlugin(BasePlugin):
    name = "my_test"
    version = "0.1.0"
    description = "Checks for X"
    author = "you"

    def run(self, graph, agents, edges, config) -> PluginResult:
        findings = [
            Finding(
                test_name=self.name,
                severity=Severity.HIGH,
                title="Something wrong",
                description="Explain what's wrong",
                remediation="Explain how to fix it",
                affected_agents=[],
            ),
        ]
        return PluginResult(
            test_name=self.name,
            status="failed" if findings else "passed",
            score=100.0 - 20 * len(findings),
            findings=findings,
            duration_ms=0.0,
        )
```

That's it — the plugin will appear in the same `swarm-test` report as the
built-in chaos tests.
