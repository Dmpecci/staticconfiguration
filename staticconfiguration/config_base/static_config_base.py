from __future__ import annotations

from .static_config_interface import StaticConfigInterface

class StaticConfigBase(StaticConfigInterface):
    def get(data_name: str):
        raise NotImplementedError("Subclasses must implement the get method.")
    
    def set(data_name: str, new_value: any):
        raise NotImplementedError("Subclasses must implement the set method.")