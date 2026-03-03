# uv-task-runner

Run multiple Python scripts in parallel or in sequence, with per-script dependency and Python version isolation via [uv](https://docs.astral.sh/uv/).

Each script is invoked as `uv run <script>`, so scripts can declare their own dependencies and Python version using [PEP 723 inline metadata](https://peps.python.org/pep-0723/). No more shared mega-environments.

[![PyPI](https://img.shields.io/pypi/v/uv-task-runner.svg?label=PyPI&color=blue)](https://pypi.org/project/uv-task-runner/)
[![Python version](https://img.shields.io/pypi/pyversions/uv-task-runner)](https://pypi.org/project/uv-task-runner/)

[![ty](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ty/main/assets/badge/v0.json)](https://github.com/astral-sh/ty)
[![Coverage](https://img.shields.io/badge/coverage-98%25-green?logo=codecov)](https://app.codecov.io/github/AllenNeuralDynamics/uv-task-runner)
[![CI/CD](https://img.shields.io/github/actions/workflow/status/AllenNeuralDynamics/uv-task-runner/publish.yaml?label=CI/CD&logo=github)](https://github.com/AllenNeuralDynamics/uv-task-runner/actions/workflows/publish.yaml)
[![GitHub issues](https://img.shields.io/github/issues/AllenNeuralDynamics/uv-task-runner?logo=github)](https://github.com/AllenNeuralDynamics/uv-task-runner/issues)


---

## Requirements

- Python 3.8+
- `uv` on PATH. See https://docs.astral.sh/uv/getting-started/installation/

## Installation

Make available globally:
```bash
uv install uv-task-runner
```

Or run CLI tool in temporary environment:
```bash
uv run uv-task-runner
```

Or add library to Python project:
```bash
uv add uv-task-runner
```

---

## Usage

### CLI

Generate an annotated config file in the current directory:

```bash
uv run uv-task-runner --init              # writes uv_task_runner.toml
uv run uv-task-runner --init my_tasks.toml  # custom path
```

Or write it by hand. Minimal `uv_task_runner.toml`:

```toml
[[tasks]]
task_path = "scripts/preprocess.py"

[[tasks]]
task_path = "scripts/analyze.py"
task_args = ["--output", "results/"]
```

Then run:

```bash
uv run uv-task-runner
```

Use a different config file:

```bash
uv run uv-task-runner --config path/to/config.toml
```

Override settings at the command line (CLI args take precedence over TOML):

```bash
uv run uv-task-runner --parallel --fail-fast --log-level DEBUG
```

Tasks can also be passed directly via `--tasks` as a JSON array (the TOML config is recommended for anything beyond a quick one-off, as shell escaping is error-prone):

```bash
# Single task
uv run uv-task-runner --tasks "[{\"task_path\":\"scripts/my_script.py\"}]"

# Multiple tasks with args
uv run uv-task-runner --tasks "[{\"task_path\":\"scripts/a.py\"},{\"task_path\":\"scripts/b.py\",\"task_args\":[\"--verbose\"]}]"
```

Note: double quotes inside the JSON must be escaped with `\"`. All `TaskConfig` fields are supported.

### Example output

Given a `uv_task_runner.toml`:

```toml
# Tasks are executed in order below if parallel=false (default):
[[tasks]]
task_path = "examples/script_a.py"
task_args = ["--param1", "updated_value"]
wait = false # don't wait for script_a.py to finish before starting the next task

[[tasks]]
task_path = "https://gist.githubusercontent.com/TAJD/1d389deba4221343caef5155090674eb/raw/13984206c008fdb35d2d574fa76b682991f00a08/error_handling.py"

[[tasks]]
task_path = "examples/script_b.py"
# if script does not declare dependencies with PEP 723 metadata it's possible to customize uv run args:
uv_run_args = ["--python", "3.14", "--verbose", "--script", "--no-project"]

[[tasks]]
task_path = "examples/script_c.py"
```

Running `uv run uv-task-runner` produces:

```
2026-03-02 13:32:27 | INFO | Running 4 task(s).
2026-03-02 13:32:27 | INFO | Running command: uv run --quiet --script examples/script_a.py --param1 updated_value
2026-03-02 13:32:27 | INFO | examples/script_a.py is running: not waiting for it to finish.
2026-03-02 13:32:27 | INFO | Running command: uv run --quiet --script https://gist.githubusercontent.com/TAJD/1d389deba4221343caef5155090674eb/raw/13984206c008fdb35d2d574fa76b682991f00a08/error_handling.py
2026-03-02 13:32:27 | INFO | [error_handling.py:164824] Error: The divisor 'b' cannot be zero.
2026-03-02 13:32:27 | INFO | [error_handling.py:164824] Error: The divisor 'b' cannot be zero.
2026-03-02 13:32:27 | INFO | [error_handling.py:164824] Stack trace:
2026-03-02 13:32:27 | INFO | [error_handling.py:164824]   File "C:\Users\BEN~1.HAR\AppData\Local\Temp\error_handlingjKocFl.py", line 52, in <module>
2026-03-02 13:32:27 | INFO | [error_handling.py:164824]     simple_example()
2026-03-02 13:32:27 | INFO | [error_handling.py:164824]   File "C:\Users\BEN~1.HAR\AppData\Local\Temp\error_handlingjKocFl.py", line 47, in simple_example
2026-03-02 13:32:27 | INFO | [error_handling.py:164824]     result = divide_numbers_stacktrace(10, 0)
2026-03-02 13:32:27 | INFO | [error_handling.py:164824]   File "C:\Users\BEN~1.HAR\AppData\Local\Temp\error_handlingjKocFl.py", line 37, in divide_numbers_stacktrace
2026-03-02 13:32:27 | INFO | [error_handling.py:164824]     return nested_division()
2026-03-02 13:32:27 | INFO | [error_handling.py:164824]   File "C:\Users\BEN~1.HAR\AppData\Local\Temp\error_handlingjKocFl.py", line 34, in nested_division
2026-03-02 13:32:27 | INFO | [error_handling.py:164824]     stack_trace = ''.join(traceback.format_stack())
2026-03-02 13:32:27 | INFO | [error_handling.py:164824]
2026-03-02 13:32:27 | INFO | https://gist.githubusercontent.com/TAJD/1d389deba4221343caef5155090674eb/raw/13984206c008fdb35d2d574fa76b682991f00a08/error_handling.py completed successfully.
2026-03-02 13:32:27 | INFO | Running command: uv run --python 3.14 --verbose --script --no-project examples/script_b.py
2026-03-02 13:32:27 | INFO | [script_b.py:145032] DEBUG uv 0.10.7 (08ab1a344 2026-02-27)
2026-03-02 13:32:27 | INFO | [script_b.py:145032] DEBUG Found project root: `C:\Users\ben.hardcastle\github\uv-plugin-architecture`
2026-03-02 13:32:27 | INFO | [script_b.py:145032] DEBUG No workspace root found, using project root
2026-03-02 13:32:27 | INFO | [script_b.py:145032] DEBUG Ignoring discovered project due to `--no-project`
2026-03-02 13:32:27 | INFO | [script_b.py:145032] DEBUG No project found; searching for Python interpreter
2026-03-02 13:32:27 | INFO | [script_b.py:145032] DEBUG Using request connect timeout of 10s and read timeout of 30s
2026-03-02 13:32:27 | INFO | [script_b.py:145032] DEBUG Searching for Python 3.14 in virtual environments, managed installations, search path, or registry
2026-03-02 13:32:27 | INFO | [script_b.py:145032] DEBUG Found `cpython-3.13.1-windows-x86_64-none` at `C:\Users\ben.hardcastle\github\uv-plugin-architecture\.venv\Scripts\python.exe` (active virtual environment)
2026-03-02 13:32:27 | INFO | [script_b.py:145032] DEBUG Skipping interpreter at `.venv\Scripts\python.exe` from active virtual environment: does not satisfy request `3.14`
2026-03-02 13:32:27 | INFO | [script_b.py:145032] DEBUG Found `cpython-3.13.1-windows-x86_64-none` at `C:\Users\ben.hardcastle\github\uv-plugin-architecture\.venv\Scripts\python.exe` (virtual environment)
2026-03-02 13:32:27 | INFO | [script_b.py:145032] DEBUG Skipping interpreter at `.venv\Scripts\python.exe` from virtual environment: does not satisfy request `3.14`
2026-03-02 13:32:27 | INFO | [script_b.py:145032] DEBUG Searching for managed installations at `C:\Users\ben.hardcastle\cache\uv\python`
2026-03-02 13:32:27 | INFO | [script_b.py:145032] DEBUG Skipping managed installation `cpython-3.13.1-windows-x86_64-none`: does not satisfy `3.14`
2026-03-02 13:32:27 | INFO | [script_b.py:145032] DEBUG Found `cpython-3.13.1-windows-x86_64-none` at `C:\Users\ben.hardcastle\github\uv-plugin-architecture\.venv\Scripts\python.exe` (first executable in the search path)
2026-03-02 13:32:27 | INFO | [script_b.py:145032] DEBUG Skipping interpreter at `.venv\Scripts\python.exe` from first executable in the search path: does not satisfy request `3.14`
2026-03-02 13:32:27 | INFO | [script_b.py:145032] INFO Fetching requested Python...
2026-03-02 13:32:27 | INFO | [script_b.py:145032] DEBUG Downloading https://github.com/astral-sh/python-build-standalone/releases/download/20260211/cpython-3.14.3%2B20260211-x86_64-pc-windows-msvc-install_only_stripped.tar.gz
2026-03-02 13:32:27 | INFO | [script_b.py:145032] DEBUG Extracting cpython-3.14.3-20260211-x86_64-pc-windows-msvc-install_only_stripped.tar.gz to temporary location: C:\Users\ben.hardcastle\cache\uv\python\.temp\.tmpajp9EC
2026-03-02 13:32:27 | INFO | [script_b.py:145032] Downloading cpython-3.14.3-windows-x86_64-none (download) (21.3MiB)
2026-03-02 13:32:35 | INFO | [script_a.py:162304] script_a.py loaded polars version 1.38.1
2026-03-02 13:32:35 | INFO | [script_a.py:162304] script_a.py running on Python 3.11.9
2026-03-02 13:32:35 | INFO | [script_a.py:162304] script_a.py successfully received param1 from command line: updated_value       
2026-03-02 13:32:35 | INFO | [script_a.py:162304] script_a.py finished
2026-03-02 13:32:38 | INFO | [script_b.py:145032]  Downloaded cpython-3.14.3-windows-x86_64-none (download)
2026-03-02 13:32:38 | INFO | [script_b.py:145032] DEBUG Moving C:\Users\ben.hardcastle\cache\uv\python\.temp\.tmpajp9EC\python to C:\Users\ben.hardcastle\cache\uv\python\cpython-3.14.3-windows-x86_64-none
2026-03-02 13:32:38 | INFO | [script_b.py:145032] DEBUG Created link C:\Users\ben.hardcastle\cache\uv\python\cpython-3.14-windows-x86_64-none -> C:\Users\ben.hardcastle\cache\uv\python\cpython-3.14.3-windows-x86_64-none
2026-03-02 13:32:39 | INFO | [script_b.py:145032] DEBUG Using Python 3.14.3 interpreter at: C:\Users\ben.hardcastle\cache\uv\python\cpython-3.14.3-windows-x86_64-none\python.exe
2026-03-02 13:32:39 | INFO | [script_b.py:145032] DEBUG Running `python examples/script_b.py`
2026-03-02 13:32:39 | INFO | [script_b.py:145032] script_b.py loaded on Python 3.14.3
2026-03-02 13:32:39 | INFO | [script_b.py:145032] Traceback (most recent call last):
2026-03-02 13:32:39 | INFO | [script_b.py:145032]   File "C:\Users\ben.hardcastle\github\uv-plugin-architecture\scripts\script_b.py", line 5, in <module>
2026-03-02 13:32:39 | INFO | [script_b.py:145032]     raise ValueError(f"Simulated error in {Path(__file__).name}")
2026-03-02 13:32:39 | INFO | [script_b.py:145032] ValueError: Simulated error in script_b.py
2026-03-02 13:32:39 | INFO | [script_b.py:145032] DEBUG Command exited with code: 1
2026-03-02 13:32:39 | ERROR | examples/script_b.py failed with exit code 1
2026-03-02 13:32:39 | INFO | Running command: uv run --quiet --script examples/script_c.py
2026-03-02 13:32:44 | INFO | [script_c.py:36208] script_c.py loaded on Python 3.13.1
2026-03-02 13:32:44 | INFO | [script_c.py:36208] script_c.py finished
2026-03-02 13:32:44 | INFO | examples/script_c.py completed successfully.
```

Key things to note:
- `script_a.py` has `wait=false`: it starts immediately and execution continues without waiting for it. With `log_multiline=true`, its output is buffered until exit — but since the parent exits first, **no output is captured** and a warning is emitted.
- `error_handling.py` is fetched from a URL. Its multiline stderr (a stack trace) is emitted as a single log block because `log_multiline=true`.
- `script_b.py` exits non-zero, logged at `ERROR` level.
- `script_c.py` mixes stdout lines directly into the log stream (lines without the `[name:pid]` prefix come from the script's own `print()` calls).

### Python API

```python
from uv_task_runner import run_tasks, TaskConfig

results = run_tasks([
    TaskConfig(task_path="scripts/preprocess.py"),
    TaskConfig(task_path="scripts/analyze.py", task_args=["--output", "results/"]),
])

for r in results.task_results:
    print(r.task_path, r.exit_code, r.duration_seconds)
```

For more control, use `Pipeline` directly:

```python
from uv_task_runner import Pipeline, Settings, TaskConfig

pipeline = Pipeline(
    tasks=[
        TaskConfig(task_path="scripts/a.py"),
        TaskConfig(task_path="scripts/b.py"),
    ],
    parallel=True,
    fail_fast=True,
)
result = pipeline.run()
print(result.aborted, result.aborted_by)
```

---

## Configuration reference

### Global settings applied to `Pipeline`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `parallel` | bool | `false` | Run all tasks concurrently. `false` runs them one at a time in listed order. |
| `fail_fast` | bool | `false` | Terminate remaining tasks on the first failure. |
| `dry_run` | bool | `false` | Print what would run without executing any tasks. |
| `log_level` | string or int | `"INFO"` | Standard logging level names: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`, case-insensitive. |
| `log_multiline` | bool | `false` | Buffer each task's stdout/stderr and emit as a single log message per stream. Default `false` logs lines as they arrive. With `parallel=true`, interleaved output from concurrent tasks can make multiline output (e.g. stack traces) hard to read: set `log_multiline=true` to keep them together at the cost of buffering until process exit. Has no readability effect when `parallel=false`. |

### Per-task settings applied to `TaskConfig`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `task_path` | string | required | Path to the script, relative to the config file. Can also be a URL (e.g. a GitHub raw file). |
| `task_args` | list[string] | `[]` | Arguments passed to the script (`sys.argv`). |
| `uv_run_args` | list[string] | `["--quiet", "--script"]` | Arguments passed to `uv run` before the script path. |
| `wait` | bool | `true` | Wait for the task to finish before proceeding. `false` spawns the process and continues immediately. |

### Callback hooks (Python API only)

`TaskConfig` accepts Python callables for `on_task_start` and `on_task_end`. These are not settable via TOML.

```python
def on_start(task_path: str, pid: int) -> None:
    print(f"Started {task_path} (PID {pid})")

def on_end(task_path: str, result: TaskResult) -> None:
    print(f"{task_path} exited {result.exit_code} after {result.duration_seconds:.1f}s")

TaskConfig(
    task_path="scripts/a.py",
    on_task_start=on_start,
    on_task_end=on_end,
)
```

`Pipeline` accepts `on_pipeline_start` and `on_pipeline_end` in the same way.

Hooks run synchronously in the parent process. Keep them fast; for slow operations, open a background thread inside the hook.

---

## How scripts are run

Each task is executed as:

```
uv run [uv_run_args] [task_path] [task_args]
```

Scripts can declare their own Python version and dependencies using PEP 723 metadata:

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["polars>=0.20", "requests"]
# ///

import polars as pl
# ...
```

`uv` resolves and installs dependencies for each script independently. Scripts with different Python versions or incompatible dependency sets run without conflict.

---

## Return values

`Pipeline.run()` and `run_tasks()` return a `PipelineResult`:

```python
@dataclass
class PipelineResult:
    task_results: list[TaskResult]
    aborted: bool           # True if fail_fast triggered early termination
    aborted_by: str | None  # task_path that caused the abort, or None
```

Each `TaskResult`:

```python
@dataclass
class TaskResult:
    task_path: str
    exit_code: int | None   # None if wait=False
    success: bool
    duration_seconds: float
    stdout: str             # Empty string if wait=False
    stderr: str             # Empty string if wait=False
    pid: int
```

The CLI entry point always exits with code 0. Inspect `PipelineResult` when using the Python API.

---

## Limitations

**No DAG-style task dependencies.** Sequential pipelines with `fail_fast=True` naturally express
linear chains ("run B only after A succeeds"). What is not supported is graph-style dependencies,
e.g. "run C after both A and B succeed" when A and B run in parallel. To implement phased parallel
execution, call `run_tasks()` or `Pipeline.run()` multiple times in sequence, or consider Snakemake,
Airflow, Prefect, or similar tools.

**`log_multiline=true` always buffers until process exit.** Output is held in a `stream.read()` call that blocks until the subprocess closes stdout. For normal `wait=true` tasks this means output appears as a single block at the end rather than in real-time. For `wait=false` (fire-and-forget) tasks it is worse: if the parent exits before the subprocess finishes, the daemon thread is killed and **no output is logged at all**. The default (`log_multiline=false`) logs lines as they arrive, which avoids both problems at the cost of interleaved output from concurrent tasks.

`TaskResult.stdout`/`stderr` are always empty for `wait=false` tasks regardless of buffering mode, because the capture threads are not joined before the result is collected. The subprocess will be reported as still running on pipeline exit.

**No per-task timeouts.** A hung task will block indefinitely. As a workaround, wrap the script invocation with `timeout` (Unix) or a similar mechanism.

**No task naming.** Tasks are identified by `task_path` in results and log output. Long paths or URLs can make logs harder to read.
