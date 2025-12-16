"""
Responsibility:
    - Provide the public API for the staticconfiguration package by exposing
      the staticconfig decorator and Data class.

Contracts:
    - Users should import staticconfig and Data from this module to create
      static configuration classes.
    - This module serves as the main entry point for the package.
"""

from .config_base import staticconfig
from .entities import Data

__all__ = ["staticconfig", "Data"]
