"""
Responsibility:
    - Provide usage examples and test helper functions for the staticconfig
      decorator.

Contracts:
    - This module contains example configuration classes that demonstrate correct
      and incorrect usage of the staticconfig decorator.
    - Helper functions intentionally create classes that violate contracts to test
      error handling.
"""

from __future__ import annotations

from staticconfiguration import staticconfig, Data

@staticconfig
class DecoratedCustomSettings:
    """
    Example configuration class demonstrating correct staticconfig usage.

    Responsibility:
        - Serve as a test fixture and example of a properly decorated static
          configuration class.

    Contracts:
        Invariants:
            - Defines all required attributes from StaticConfigInterface.
            - Contains three Data fields with unique names.
        Preconditions:
            - Decorated with @staticconfig to enable configuration functionality.
        Postconditions:
            - Inherits from StaticConfigBase and can use get/set methods.
    """
    __config_file__: str = "settings.json"
    __version__: str = "1.0.0"
    __development__: bool = True
    __config_path__: str = "~/.config"

    api_url = Data(name="api_url", data_type=str, default="https://api.example.com")
    max_retries = Data(name="max_retries", data_type=int, default=3)
    enable_feature_x = Data(name="enable_feature_x", data_type=bool, default=False)


def build_missing_required_class():
    """
    Build a class that violates the required attributes contract.

    Responsibility:
        - Create a test fixture that intentionally omits required attributes
          to verify decorator validation.

    Contracts:
        Preconditions:
            - Function is called in a test context where exceptions are expected.
        Postconditions:
            - The returned class definition will cause the decorator to raise
              TypeError due to missing __config_file__ attribute.

    Returns:
        type: A class that violates StaticConfigInterface requirements.

    Raises:
        TypeError: When the decorator validates the class and finds missing
            required attributes.

    Example:
        >>> with pytest.raises(TypeError):
        >>>     build_missing_required_class()
    """
    @staticconfig
    class MissingRequired:
        __version__: str = "1.0.0"
        __development__: bool = True
        __config_path__: str = "~/.config"

    return MissingRequired


def build_duplicate_data_class():
    """
    Build a class with duplicate Data field names to test validation.

    Responsibility:
        - Create a test fixture that intentionally defines duplicate Data field
          names to verify decorator validation.

    Contracts:
        Preconditions:
            - Function is called in a test context where exceptions are expected.
        Postconditions:
            - The returned class definition will cause the decorator to raise
              TypeError due to duplicate data field names.

    Returns:
        type: A class with duplicate Data field names.

    Raises:
        TypeError: When the decorator detects duplicate data field names during
            validation.

    Example:
        >>> with pytest.raises(TypeError):
        >>>     build_duplicate_data_class()
    """
    @staticconfig
    class DuplicateDataNames:
        __config_file__: str = "settings.json"
        __version__: str = "1.0.0"
        __development__: bool = True
        __config_path__: str = "~/.config"

        first = Data(name="api_url", data_type=str, default="https://api.example.com")
        second = Data(name="api_url", data_type=str, default="https://api.backup.com")

    return DuplicateDataNames
