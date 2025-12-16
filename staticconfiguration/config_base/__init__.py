"""
Responsibility:
    - Expose the core components of the configuration base system, including
      the decorator, base class, and interface.

Contracts:
    - This module provides the building blocks for creating static configuration
      classes with validation and persistence capabilities.
    - All exports are designed to work together as a cohesive system.
"""

from .decorator import staticconfig
from .static_config_base import StaticConfigBase
from .static_config_interface import StaticConfigInterface

__all__ = ["staticconfig", "decorate_settings_class", "StaticConfigBase", "StaticConfigInterface"]
