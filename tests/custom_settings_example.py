"""Usage example for the staticconfig library."""

from __future__ import annotations

from src.config_base.static_config_base import StaticConfigBase
from src.entities.data import Data

class CustomSettings(StaticConfigBase):
    __config_file__: str = "settings.json"
    __version__: str = "1.0.0"
    __development__: bool = True
    __config_path__: str = "~/.config"

    api_url = Data(name="api_url", data_type=str, default="https://api.example.com")
    max_retries = Data(name="max_retries", data_type=int, default=3)
    enable_feature_x = Data(name="enable_feature_x", data_type=bool, default=False)

if __name__ == "__main__":

    print(f"API URL: {CustomSettings.api_url}")
    print(f"Max Retries: {CustomSettings.max_retries}")