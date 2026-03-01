import logging
import os
import signal
import subprocess
import sys
import threading
import concurrent.futures as cf
from pathlib import Path
from typing import IO, Any, Callable

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    TomlConfigSettingsSource,
)

logger = logging.getLogger(__name__)


class PluginConfig(BaseModel):
    wait: bool = True
    plugin_args: list[str] = Field(default_factory=list)
    uv_args: list[str] = Field(default_factory=lambda: ["--quiet"])


class Settings(BaseSettings):

    parallel: bool = True
    fail_fast: bool = True
    # Buffer subprocess output and emit as a single log message per stream.
    # Keeps multiline output (e.g. stack traces) together. Set to false for
    # line-by-line logging (harder to read, but better compatibility).
    log_multiline: bool = True
    plugin_paths: list[str] = Field(default_factory=list)
    plugins: dict[str, PluginConfig] = Field(default_factory=dict)

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
            dotenv_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls, "plugin_config.toml"),
        )


def _pipe_to_log(stream: IO[str], log_fn: Callable[[str], None], prefix: str, buffer_output: bool = True) -> None:
    if buffer_output:
        # Buffer all output and emit as a single log message so multiline content
        # (e.g. stack traces) isn't split across many log entries. Disable with
        # log_multiline = false in plugin_config.toml if you need real-time output.
        content = stream.read()
        if content.strip():
            log_fn(f"{prefix}{content.rstrip()}")
    else:
        for line in stream:
            log_fn(f"{prefix}{line.rstrip()}")


def _terminate_tree(proc: subprocess.Popen) -> None:
    """Terminate a process and all its children."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass


def run_plugin(
    plugin_path: str,
    plugin_args: list[str] | None = None,
    uv_run_args: list[str] | None = None,
    popen_kwargs: dict[str, Any] | None = None,
    log_multiline: bool = True,
):
    args = (
        ["uv", "run"]
        + (uv_run_args or [])
        + ["--script", plugin_path]
        + (plugin_args or [])
    )
    kwargs = dict(popen_kwargs or {})
    if sys.platform != "win32":
        kwargs.setdefault("start_new_session", True)
    logger.info(f"Running {plugin_path} with {plugin_args=}")
    process = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs
    )
    prefix = f"[{Path(plugin_path).name}:{process.pid}] "
    threads = []
    for stream in (process.stdout, process.stderr):
        t = threading.Thread(
            target=_pipe_to_log,
            kwargs={"stream": stream, "log_fn": logger.info, "prefix": prefix, "buffer_output": log_multiline},
            daemon=True,
        )
        t.start()
        threads.append(t)
    return process, *threads


def main():
    settings = Settings()
    plugin_paths = settings.plugin_paths
    logger.info(f"Running {len(plugin_paths)} plugin(s).")

    popen_kwargs = {}
    plugin_path_to_proc: dict[str, subprocess.Popen] = {}

    def _run_and_wait(plugin_path: str) -> int | None:
        cfg = settings.plugins.get(plugin_path, PluginConfig())
        process, stdout_t, stderr_t = run_plugin(
            plugin_path,
            plugin_args=cfg.plugin_args,
            uv_run_args=cfg.uv_args,
            popen_kwargs=popen_kwargs,
            log_multiline=settings.log_multiline,
        )
        plugin_path_to_proc[plugin_path] = process
        if cfg.wait:
            process.wait()
            stdout_t.join()
            stderr_t.join()
        rc: int | None = process.returncode # None if cfg.wait is False
        return rc

    if settings.parallel:
        with cf.ThreadPoolExecutor() as executor:
            future_to_plugin_path = {
                executor.submit(_run_and_wait, p): p for p in plugin_paths
            }
            for future in cf.as_completed(future_to_plugin_path):
                rc = future.result()
                plugin_path = future_to_plugin_path[future]
                if rc != 0:
                    logger.error(f"{plugin_path} failed with return code {rc}")
                    if settings.fail_fast:
                        logger.warning(
                            "Fail fast enabled: terminating any plugins still running."
                        )
                        for plugin_path, proc in plugin_path_to_proc.items():
                            if proc.poll() is None:
                                logger.warning(
                                    f"Terminating {plugin_path} with PID {proc.pid}"
                                )
                                _terminate_tree(proc)
                        executor.shutdown(wait=False, cancel_futures=True)
                        break
                else:
                    logger.info(f"{plugin_path} completed successfully.")
    else:
        for plugin_path in plugin_paths:
            rc = _run_and_wait(plugin_path)
            if rc != 0:
                logger.error(f"{plugin_path} failed with return code {rc}")
                if settings.fail_fast:
                    logger.warning("Fail fast enabled, exiting.")
                    break
            elif rc is None:
                logger.info(f"{plugin_path} is running: not waiting for it to finish.")
            else:
                logger.info(f"{plugin_path} completed successfully.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
    )
    main()
