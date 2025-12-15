from __future__ import annotations
from pathlib import Path
from staticconfiguration.entities import Data

class JSONBackend:
    def ensure_initialized(self, config_path: str, version: str, data_fields: list[Data]) -> None:
        raise NotImplementedError("Subclasses must implement the get method.")

    def read_value(self, data: Data, config_path: Path):
        raise NotImplementedError("Subclasses must implement the get method.")

    def write_value(self, data: Data, new_value, config_path: Path):
        raise NotImplementedError("Subclasses must implement the get method.")
    
    def _init_json_file(self, config_path: Path, version: str, data_fields: list[Data]) -> None:
        raise NotImplementedError("Subclasses must implement the get method.")