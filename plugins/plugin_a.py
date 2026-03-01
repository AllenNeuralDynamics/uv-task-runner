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

print(f'{Path(__file__).name} loaded on Python {".".join(map(str, sys.version_info[:3]))} with polars version {polars.__version__}')
time.sleep(5)
print(f'{Path(__file__).name} finished')