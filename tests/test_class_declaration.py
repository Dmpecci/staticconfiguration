"""
Responsibility:
    - Test the staticconfig decorator's class validation and transformation
      functionality.

Contracts:
    - Tests verify that the decorator properly validates required attributes,
      detects duplicate Data fields, and correctly injects StaticConfigBase
      inheritance.
    - All tests should pass when the decorator behaves according to its contract.
"""

import pytest

from .custom_settings_example import (
    DecoratedCustomSettings,
    build_duplicate_data_class,
    build_missing_required_class,
)

def test_decorated_class_inherits_static_config_base():
    """
    Test that decorated classes inherit from StaticConfigBase.

    Responsibility:
        - Verify that the staticconfig decorator correctly injects StaticConfigBase
          into the class inheritance hierarchy.

    Contracts:
        Preconditions:
            - DecoratedCustomSettings is decorated with @staticconfig.
        Postconditions:
            - StaticConfigBase appears in the method resolution order (MRO).

    Example:
        >>> test_decorated_class_inherits_static_config_base()
    """
    base_class_names = {base.__name__ for base in DecoratedCustomSettings.__mro__}
    assert "StaticConfigBase" in base_class_names


def test_data_fields_are_collected_and_unique():
    """
    Test that Data fields are properly collected and have unique names.

    Responsibility:
        - Verify that the decorator correctly identifies all Data fields and stores
          them in the __data_fields__ class attribute.

    Contracts:
        Preconditions:
            - DecoratedCustomSettings defines three Data fields with unique names.
        Postconditions:
            - __data_fields__ contains exactly three entries with the correct names.

    Example:
        >>> test_data_fields_are_collected_and_unique()
    """
    data_fields = getattr(DecoratedCustomSettings, "__data_fields__", {})
    assert set(data_fields.keys()) == {"api_url", "max_retries", "enable_feature_x"}


def test_missing_required_attributes_raise_type_error():
    """
    Test that missing required attributes raise TypeError.

    Responsibility:
        - Verify that the decorator properly validates the presence of all required
          attributes from StaticConfigInterface.

    Contracts:
        Preconditions:
            - build_missing_required_class creates a class missing __config_file__.
        Postconditions:
            - TypeError is raised during decoration.

    Example:
        >>> test_missing_required_attributes_raise_type_error()
    """
    with pytest.raises(TypeError):
        build_missing_required_class()


def test_duplicate_data_names_raise_type_error():
    """
    Test that duplicate Data field names raise TypeError.

    Responsibility:
        - Verify that the decorator detects and rejects duplicate data field names
          during class validation.

    Contracts:
        Preconditions:
            - build_duplicate_data_class creates a class with two Data fields
              sharing the same name.
        Postconditions:
            - TypeError is raised during decoration.

    Example:
        >>> test_duplicate_data_names_raise_type_error()
    """
    with pytest.raises(TypeError):
        build_duplicate_data_class()
