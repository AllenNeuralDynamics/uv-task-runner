from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Callable, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from uv_task_runner import utils

logger = logging.getLogger(__name__)


@runtime_checkable
class OnTaskStart(Protocol):
    def __call__(self, task_path: str, pid: int) -> None: ...  # pragma: no cover


@runtime_checkable
class OnTaskEnd(Protocol):
    def __call__(self, task_path: str, result: TaskResult) -> None: ...  # pragma: no cover


class TaskConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_path: str
    task_args: list[str] = Field(default_factory=list)
    uv_run_args: list[str] = Field(default_factory=lambda: ["--quiet", "--script"])
    wait: bool = True
    # Hooks below are Python-only (not settable via TOML/CLI), and run in parent environment.
    # Called synchronously; keep them fast. For slow operations open a background thread inside the hook itself.
    on_task_start: OnTaskStart | list[OnTaskStart] | None = None
    on_task_end: OnTaskEnd | list[OnTaskEnd] | None = None


@dataclass(frozen=True)
class TaskResult:
    task_path: str
    exit_code: int | None  # None if wait=False
    success: bool
    duration_seconds: float
    stdout: str  # Empty string if wait=False
    stderr: str  # Empty string if wait=False
    pid: int


@dataclass
class _TaskHandle:
    """Internal: bundles a running process with its output-capture state."""

    process: subprocess.Popen
    stdout_thread: threading.Thread
    stderr_thread: threading.Thread
    stdout_capture: list[str]
    stderr_capture: list[str]
    task_path: str
    start_time: float


def _pipe_to_log(
    stream: IO[str],
    log_fn: Callable[[str], None],
    prefix: str,
    buffer_output: bool = True,
    capture: list[str] | None = None,  # mutated in-place
) -> None:
    """Read stream, send to logger, and optionally accumulate into capture list."""
    if buffer_output:
        content = stream.read()
        if content.strip():
            log_fn(f"{prefix}{content.rstrip()}")
        if capture is not None:
            capture.append(content)
    else:
        lines: list[str] = []
        for line in stream:
            log_fn(f"{prefix}{line.rstrip()}")
            lines.append(line)
        if capture is not None:
            capture.append("".join(lines))


def _terminate_tree(proc: subprocess.Popen) -> None:
    """Terminate a process and all its children."""
    if sys.platform.startswith("win"):
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass


def _build_args(task_config: TaskConfig) -> list[str]:
    if any(c in task_config.uv_run_args for c in ("--help", "-h")):
        raise ValueError("uv_run_args cannot contain --help or -h")
    if any(c in task_config.uv_run_args for c in ("--python", "-p")):
        if "--no-project" not in task_config.uv_run_args:
            logger.warning(
                f"Detected --python without --no-project in uv_run_args for task {task_config.task_path}. "
                "This may cause unexpected behavior if the task's Python version conflicts with the parent process's '.python-version' file. "
            )
    return ["uv", "run"] + task_config.uv_run_args + [task_config.task_path] + task_config.task_args


def dry_run_task(task_config: TaskConfig) -> TaskResult:
    """Log the command that would run without executing it. Returns a synthetic TaskResult."""
    logger.info("DRY RUN: would run: %s", " ".join(_build_args(task_config)))
    return TaskResult(
        task_path=task_config.task_path,
        exit_code=0,
        success=True,
        duration_seconds=0.0,
        stdout="",
        stderr="",
        pid=0,
    )


def run_task(
    task_config: TaskConfig,
    popen_kwargs: dict[str, Any] | None = None,
    log_multiline: bool = False,
) -> _TaskHandle:
    """Spawn a uv run subprocess for task_config. Fires on_task_start hooks.

    Returns a _TaskHandle. Call pipeline._collect_result() to wait and get TaskResult.
    """
    task_path = task_config.task_path
    args = _build_args(task_config)
    kwargs = dict(popen_kwargs or {})
    if sys.platform.startswith("win"):
        kwargs.setdefault("start_new_session", True)
    logger.debug("uv_run_args=%r task_args=%r popen_kwargs=%r", task_config.uv_run_args, task_config.task_args, kwargs)
    logger.info("Running command: %s", " ".join(args))
    process = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs
    )
    start_time = time.monotonic()
    prefix = f"[{Path(task_path).name}:{process.pid}] "
    logger.debug("Spawned pid=%s for %r", process.pid, task_path)

    stdout_capture: list[str] = []
    stderr_capture: list[str] = []
    threads: list[threading.Thread] = []
    for stream, capture in (
        (process.stdout, stdout_capture),
        (process.stderr, stderr_capture),
    ):
        t = threading.Thread(
            target=_pipe_to_log,
            kwargs={
                "stream": stream,
                "log_fn": logger.info,
                "prefix": prefix,
                "buffer_output": log_multiline,
                "capture": capture,
            },
            daemon=True,
        )
        t.start()
        threads.append(t)

    if task_config.on_task_start:
        logger.debug("Calling on_task_start hooks for %r with pid=%s", task_path, process.pid)
        utils._call_hooks(task_config.on_task_start, task_path, process.pid)
        logger.debug("on_task_start hooks complete for %r", task_path)

    return _TaskHandle(
        process=process,
        stdout_thread=threads[0],
        stderr_thread=threads[1],
        stdout_capture=stdout_capture,
        stderr_capture=stderr_capture,
        task_path=task_path,
        start_time=start_time,
    )


def _collect_result(handle: _TaskHandle, wait: bool) -> TaskResult:
    """Wait for a running task (if wait=True) and return its TaskResult."""
    if wait:
        logger.debug("Waiting for pid=%s (%r) to exit", handle.process.pid, handle.task_path)
        handle.process.wait()
        handle.stdout_thread.join()
        handle.stderr_thread.join()
        logger.debug("pid=%s exited with code %s", handle.process.pid, handle.process.returncode)
    return TaskResult(
        task_path=handle.task_path,
        exit_code=handle.process.returncode,
        success=handle.process.returncode == 0,
        duration_seconds=time.monotonic() - handle.start_time,
        stdout=handle.stdout_capture[0] if handle.stdout_capture else "",
        stderr=handle.stderr_capture[0] if handle.stderr_capture else "",
        pid=handle.process.pid,
    )
