# config_base/__init__.py
from .decorator import staticconfig, decorate_settings_class
from .static_config_base import StaticConfigBase
from .static_config_interface import StaticConfigInterface

__all__ = ["staticconfig", "decorate_settings_class", "StaticConfigBase", "StaticConfigInterface"]
