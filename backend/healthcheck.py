import os
import sys
from urllib.error import URLError
from urllib.request import urlopen

try:
    with urlopen(f"http://127.0.0.1:{os.getenv('PORT', '8000')}/health", timeout=3) as response:
        sys.exit(0 if response.status == 200 else 1)
except (OSError, URLError):
    sys.exit(1)
