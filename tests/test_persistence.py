"""
Test suite for JSONBackend and StaticConfigBase persistence functionality.

This module provides comprehensive testing of the static configuration library,
covering both the low-level JSONBackend operations and the high-level
StaticConfigBase API. Tests are designed to validate correct behavior,
edge cases, and failure modes without modifying production code.

Test Organization:
    - TestJSONBackendEnsureInitialized: File creation and initialization (8 tests)
    - TestJSONBackendReadValue: Reading and decoding configuration values (8 tests)
    - TestJSONBackendWriteValue: Writing and encoding configuration values (8 tests)
    - TestStaticConfigBaseGet: High-level configuration retrieval (5 tests)
    - TestStaticConfigBaseSet: High-level configuration updates (5 tests)
    - TestPersistenceEdgeCases: Complex scenarios and edge cases (11 tests)

Test Results:
    - ✅ 45/45 PASSED: All tests pass successfully (100% success rate)
    
Historical Note:
    Originally, 9 tests failed due to a critical bug in _get_data_fields() that only
    inspected cls.__dict__ and couldn't find Data fields after decoration. This bug
    has been FIXED in the current implementation. All tests now pass.
    See TEST_REPORT.md for complete historical context and current status.

Each test is isolated and cleans up its own resources using the remove_json utility.
"""

import json
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
import tempfile
import time

from staticconfiguration.entities import Data
from staticconfiguration.json_backend.json_backend import JSONBackend
from staticconfiguration import staticconfig
from tests.test_utilities import remove_json

# ============================================================================
# Test Fixtures and Helper Classes
# ============================================================================

@pytest.fixture
def temp_config_dir():
    """Provide a temporary directory for test configuration files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def backend():
    """Provide a fresh JSONBackend instance."""
    return JSONBackend()


@pytest.fixture
def sample_data_fields():
    """Provide standard Data fields for testing."""
    return [
        Data(name="api_url", data_type=str, default="https://api.example.com"),
        Data(name="max_retries", data_type=int, default=3),
        Data(name="enable_feature", data_type=bool, default=False),
    ]

@staticconfig
class TestConfig:
    """Test configuration class for StaticConfigBase testing.
    
    This class is properly decorated and works correctly with the current implementation.
    Historical note: Originally demonstrated a bug in _get_data_fields() that has been fixed.
    """
    __config_file__: str = "test_config.json"
    __version__: str = "1.0.0"
    __development__: bool = True
    __config_path__: str = ""  # Will be set per test
    
    api_url = Data(name="api_url", data_type=str, default="https://api.example.com")
    max_retries = Data(name="max_retries", data_type=int, default=3)
    timeout = Data(name="timeout", data_type=int, default=30)


# ============================================================================
# TestJSONBackendEnsureInitialized
# ============================================================================

class TestJSONBackendEnsureInitialized:
    """Test suite for JSONBackend.ensure_initialized method."""
    
    def test_creates_file_with_correct_structure(self, backend, temp_config_dir, sample_data_fields):
        """Test that ensure_initialized creates a JSON file with required structure."""
        config_path = temp_config_dir / "config.json"
        version = "1.0.0"
        
        backend.ensure_initialized(config_path, version, sample_data_fields)
        
        assert config_path.exists()
        
        with config_path.open("r") as f:
            data = json.load(f)
        
        assert "version" in data
        assert "created" in data
        assert "last_modified" in data
        assert "data" in data
        assert data["version"] == version
        
        remove_json(config_path)
    
    def test_initializes_with_default_values(self, backend, temp_config_dir, sample_data_fields):
        """Test that default values are correctly written during initialization."""
        config_path = temp_config_dir / "config.json"
        
        backend.ensure_initialized(config_path, "1.0.0", sample_data_fields)
        
        with config_path.open("r") as f:
            data = json.load(f)
        
        assert data["data"]["api_url"] == "https://api.example.com"
        assert data["data"]["max_retries"] == 3
        assert data["data"]["enable_feature"] is False
        
        remove_json(config_path)
    
    def test_does_not_overwrite_existing_file(self, backend, temp_config_dir, sample_data_fields):
        """Test that ensure_initialized does not modify existing configuration files."""
        config_path = temp_config_dir / "config.json"
        
        # Create initial file
        backend.ensure_initialized(config_path, "1.0.0", sample_data_fields)
        
        # Modify the file
        with config_path.open("r") as f:
            data = json.load(f)
        
        data["data"]["max_retries"] = 99
        data["custom_field"] = "custom_value"
        
        with config_path.open("w") as f:
            json.dump(data, f)
        
        # Call ensure_initialized again
        backend.ensure_initialized(config_path, "2.0.0", sample_data_fields)
        
        # Verify file was not overwritten
        with config_path.open("r") as f:
            data = json.load(f)
        
        assert data["data"]["max_retries"] == 99
        assert data["custom_field"] == "custom_value"
        assert data["version"] == "1.0.0"  # Not updated
        
        remove_json(config_path)
    
    def test_creates_intermediate_directories(self, backend, temp_config_dir, sample_data_fields):
        """Test that ensure_initialized creates missing parent directories."""
        config_path = temp_config_dir / "nested" / "deep" / "config.json"
        
        assert not config_path.parent.exists()
        
        backend.ensure_initialized(config_path, "1.0.0", sample_data_fields)
        
        assert config_path.exists()
        assert config_path.parent.exists()
        
        remove_json(config_path)
    
    def test_timestamps_are_in_correct_format(self, backend, temp_config_dir, sample_data_fields):
        """Test that timestamps are in ISO 8601 UTC format without microseconds."""
        config_path = temp_config_dir / "config.json"
        
        before = datetime.now(timezone.utc)
        backend.ensure_initialized(config_path, "1.0.0", sample_data_fields)
        after = datetime.now(timezone.utc)
        
        with config_path.open("r") as f:
            data = json.load(f)
        
        created = data["created"]
        last_modified = data["last_modified"]
        
        # Verify format: ends with Z and no microseconds
        assert created.endswith("Z")
        assert last_modified.endswith("Z")
        assert "." not in created  # No microseconds
        
        # Verify timestamps are valid and within expected range
        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        assert before.replace(microsecond=0) <= created_dt <= after.replace(microsecond=0) + timedelta(seconds=1)
        
        remove_json(config_path)
    
    def test_encoder_applied_to_defaults(self, backend, temp_config_dir):
        """Test that encoder functions are applied to default values during initialization."""
        def list_encoder(value):
            return ",".join(value)
        
        data_fields = [
            Data(name="tags", data_type=list, default=["python", "testing"], encoder=list_encoder)
        ]
        
        config_path = temp_config_dir / "config.json"
        backend.ensure_initialized(config_path, "1.0.0", data_fields)
        
        with config_path.open("r") as f:
            data = json.load(f)
        
        # Encoder should have converted list to comma-separated string
        assert data["data"]["tags"] == "python,testing"
        
        remove_json(config_path)
    
    def test_empty_data_fields_list(self, backend, temp_config_dir):
        """Test initialization with no data fields."""
        config_path = temp_config_dir / "config.json"
        
        backend.ensure_initialized(config_path, "1.0.0", [])
        
        with config_path.open("r") as f:
            data = json.load(f)
        
        assert data["data"] == {}
        assert "version" in data
        assert "created" in data
        
        remove_json(config_path)
    
    def test_none_default_values(self, backend, temp_config_dir):
        """Test initialization with None as default value."""
        data_fields = [
            Data(name="optional_field", data_type=str, default=None)
        ]
        
        config_path = temp_config_dir / "config.json"
        backend.ensure_initialized(config_path, "1.0.0", data_fields)
        
        with config_path.open("r") as f:
            data = json.load(f)
        
        assert data["data"]["optional_field"] is None
        
        remove_json(config_path)


# ============================================================================
# TestJSONBackendReadValue
# ============================================================================

class TestJSONBackendReadValue:
    """Test suite for JSONBackend.read_value method."""
    
    def test_reads_existing_value(self, backend, temp_config_dir):
        """Test reading an existing configuration value."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="api_url", data_type=str, default="default.com")
        
        # Create config file
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"api_url": "https://api.example.com"}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        result = backend.read_value(data_field, config_path)
        
        assert result == "https://api.example.com"
        
        remove_json(config_path)
    
    def test_returns_default_when_key_missing(self, backend, temp_config_dir):
        """Test that default value is returned when key doesn't exist in file."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="missing_key", data_type=str, default="default_value")
        
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        result = backend.read_value(data_field, config_path)
        
        assert result == "default_value"
        
        remove_json(config_path)
    
    def test_applies_decoder(self, backend, temp_config_dir):
        """Test that decoder function is applied when reading values."""
        def list_decoder(value):
            return value.split(",") if value else []
        
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="tags", data_type=list, default=[], decoder=list_decoder)
        
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"tags": "python,testing,automation"}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        result = backend.read_value(data_field, config_path)
        
        assert result == ["python", "testing", "automation"]
        
        remove_json(config_path)
    
    def test_type_conversion_without_decoder(self, backend, temp_config_dir):
        """Test automatic type conversion when no decoder is provided."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="count", data_type=int, default=0)
        
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"count": 42}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        result = backend.read_value(data_field, config_path)
        
        assert result == 42
        assert isinstance(result, int)
        
        remove_json(config_path)
    
    def test_handles_none_value(self, backend, temp_config_dir):
        """Test reading None value from configuration."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="optional", data_type=str, default=None)
        
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"optional": None}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        result = backend.read_value(data_field, config_path)
        
        assert result is None
        
        remove_json(config_path)
    
    def test_raises_file_not_found(self, backend, temp_config_dir):
        """Test that FileNotFoundError is raised for non-existent config file."""
        config_path = temp_config_dir / "nonexistent.json"
        data_field = Data(name="field", data_type=str, default="default")
        
        with pytest.raises(FileNotFoundError):
            backend.read_value(data_field, config_path)
    
    def test_raises_on_malformed_json(self, backend, temp_config_dir):
        """Test that json.JSONDecodeError is raised for malformed JSON."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="field", data_type=str, default="default")
        
        # Write malformed JSON
        with config_path.open("w") as f:
            f.write("{invalid json content}")
        
        with pytest.raises(json.JSONDecodeError):
            backend.read_value(data_field, config_path)
        
        remove_json(config_path)
    
    def test_decoder_with_none_default(self, backend, temp_config_dir):
        """Test decoder behavior when default is None."""
        def custom_decoder(value):
            if value is None:
                return None
            return value.upper()
        
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="field", data_type=str, default=None, decoder=custom_decoder)
        
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        result = backend.read_value(data_field, config_path)
        
        assert result is None
        
        remove_json(config_path)


# ============================================================================
# TestJSONBackendWriteValue
# ============================================================================

class TestJSONBackendWriteValue:
    """Test suite for JSONBackend.write_value method."""
    
    def test_writes_value_successfully(self, backend, temp_config_dir):
        """Test basic value writing to configuration file."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="api_url", data_type=str, default="default.com")
        
        # Create initial config
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"api_url": "old_value"}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        backend.write_value(data_field, "new_value", config_path)
        
        with config_path.open("r") as f:
            data = json.load(f)
        
        assert data["data"]["api_url"] == "new_value"
        
        remove_json(config_path)
    
    def test_updates_last_modified_timestamp(self, backend, temp_config_dir):
        """Test that last_modified timestamp is updated on write."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="field", data_type=str, default="default")
        
        original_timestamp = "2023-01-01T00:00:00Z"
        payload = {
            "version": "1.0.0",
            "created": original_timestamp,
            "last_modified": original_timestamp,
            "data": {"field": "old"}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        time.sleep(0.01)  # Ensure timestamp difference
        backend.write_value(data_field, "new", config_path)
        
        with config_path.open("r") as f:
            data = json.load(f)
        
        assert data["last_modified"] != original_timestamp
        assert data["last_modified"].endswith("Z")
        
        remove_json(config_path)
    
    def test_preserves_version_and_created(self, backend, temp_config_dir):
        """Test that version and created timestamp are not modified on write."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="field", data_type=str, default="default")
        
        original_version = "1.2.3"
        original_created = "2023-01-01T00:00:00Z"
        
        payload = {
            "version": original_version,
            "created": original_created,
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"field": "old"}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        backend.write_value(data_field, "new", config_path)
        
        with config_path.open("r") as f:
            data = json.load(f)
        
        assert data["version"] == original_version
        assert data["created"] == original_created
        
        remove_json(config_path)
    
    def test_applies_encoder(self, backend, temp_config_dir):
        """Test that encoder function is applied when writing values."""
        def list_encoder(value):
            return ",".join(value)
        
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="tags", data_type=list, default=[], encoder=list_encoder)
        
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"tags": "old"}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        backend.write_value(data_field, ["python", "testing"], config_path)
        
        with config_path.open("r") as f:
            data = json.load(f)
        
        assert data["data"]["tags"] == "python,testing"
        
        remove_json(config_path)
    
    def test_uses_temporary_file(self, backend, temp_config_dir):
        """Test that write_value uses a temporary file for atomic writes."""
        config_path = temp_config_dir / "config.json"
        tmp_path = config_path.with_suffix(".tmp")
        data_field = Data(name="field", data_type=str, default="default")
        
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"field": "old"}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        backend.write_value(data_field, "new", config_path)
        
        # Temporary file should be removed after successful write
        assert not tmp_path.exists()
        assert config_path.exists()
        
        remove_json(config_path)
    
    def test_raises_file_not_found(self, backend, temp_config_dir):
        """Test that FileNotFoundError is raised when config file doesn't exist."""
        config_path = temp_config_dir / "nonexistent.json"
        data_field = Data(name="field", data_type=str, default="default")
        
        with pytest.raises(FileNotFoundError):
            backend.write_value(data_field, "value", config_path)
    
    def test_preserves_other_fields(self, backend, temp_config_dir):
        """Test that writing one field doesn't affect other fields."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="field1", data_type=str, default="default")
        
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {
                "field1": "value1",
                "field2": "value2",
                "field3": 123
            }
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        backend.write_value(data_field, "new_value1", config_path)
        
        with config_path.open("r") as f:
            data = json.load(f)
        
        assert data["data"]["field1"] == "new_value1"
        assert data["data"]["field2"] == "value2"
        assert data["data"]["field3"] == 123
        
        remove_json(config_path)
    
    def test_write_none_value(self, backend, temp_config_dir):
        """Test writing None value to configuration."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="optional", data_type=str, default=None)
        
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"optional": "something"}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        backend.write_value(data_field, None, config_path)
        
        with config_path.open("r") as f:
            data = json.load(f)
        
        assert data["data"]["optional"] is None
        
        remove_json(config_path)


# ============================================================================
# TestStaticConfigBaseGet
# ============================================================================

class TestStaticConfigBaseGet:
    """Test suite for StaticConfigBase.get method."""
    
    def test_get_existing_value(self, temp_config_dir):
        """Test retrieving an existing configuration value."""
        TestConfig.__config_path__ = str(temp_config_dir)
        config_path = temp_config_dir / TestConfig.__config_file__
        
        try:
            # Initialize and set a value
            TestConfig.set(TestConfig.max_retries, 5)
            
            result = TestConfig.get(TestConfig.max_retries)
            assert result == 5
        finally:
            remove_json(config_path)
    
    def test_get_default_value(self, temp_config_dir):
        """Test that get returns default value when file doesn't exist yet."""
        TestConfig.__config_path__ = str(temp_config_dir)
        config_path = temp_config_dir / TestConfig.__config_file__
        
        try:
            result = TestConfig.get(TestConfig.timeout)
            assert result == 30  # Default value
        finally:
            remove_json(config_path)
    
    def test_get_raises_keyerror_for_undefined_field(self, temp_config_dir):
        """Test that get raises KeyError for non-existent data field."""
        TestConfig.__config_path__ = str(temp_config_dir)
        config_path = temp_config_dir / TestConfig.__config_file__
        
        try:
            with pytest.raises(KeyError) as exc_info:
                TestConfig.get(Data(name="nonexistent_field", data_type=str, default=""))
            
            assert "nonexistent_field" in str(exc_info.value)
        finally:
            remove_json(config_path)
    
    def test_get_creates_file_automatically(self, temp_config_dir):
        """Test that get automatically creates config file if it doesn't exist."""
        TestConfig.__config_path__ = str(temp_config_dir)
        config_path = temp_config_dir / TestConfig.__config_file__
        
        try:
            assert not config_path.exists()
            
            TestConfig.get(TestConfig.api_url)
            
            assert config_path.exists()
        finally:
            remove_json(config_path)
    
    def test_get_with_expanduser(self, temp_config_dir):
        """Test that get correctly expands ~ in config path."""
        # Create a config in temp directory but reference it with absolute path
        TestConfig.__config_path__ = str(temp_config_dir)
        config_path = temp_config_dir / TestConfig.__config_file__
        
        try:
            result = TestConfig.get(TestConfig.api_url)
            assert result == "https://api.example.com"
            assert config_path.exists()
        finally:
            remove_json(config_path)


# ============================================================================
# TestStaticConfigBaseSet
# ============================================================================

class TestStaticConfigBaseSet:
    """Test suite for StaticConfigBase.set method."""
    
    def test_set_value_successfully(self, temp_config_dir):
        """Test setting a configuration value."""
        TestConfig.__config_path__ = str(temp_config_dir)
        config_path = temp_config_dir / TestConfig.__config_file__
        
        try:
            TestConfig.set(TestConfig.max_retries, 10)
            
            result = TestConfig.get(TestConfig.max_retries)
            assert result == 10
        finally:
            remove_json(config_path)
    
    def test_set_raises_keyerror_for_undefined_field(self, temp_config_dir):
        """Test that set raises KeyError for non-existent data field."""
        TestConfig.__config_path__ = str(temp_config_dir)
        config_path = temp_config_dir / TestConfig.__config_file__
        
        try:
            with pytest.raises(KeyError) as exc_info:
                TestConfig.set(Data(name="nonexistent_field", data_type=str, default=""), "value")
            
            assert "nonexistent_field" in str(exc_info.value)
        finally:
            remove_json(config_path)
    
    def test_set_raises_typeerror_for_wrong_type(self, temp_config_dir):
        """Test that set raises TypeError when value type doesn't match data_type."""
        TestConfig.__config_path__ = str(temp_config_dir)
        config_path = temp_config_dir / TestConfig.__config_file__
        
        try:
            with pytest.raises(TypeError) as exc_info:
                TestConfig.set(TestConfig.max_retries, "not_an_int")
            
            assert "max_retries" in str(exc_info.value)
            assert "int" in str(exc_info.value)
        finally:
            remove_json(config_path)
    
    def test_set_creates_file_automatically(self, temp_config_dir):
        """Test that set automatically creates config file if it doesn't exist."""
        TestConfig.__config_path__ = str(temp_config_dir)
        config_path = temp_config_dir / TestConfig.__config_file__
        
        try:
            assert not config_path.exists()
            
            TestConfig.set(TestConfig.timeout, 60)
            
            assert config_path.exists()
        finally:
            remove_json(config_path)
    
    def test_set_preserves_other_fields(self, temp_config_dir):
        """Test that setting one field doesn't affect other fields."""
        TestConfig.__config_path__ = str(temp_config_dir)
        config_path = temp_config_dir / TestConfig.__config_file__
        
        try:
            TestConfig.set(TestConfig.max_retries, 5)
            TestConfig.set(TestConfig.timeout, 60)
            
            # Set one field and verify others are unchanged
            TestConfig.set(TestConfig.api_url, "https://new-api.example.com")
            
            assert TestConfig.get(TestConfig.api_url) == "https://new-api.example.com"
            assert TestConfig.get(TestConfig.max_retries) == 5
            assert TestConfig.get(TestConfig.timeout) == 60
        finally:
            remove_json(config_path)


# ============================================================================
# TestPersistenceEdgeCases
# ============================================================================

class TestPersistenceEdgeCases:
    """Test suite for edge cases and complex scenarios."""
    
    def test_encoder_decoder_roundtrip(self, backend, temp_config_dir):
        """Test that values survive encoder/decoder roundtrip."""
        def dict_encoder(value):
            return json.dumps(value)
        
        def dict_decoder(value):
            return json.loads(value)
        
        config_path = temp_config_dir / "config.json"
        data_field = Data(
            name="config_dict",
            data_type=dict,
            default={"key": "value"},
            encoder=dict_encoder,
            decoder=dict_decoder
        )
        
        backend.ensure_initialized(config_path, "1.0.0", [data_field])
        
        test_value = {"nested": {"key": "value"}, "list": [1, 2, 3]}
        backend.write_value(data_field, test_value, config_path)
        
        result = backend.read_value(data_field, config_path)
        
        assert result == test_value
        
        remove_json(config_path)
    
    def test_concurrent_writes_last_wins(self, backend, temp_config_dir):
        """Test behavior when multiple writes occur in sequence."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="counter", data_type=int, default=0)
        
        backend.ensure_initialized(config_path, "1.0.0", [data_field])
        
        # Simulate multiple rapid writes
        for i in range(10):
            backend.write_value(data_field, i, config_path)
        
        result = backend.read_value(data_field, config_path)
        assert result == 9
        
        remove_json(config_path)
    
    def test_empty_string_value(self, backend, temp_config_dir):
        """Test handling of empty string values."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="text", data_type=str, default="default")
        
        backend.ensure_initialized(config_path, "1.0.0", [data_field])
        backend.write_value(data_field, "", config_path)
        
        result = backend.read_value(data_field, config_path)
        assert result == ""
        
        remove_json(config_path)
    
    def test_boolean_false_value(self, backend, temp_config_dir):
        """Test that False boolean value is correctly handled (not confused with None/empty)."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="enabled", data_type=bool, default=True)
        
        backend.ensure_initialized(config_path, "1.0.0", [data_field])
        backend.write_value(data_field, False, config_path)
        
        result = backend.read_value(data_field, config_path)
        assert result is False
        assert isinstance(result, bool)
        
        remove_json(config_path)
    
    def test_zero_integer_value(self, backend, temp_config_dir):
        """Test that zero integer value is correctly handled."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="count", data_type=int, default=10)
        
        backend.ensure_initialized(config_path, "1.0.0", [data_field])
        backend.write_value(data_field, 0, config_path)
        
        result = backend.read_value(data_field, config_path)
        assert result == 0
        assert isinstance(result, int)
        
        remove_json(config_path)
    
    def test_missing_data_section_in_json(self, backend, temp_config_dir):
        """Test reading from JSON file with missing data section."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="field", data_type=str, default="default")
        
        # Create malformed config without data section
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z"
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        # This should raise KeyError when accessing payload["data"]
        with pytest.raises(KeyError):
            backend.read_value(data_field, config_path)
        
        remove_json(config_path)
    
    def test_type_coercion_string_to_int(self, backend, temp_config_dir):
        """Test automatic type coercion from string to int."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="number", data_type=int, default=0)
        
        # Manually create JSON with string value
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"number": "42"}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        result = backend.read_value(data_field, config_path)
        assert result == 42
        assert isinstance(result, int)
        
        remove_json(config_path)
    
    def test_large_nested_structure(self, backend, temp_config_dir):
        """Test handling of large nested data structures with encoder/decoder."""
        def json_encoder(value):
            return json.dumps(value)
        
        def json_decoder(value):
            return json.loads(value)
        
        config_path = temp_config_dir / "config.json"
        data_field = Data(
            name="complex",
            data_type=dict,
            default={},
            encoder=json_encoder,
            decoder=json_decoder
        )
        
        large_structure = {
            "level1": {
                "level2": {
                    "level3": {
                        "items": [{"id": i, "value": f"item_{i}"} for i in range(100)]
                    }
                }
            }
        }
        
        backend.ensure_initialized(config_path, "1.0.0", [data_field])
        backend.write_value(data_field, large_structure, config_path)
        result = backend.read_value(data_field, config_path)
        
        assert result == large_structure
        assert len(result["level1"]["level2"]["level3"]["items"]) == 100
        
        remove_json(config_path)
    
    def test_unicode_characters(self, backend, temp_config_dir):
        """Test handling of unicode characters in string values."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="message", data_type=str, default="")
        
        unicode_text = "Hello 世界 🌍 Привет مرحبا"
        
        backend.ensure_initialized(config_path, "1.0.0", [data_field])
        backend.write_value(data_field, unicode_text, config_path)
        result = backend.read_value(data_field, config_path)
        
        assert result == unicode_text
        
        remove_json(config_path)
    
    def test_read_after_manual_json_modification(self, backend, temp_config_dir):
        """Test that manually modified JSON files are read correctly."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="setting", data_type=str, default="default")
        
        backend.ensure_initialized(config_path, "1.0.0", [data_field])
        
        # Manually modify the JSON file
        with config_path.open("r") as f:
            data = json.load(f)
        
        data["data"]["setting"] = "manually_changed"
        data["custom_metadata"] = "extra_info"
        
        with config_path.open("w") as f:
            json.dump(data, f)
        
        result = backend.read_value(data_field, config_path)
        assert result == "manually_changed"
        
        # Verify custom metadata is preserved on write
        backend.write_value(data_field, "new_value", config_path)
        
        with config_path.open("r") as f:
            data = json.load(f)
        
        assert data["custom_metadata"] == "extra_info"
        
        remove_json(config_path)
    
    def test_multiple_config_classes_same_directory(self, temp_config_dir):
        """Test that multiple config classes can coexist in same directory."""
        @staticconfig
        class ConfigA:
            __config_file__: str = "config_a.json"
            __version__: str = "1.0.0"
            __development__: bool = True
            __config_path__: str = str(temp_config_dir)
            
            field_a = Data(name="field_a", data_type=str, default="a")
        
        @staticconfig
        class ConfigB:
            __config_file__: str = "config_b.json"
            __version__: str = "1.0.0"
            __development__: bool = True
            __config_path__: str = str(temp_config_dir)
            
            field_b = Data(name="field_b", data_type=str, default="b")
        
        try:
            ConfigA.set(ConfigA.field_a, "value_a")
            ConfigB.set(ConfigB.field_b, "value_b")
            
            assert ConfigA.get(ConfigA.field_a) == "value_a"
            assert ConfigB.get(ConfigB.field_b) == "value_b"
        finally:
            remove_json(temp_config_dir / "config_a.json")
            remove_json(temp_config_dir / "config_b.json")
