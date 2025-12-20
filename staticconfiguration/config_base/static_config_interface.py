# Copyright (c) 2025 David Muñoz Pecci
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
Responsibility:
    - Define the interface that all static configuration classes must implement
      by declaring required class attributes.

Contracts:
    - Classes implementing this interface must define __config_file__, __version__,
      __development__, and __config_path__ as class attributes.
    - This interface serves as a contract enforced by the staticconfig decorator.
"""

from __future__ import annotations

from abc import ABC

class StaticConfigInterface(ABC):
    """
    Interface defining required attributes for static configuration classes.

    Responsibility:
        - Declare the required class attributes that all static configuration
          classes must define to enable proper configuration management.

    Contracts:
        Invariants:
            - Implementing classes must define all four annotated attributes.
            - __config_file__ specifies the configuration file name.
            - __version__ specifies the configuration schema version.
            - __development__ indicates development mode status.
            - __config_path__ specifies the directory path for configuration storage.
    """

    __config_file__: str
    __version__: str
    __development__: bool
    __config_path__: str
