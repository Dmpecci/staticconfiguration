from pathlib import Path
from staticconfiguration.entities import Data
from staticconfiguration.json_backend.json_backend import JSONBackend

def remove_json(path: Path):
    """
    Remove a JSON file if it exists.

    Responsibility:
        - Safely delete the specified JSON file without raising an error if
          the file does not exist.
    """
    try:
        if path.suffix.lower() != '.json':
            return
        if path.exists() and not path.is_file():
            return
        path.unlink()
    except FileNotFoundError:
        pass


# ============================================================================
# Test Helpers for New API (v2.0)
# ============================================================================

def read_value_simple(config_path: Path, data_field: Data, data_fields: list = None, 
                      version: str = "1.0.0", concurrency_unsafe: bool = False):
    """
    Helper to read a value using the new API with sensible defaults.
    
    This simplifies test code by providing default values for commonly-used parameters.
    """
    backend = JSONBackend()
    if data_fields is None:
        data_fields = [data_field]
    
    return backend.read_value(
        data=data_field,
        config_path=config_path,
        version=version,
        data_fields=data_fields,
        development=False,
        concurrency_unsafe=concurrency_unsafe
    )


def write_value_simple(config_path: Path, data_field: Data, value, data_fields: list = None,
                       version: str = "1.0.0", concurrency_unsafe: bool = False):
    """
    Helper to write a value using the new API with sensible defaults.
    
    This simplifies test code by providing default values for commonly-used parameters.
    """
    backend = JSONBackend()
    if data_fields is None:
        data_fields = [data_field]
    
    backend.write_value(
        data=data_field,
        new_value=value,
        config_path=config_path,
        version=version,
        data_fields=data_fields,
        development=False,
        concurrency_unsafe=concurrency_unsafe
    )