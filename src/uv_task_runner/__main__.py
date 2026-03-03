# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "pydantic-settings>=2",
# ]
# ///
"""CLI entry point for uv-task-runner."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    CliSettingsSource,
    PydanticBaseSettingsSource,
    TomlConfigSettingsSource,
)

from uv_task_runner import pipeline, settings

logger = logging.getLogger(__name__)


class _CliSettings(settings.Settings):
    """Settings subclass that adds CLI argument and TOML file parsing.

    Used only by main(). Library users should construct Settings directly.
    """

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        config_path, cli_args = settings._parse_config_path()
        return (
            init_settings,
            CliSettingsSource(
                settings_cls,
                cli_parse_args=cli_args,
                cli_kebab_case=True,
                cli_implicit_flags=True,
            ),
            TomlConfigSettingsSource(settings_cls, toml_file=config_path),
        )


def main() -> None:
    # Handle --init as a unique case then exit:
    if "--init" in sys.argv[1:]:
        import argparse

        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("--init", nargs="?", const=settings.DEFAULT_CONFIG_PATH)
        args, _ = p.parse_known_args(sys.argv[1:])
        try:
            dest = settings.write_template_config(args.init)
            print(f"Created {dest}")
        except FileExistsError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        return

    config_path, _ = settings._parse_config_path()
    if not Path(config_path).exists():
        if "--config" in sys.argv[1:]:
            print(f"Error: config file not found: {config_path}", file=sys.stderr)
            raise SystemExit(1)
        print(
            f"No config file found at '{Path(config_path).resolve()}'. "
            f"Run --init <dest> to create a template, or pass --config <src>.",
            file=sys.stderr,
        )

    s = _CliSettings()

    logging.basicConfig(
        level=s.log_level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    pipeline.Pipeline.from_settings(s).run()


if __name__ == "__main__":
    main()
