import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    TomlConfigSettingsSource,
)

logger = logging.getLogger(__name__)


def _pipe_to_log(stream, log_fn, prefix: str) -> None:
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


class PluginConfig(BaseModel):
    wait: bool = True
    plugin_args: list[str] = Field(default_factory=list)
    uv_args: list[str] = Field(
        default_factory=lambda: ["--quiet"]
    )

class Settings(BaseSettings):

    parallel: bool = True
    fail_fast: bool = True
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
            env_settings,
            TomlConfigSettingsSource(settings_cls, "plugin_config.toml"),
        )


def run_plugin(
    plugin_path: str,
    plugin_args: list[str] | None = None,
    uv_run_args: list[str] | None = None,
    popen_kwargs: dict[str, Any] | None = None,
):
    logger.info(f"Running {plugin_path}")
    args = (
        ["uv", "run"]
        + (uv_run_args or [])
        + ["--script", plugin_path]
        + (plugin_args or [])
    )
    kwargs = {**(popen_kwargs or {})}
    if sys.platform != "win32":
        kwargs.setdefault("start_new_session", True)
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs)
    prefix = f"[{Path(plugin_path).name}] "
    stdout_t = threading.Thread(target=_pipe_to_log, args=(process.stdout, logger.info, prefix), daemon=True)
    stderr_t = threading.Thread(target=_pipe_to_log, args=(process.stderr, logger.info, prefix), daemon=True)
    stdout_t.start()
    stderr_t.start()
    return process, stdout_t, stderr_t



def main():
    settings = Settings()
    plugin_paths = settings.plugin_paths
    logger.info(f"Running {len(plugin_paths)} plugin(s).")

    popen_kwargs = {}
    plugin_path_to_procs: dict[str, subprocess.Popen] = {}

    def run_and_wait(plugin_path: str) -> tuple[str, int]:
        cfg = settings.plugins.get(plugin_path, PluginConfig())
        process, stdout_t, stderr_t = run_plugin(
            plugin_path,
            plugin_args=cfg.plugin_args,
            uv_run_args=cfg.uv_args,
            popen_kwargs=popen_kwargs,
        )
        plugin_path_to_procs[plugin_path] = process
        if cfg.wait:
            process.wait()
            stdout_t.join()
            stderr_t.join()
        return plugin_path, process.returncode

    if settings.parallel:
        executor = ThreadPoolExecutor()
        futures = {executor.submit(run_and_wait, p): p for p in plugin_paths}
        for future in as_completed(futures):
            plugin_path, rc = future.result()
            if rc != 0:
                logger.info(f"{plugin_path} failed with return code {rc}")
                if settings.fail_fast:
                    logger.info(
                        "Fail fast enabled: terminating any plugins still running."
                    )
                    for plugin_path, proc in plugin_path_to_procs.items():
                        if proc.poll() is None:
                            logger.info(
                                f"Terminating {plugin_path} with PID {proc.pid}"
                            )
                            _terminate_tree(proc)
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
            else:
                logger.info(f"{plugin_path} completed successfully.")
        else:
            executor.shutdown(wait=True)
    else:
        for plugin_path in plugin_paths:
            plugin_path, rc = run_and_wait(plugin_path)
            if rc != 0:
                logger.info(f"{plugin_path} failed with return code {rc}")
                if settings.fail_fast:
                    logger.info("Fail fast enabled, exiting.")
                    break
            else:
                logger.info(f"{plugin_path} completed successfully.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
    )
    main()
