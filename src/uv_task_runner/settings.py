from __future__ import annotations

import logging
import sys

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from uv_task_runner import task


def _parse_config_path() -> tuple[str, list[str]]:
    """Pre-parse --config before full settings initialization.

    Returns (config_path, remaining_args) where remaining_args is sys.argv[1:]
    with --config and its value removed. Pass remaining_args to CliSettingsSource
    so the --config flag is not treated as an unrecognised argument.
    """
    import argparse

    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", default="task_runner.toml")
    args, remaining = pre.parse_known_args(sys.argv[1:])
    return args.config, remaining


class Settings(BaseSettings):
    parallel: bool = False
    fail_fast: bool = False
    dry_run: bool = False
    log_level: str | int = "INFO"
    # Emit subprocess output line-by-line (false) or buffer per stream (true).
    # Line-by-line is the default: output appears in real-time and is not lost
    # for wait=false tasks. Set to true to keep multiline output (e.g. stack
    # traces) together at the cost of buffering until the subprocess exits:
    log_multiline: bool = False
    tasks: list[task.TaskConfig] = Field(default_factory=list)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Library-safe: only uses init kwargs. No CLI parsing, no TOML file.
        # For CLI usage, see _CliSettings in __main__.py.
        return (init_settings,)

    @field_validator("log_level")
    def validate_log_level(cls, v: str | int) -> str:
        if isinstance(v, str) and v.isnumeric() or isinstance(v, int):
            # Numeric value: look up the name
            name = logging.getLevelName(int(v))
            if name.startswith("Level "):
                raise ValueError(f"Invalid log level: {v}")
            return name
        else:
            # String name: normalise and verify it's a known level
            level_name = v.upper()
            if not isinstance(getattr(logging, level_name, None), int):
                raise ValueError(f"Invalid log level: {v}")
            return level_name
