from __future__ import annotations

from typing import Any

class Data:
    """Container for configuration metadata."""

    def __init__(self, name: str, data_type: type, default: object, 
                 encoder: function | None = None, decoder: function | None = None) -> None:
        self.name: str = name
        self.data_type: type = data_type
        self.default: object = default
        self.encoder: function | None = encoder 
        self.decoder: function | None = decoder

    def __repr__(self) -> str:
        return f"Data(name={self.name!r}, data_type={self.data_type!r}, default={self.default!r})"
