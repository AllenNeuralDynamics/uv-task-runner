# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "polars",
#     "pydantic-settings",
# ]
# ///
import time
import sys
from pathlib import Path

import polars
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(cli_parse_args=True)
    param1: str = "default_value"
    


print(f'{Path(__file__).name} loaded on Python {".".join(map(str, sys.version_info[:3]))} with polars version {polars.__version__}')
if Settings().param1 != "default_value":
    print(f"{Path(__file__).name} successfully received param1 from command line: {Settings().param1}")
else:
    raise AssertionError("param1 was not passed correctly to the plugin.")
time.sleep(5)
print(f'{Path(__file__).name} finished')