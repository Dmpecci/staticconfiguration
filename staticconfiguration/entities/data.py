# Copyright (c) 2025 David Muñoz Pecci
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
Responsibility:
    - Define the Data class that serves as a descriptor for configuration fields,
      encapsulating metadata such as name, type, default value, and optional
      encoder/decoder functions.

Contracts:
    - Data instances are immutable after creation and serve as declarative
      specifications for configuration fields.
    - Encoder and decoder functions, if provided, must be compatible with the
      data_type specified.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

class Data:
    """
    Container for configuration field metadata.

    Responsibility:
        - Encapsulate all metadata required to define a configuration field,
          including its name, type, default value, and optional serialization
          functions.

    Contracts:
        Invariants:
            - Once created, Data instances should be treated as immutable
              specifications.
        Preconditions:
            - ``name`` must be a non-empty string.
            - ``data_type`` must be a valid Python type.
            - ``default`` should be compatible with ``data_type``.
            - If provided, ``encoder`` must accept instances of ``data_type``.
            - If provided, ``decoder`` must return instances of ``data_type``.
        Postconditions:
            - All provided attributes are stored and accessible as instance
              attributes.
    """

    def __init__(self, name: str, data_type: type, default: object, 
                 encoder: function | None = None, decoder: function | None = None) -> None:
        """
        Initialize a Data descriptor for a configuration field.

        Responsibility:
            - Store all metadata required to manage a configuration field,
              including type validation and serialization capabilities.

        Contracts:
            Preconditions:
                - ``name`` is a non-empty string identifying the field.
                - ``data_type`` is a valid Python type (e.g., int, str, bool).
                - ``default`` is a value compatible with ``data_type``.
                - ``encoder`` (optional) is a callable that converts ``data_type``
                  instances to JSON-serializable values.
                - ``decoder`` (optional) is a callable that converts JSON values
                  back to ``data_type`` instances.
            Postconditions:
                - All attributes are stored in the instance.

        Args:
            name (str): Unique identifier for the configuration field.
            data_type (type): Python type of the configuration value.
            default (object): Default value for the field.
            encoder (function | None): Optional function to encode values for
                JSON serialization.
            decoder (function | None): Optional function to decode values from
                JSON.

        Returns:
            None: Initializes the Data instance.

        Example:
            >>> timeout = Data(name="timeout", data_type=int, default=30)
            >>> url = Data(name="api_url", data_type=str, default="https://api.example.com")
        """
        self.name: str = name
        self.data_type: type = data_type
        self.default: object = default
        self.encoder: Optional[Callable[[Any], Any]] = encoder
        self.decoder: Optional[Callable[[Any], Any]] = decoder

    def __repr__(self) -> str:
        """
        Return a string representation of the Data instance.

        Responsibility:
            - Provide a human-readable representation showing the key attributes
              of the Data descriptor.

        Contracts:
            Postconditions:
                - Returns a string in the format Data(name=..., data_type=..., default=...).

        Returns:
            str: String representation of the Data instance.

        Example:
            >>> data = Data(name="timeout", data_type=int, default=30)
            >>> repr(data)
            "Data(name='timeout', data_type=<class 'int'>, default=30)"
        """
        return f"Data(name={self.name!r}, data_type={self.data_type!r}, default={self.default!r})"
