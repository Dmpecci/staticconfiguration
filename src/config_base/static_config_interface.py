from __future__ import annotations

from abc import ABC


class StaticConfigInterface(ABC):

    __config_file__: str
    __version__: str
    __development__: bool
    __config_path__: str
