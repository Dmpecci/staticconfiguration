# Copyright (c) 2025 David Muñoz Pecci
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
Responsibility:
    - Provide the staticconfig decorator to transform user-defined configuration
      classes into fully functional static configuration classes with validation
      and persistence capabilities.

Contracts:
    - The decorator expects classes to define required attributes from
      StaticConfigInterface.
    - Data fields with duplicate names will raise TypeError during decoration.
    - All decorated classes will inherit from StaticConfigBase.
"""

from __future__ import annotations

from typing import Dict

from ..entities import Data
from .static_config_base import StaticConfigBase
from .static_config_interface import StaticConfigInterface


def _verify_required_attributes(cls: type) -> None:
    """
    Verify that the class defines all required attributes from StaticConfigInterface.

    Responsibility:
        - Validate that the decorated class has all required configuration
          attributes defined in StaticConfigInterface.

    Contracts:
        Preconditions:
            - ``cls`` is a type that is being decorated with staticconfig.
        Postconditions:
            - If all required attributes are present, the function returns without
              raising an exception.
            - If any required attributes are missing, raises TypeError.

    Args:
        cls (type): The class to verify for required attributes.

    Returns:
        None: Does not return a value; raises TypeError if validation fails.

    Raises:
        TypeError: If the class is missing any required attributes from
            StaticConfigInterface.

    Example:
        >>> @staticconfig
        >>> class MyConfig:
        >>>     __config_file__ = "config.json"
        >>>     __version__ = "1.0.0"
    """
    required_attrs = tuple(getattr(StaticConfigInterface, "__annotations__", {}).keys())

    missing = [attr for attr in required_attrs if not hasattr(cls, attr)]
    if missing:
        missing_list = ", ".join(missing)
        raise TypeError(f"Class {cls.__name__} is missing required attributes: {missing_list}")

def _verify_data_fields(cls: type) -> None:
    """
    Verify that all Data fields in the class have unique names.

    Responsibility:
        - Collect all Data instances from the class definition and validate that
          no duplicate data field names exist. Store the collected fields in __data_fields__ 
          for validation and introspection purposes.

    Contracts:
        Preconditions:
            - ``cls`` is a type that may contain Data field declarations.
        Postconditions:
            - If Data fields exist and have unique names, they are stored in
              ``cls.__data_fields__`` as a dictionary.
            - If duplicate data field names are detected, raises TypeError.

    Args:
        cls (type): The class to verify for Data field uniqueness.

    Returns:
        None: Does not return a value; modifies cls.__data_fields__ if Data
            fields are present.

    Raises:
        TypeError: If duplicate data field names are detected.

    Example:
        >>> @staticconfig
        >>> class MyConfig:
        >>>     field1 = Data(name="api_url", data_type=str, default="")
        >>>     field2 = Data(name="api_url", data_type=str, default="")  # raises TypeError
    """
    data_fields: Dict[str, Data] = {}

    for attr_name, value in cls.__dict__.items():
        if isinstance(value, Data):
            if value.name in data_fields:
                raise TypeError(f"Duplicate data field name detected: {value.name!r}")
            data_fields[value.name] = value

    if data_fields:
        cls.__data_fields__ = data_fields

def _inject_config_base_inheritance(cls: type) -> type:
    """
    Inject StaticConfigBase into the class inheritance hierarchy.

    Responsibility:
        - Ensure the decorated class inherits from StaticConfigBase by creating
          a new class with StaticConfigBase as a base if not already present.

    Contracts:
        Preconditions:
            - ``cls`` is a valid type being decorated.
        Postconditions:
            - Returns a class that inherits from StaticConfigBase.
            - If the class already inherits from StaticConfigBase, returns it
              unchanged.
            - Otherwise, creates a new class with StaticConfigBase injected.

    Args:
        cls (type): The class to inject StaticConfigBase inheritance into.

    Returns:
        type: A class that inherits from StaticConfigBase.

    Example:
        >>> class MyConfig:
        >>>     pass
        >>> new_class = _inject_config_base_inheritance(MyConfig)
        >>> issubclass(new_class, StaticConfigBase)
        True
    """
    if issubclass(cls, StaticConfigBase):
        return cls

    new_class = type(
        cls.__name__,
        (StaticConfigBase, cls),
        {
            "__module__": cls.__module__,
            "__doc__": cls.__doc__,
        },
    )
    return new_class


def staticconfig(cls: type) -> type:
    """
    Decorate a settings class to enable static configuration functionality.

    Responsibility:
        - Validate the class structure, verify required attributes and Data fields,
          and inject StaticConfigBase inheritance to enable configuration
          persistence and access.

    Contracts:
        Preconditions:
            - ``cls`` must define all required attributes from StaticConfigInterface.
            - All Data fields in ``cls`` must have unique names.
        Postconditions:
            - Returns a class that inherits from StaticConfigBase.
            - The returned class has validated Data fields stored in
              ``__data_fields__``.
            - The class is ready to use get/set methods for configuration access.

    Args:
        cls (type): The class to decorate as a static configuration class.

    Returns:
        type: The decorated class with StaticConfigBase functionality.

    Raises:
        TypeError: If required attributes are missing or Data field names are
            duplicated.

    Example:
        >>> @staticconfig
        >>> class AppConfig:
        >>>     __config_path__ = "~/.config/myapp"
        >>>     __config_file__ = "app.json"
        >>>     __version__ = "1.0.0"
        >>>     __development__ = False
        >>>     timeout = Data(name="timeout", data_type=int, default=30)
    """
    _verify_required_attributes(cls)
    _verify_data_fields(cls)

    return _inject_config_base_inheritance(cls)
