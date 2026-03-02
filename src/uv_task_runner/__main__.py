# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "pydantic-settings>=2.13.1",
# ]
# ///
from __future__ import annotations

import logging

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
    s = _CliSettings()

    logging.basicConfig(
        level=s.log_level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    pipeline.Pipeline.from_settings(s).run()


if __name__ == "__main__":
    main()
