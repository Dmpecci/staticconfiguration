"""
Responsibility:
    - Expose the Data class for defining configuration field descriptors.

Contracts:
    - This module provides the entity classes used throughout the
      staticconfiguration system.
    - Data instances serve as declarative specifications for configuration fields.
"""

from .data import Data

__all__ = ["Data"]
