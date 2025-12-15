import pytest

from .custom_settings_example import (
    DecoratedCustomSettings,
    build_duplicate_data_class,
    build_missing_required_class,
)

def test_decorated_class_inherits_static_config_base():
    base_class_names = {base.__name__ for base in DecoratedCustomSettings.__mro__}
    assert "StaticConfigBase" in base_class_names


def test_data_fields_are_collected_and_unique():
    data_fields = getattr(DecoratedCustomSettings, "__data_fields__", {})
    assert set(data_fields.keys()) == {"api_url", "max_retries", "enable_feature_x"}


def test_missing_required_attributes_raise_type_error():
    with pytest.raises(TypeError):
        build_missing_required_class()


def test_duplicate_data_names_raise_type_error():
    with pytest.raises(TypeError):
        build_duplicate_data_class()
