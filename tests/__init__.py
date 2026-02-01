"""
Responsibility:
    - Configure the test environment by ensuring the package root is importable.

Contracts:
    - This module must be imported before running tests to set up proper import
      paths.
    - The package root is added to sys.path only if not already present.
"""

import sys
from pathlib import Path

# Ensure the package root is importable when running tests directly from this directory.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))