from __future__ import annotations

from typing import Dict

from ..entities import Data
from .static_config_base import StaticConfigBase
from .static_config_interface import StaticConfigInterface


def _verify_required_attributes(cls: type) -> None:
    required_attrs = tuple(getattr(StaticConfigInterface, "__annotations__", {}).keys())

    missing = [attr for attr in required_attrs if not hasattr(cls, attr)]
    if missing:
        missing_list = ", ".join(missing)
        raise TypeError(f"Class {cls.__name__} is missing required attributes: {missing_list}")

def _verify_data_fields(cls: type) -> None:
    data_fields: Dict[str, Data] = {}

    for attr_name, value in cls.__dict__.items():
        if isinstance(value, Data):
            if value.name in data_fields:
                raise TypeError(f"Duplicate data field name detected: {value.name!r}")
            data_fields[value.name] = value

    if data_fields:
        cls.__data_fields__ = data_fields

def _inject_config_base_inheritance(cls: type) -> type:
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


def decorate_settings_class(cls: type) -> type:
    _verify_required_attributes(cls)
    _verify_data_fields(cls)

    return _inject_config_base_inheritance(cls)


staticconfig = decorate_settings_class

__all__ = [
    "staticconfig",
    "decorate_settings_class",
    "_verify_required_attributes",
    "_verify_data_fields",
    "_inject_config_base_inheritance",
]
