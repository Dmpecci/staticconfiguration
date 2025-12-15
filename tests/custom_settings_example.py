"""Usage examples and helpers for the staticconfig decorator."""

from __future__ import annotations

from staticconfiguration import staticconfig, Data

@staticconfig
class DecoratedCustomSettings:
    __config_file__: str = "settings.json"
    __version__: str = "1.0.0"
    __development__: bool = True
    __config_path__: str = "~/.config"

    api_url = Data(name="api_url", data_type=str, default="https://api.example.com")
    max_retries = Data(name="max_retries", data_type=int, default=3)
    enable_feature_x = Data(name="enable_feature_x", data_type=bool, default=False)


def build_missing_required_class():
    @staticconfig
    class MissingRequired:
        __version__: str = "1.0.0"
        __development__: bool = True
        __config_path__: str = "~/.config"

    return MissingRequired


def build_duplicate_data_class():
    @staticconfig
    class DuplicateDataNames:
        __config_file__: str = "settings.json"
        __version__: str = "1.0.0"
        __development__: bool = True
        __config_path__: str = "~/.config"

        first = Data(name="api_url", data_type=str, default="https://api.example.com")
        second = Data(name="api_url", data_type=str, default="https://api.backup.com")

    return DuplicateDataNames
