# tests/__init__.py
import sys
from pathlib import Path

# Ensure the package root is importable when running tests directly from this directory.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
