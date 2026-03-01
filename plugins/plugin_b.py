import sys
from pathlib import Path
print(f'{Path(__file__).name} loaded on Python {".".join(map(str, sys.version_info[:3]))}')

raise ValueError(f"Simulated error in {Path(__file__).name}")
