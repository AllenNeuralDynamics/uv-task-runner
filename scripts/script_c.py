import time
import sys 
from pathlib import Path

print(f'{Path(__file__).name} loaded on Python {".".join(map(str, sys.version_info[:3]))}')
time.sleep(5)
print(f'{Path(__file__).name} finished')