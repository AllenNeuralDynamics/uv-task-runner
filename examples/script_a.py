# /// script
# requires-python = "==3.11.9"
# dependencies = [
#     "polars",
#     "pydantic-settings",
# ]
# ///
import sys
import time
from pathlib import Path

import polars
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(cli_parse_args=True)
    param1: str = "default_value"

proof = Path('a_finished.txt')
proof.unlink(missing_ok=True)  # ensure proof file doesn't exist before we start
print(
    f'{Path(__file__).name} loaded polars version {polars.__version__}'
)
if sys.version_info[:3] != (3, 11, 9):
    raise AssertionError(
        f"Expected Python 3.11.9, but got {'.'.join(map(str, sys.version_info[:3]))}"
    )
print(f'{Path(__file__).name} running on Python {".".join(map(str, sys.version_info[:3]))}')
if Settings().param1 != "default_value":
    print(
        f"{Path(__file__).name} successfully received param1 from command line: {Settings().param1}"
    )
else:
    raise AssertionError("param1 was not passed correctly to the task.")
time.sleep(8) # simulate long-running task that finished after parent process exits
proof.touch()  # create proof file to indicate successful completion
print(f"{Path(__file__).name} finished")
