# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "pydantic-settings>=2.13.1",
# ]
# ///
import concurrent.futures as cf
import logging
import os
import platform
import signal
import subprocess
import threading
from pathlib import Path
from typing import IO, Any, Callable

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    CliSettingsSource,
    PydanticBaseSettingsSource,
    TomlConfigSettingsSource,
)

logger = logging.getLogger(__name__)


class TaskConfig(BaseModel):
    wait: bool = True
    task_args: list[str] = Field(default_factory=list)
    uv_run_args: list[str] = Field(default_factory=lambda: ["--quiet", "--script"])


class Settings(BaseSettings):

    parallel: bool = True
    fail_fast: bool = True
    log_level: str | int = logging.INFO
    # Buffer subprocess output and emit as a single log message per stream.
    # Keeps multiline output (e.g. stack traces) together. Set to false for
    # line-by-line logging (harder to read, but better compatibility).
    log_multiline: bool = True
    task_paths: list[str] = Field(default_factory=list)
    task_configs: dict[str, TaskConfig] = Field(default_factory=dict)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            CliSettingsSource(settings_cls, cli_parse_args=True),
            dotenv_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls, "task_runner.toml"),
        )


def _pipe_to_log(
    stream: IO[str],
    log_fn: Callable[[str], None],
    prefix: str,
    buffer_output: bool = True,
) -> None:
    if buffer_output:
        # Buffer all output and emit as a single log message so multiline content
        # (e.g. stack traces) isn't split across many log entries. Disable with
        # log_multiline = false in task_config.toml if you need real-time output.
        content = stream.read()
        if content.strip():
            log_fn(f"{prefix}{content.rstrip()}")
    else:
        for line in stream:
            log_fn(f"{prefix}{line.rstrip()}")


def _terminate_tree(proc: subprocess.Popen) -> None:
    """Terminate a process and all its children."""
    if platform.system() == "Windows":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass


def run_task(
    task_path: str,
    task_args: list[str] | None = None,
    uv_run_args: list[str] | None = None,
    popen_kwargs: dict[str, Any] | None = None,
    log_multiline: bool = True,
) -> tuple[subprocess.Popen, threading.Thread, threading.Thread]:
    args = ["uv", "run"] + (uv_run_args or []) + [task_path] + (task_args or [])
    kwargs = dict(popen_kwargs or {})
    if platform.system() != "Windows":
        # Start the process in a new process group so we can terminate the whole tree if needed.
        kwargs.setdefault("start_new_session", True)
    logger.info(f"Running command: {' '.join(args)}")
    process = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs
    )
    prefix = f"[{Path(task_path).name}:{process.pid}] "
    threads: list[threading.Thread] = []
    for stream in (process.stdout, process.stderr):
        t = threading.Thread(
            target=_pipe_to_log,
            kwargs={
                "stream": stream,
                "log_fn": logger.info,
                "prefix": prefix,
                "buffer_output": log_multiline,
            },
            daemon=True,
        )
        t.start()
        threads.append(t)
    return process, threads[0], threads[1]


def main():
    settings = Settings()

    # start root logger
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S",

    )

    task_paths = settings.task_paths
    logger.info(f"Running {len(task_paths)} task(s).")

    popen_kwargs = {}
    task_path_to_proc: dict[str, subprocess.Popen] = {}

    def _helper(task_path: str) -> int | None:
        cfg = settings.task_configs.get(task_path, TaskConfig())
        process, stdout_t, stderr_t = run_task(
            task_path,
            task_args=cfg.task_args,
            uv_run_args=cfg.uv_run_args,
            popen_kwargs=popen_kwargs,
            log_multiline=settings.log_multiline,
        )
        task_path_to_proc[task_path] = process
        if cfg.wait:
            process.wait()
            stdout_t.join()
            stderr_t.join()
        rc: int | None = process.returncode  # None if cfg.wait is False
        return rc

    if settings.parallel:
        with cf.ThreadPoolExecutor() as executor:
            future_to_task_path = {executor.submit(_helper, p): p for p in task_paths}
            for future in cf.as_completed(future_to_task_path):
                exit_code = future.result()
                task_path = future_to_task_path[future]
                if exit_code != 0:
                    logger.error(f"{task_path} failed with return code {exit_code}")
                    if settings.fail_fast:
                        logger.warning(
                            "Fail fast enabled: terminating any tasks still running."
                        )
                        for task_path, proc in task_path_to_proc.items():
                            if proc.poll() is None:
                                logger.warning(
                                    f"Terminating {task_path} with PID {proc.pid}"
                                )
                                _terminate_tree(proc)
                        executor.shutdown(wait=False, cancel_futures=True)
                        break
                else:
                    logger.info(f"{task_path} completed successfully.")
    else:
        for task_path in task_paths:
            exit_code = _helper(task_path)
            if exit_code is None:
                logger.info(f"{task_path} is running: not waiting for it to finish.")
                continue
            elif exit_code != 0:
                logger.error(f"{task_path} failed with return code {exit_code}")
                if settings.fail_fast:
                    logger.warning("Fail fast enabled, exiting.")
                    break
            else:
                logger.info(f"{task_path} completed successfully.")


if __name__ == "__main__":
    main()
