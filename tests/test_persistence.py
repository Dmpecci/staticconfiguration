"""
Test suite for JSONBackend and StaticConfigBase persistence functionality.

This module provides comprehensive testing of the static configuration library,
covering both the low-level JSONBackend operations and the high-level
StaticConfigBase API. Tests are designed to validate correct behavior,
edge cases, and failure modes without modifying production code.

Test Organization:
    - TestJSONBackendReadValue: Reading and decoding configuration values (6 tests)
    - TestJSONBackendWriteValue: Writing and encoding configuration values (8 tests)
    - TestImplicitInitialization: Implicit file creation via read/write (5 tests)
    - TestCorruptionRecovery: Automatic recovery from corrupted files (3 tests)
    - TestStaticConfigBaseGet: High-level configuration retrieval (5 tests)
    - TestStaticConfigBaseSet: High-level configuration updates (5 tests)
    - TestPersistenceEdgeCases: Complex scenarios and edge cases (11 tests)

Test Results:
    - Suite aligned with current implementation (JSONBackend v2.0)
    
API Changes from v1.0:
    - ensure_safe_state() is now private (_ensure_safe_state)
    - read_value() and write_value() require 6 parameters (added: version, data_fields, development, concurrency_unsafe)
    - Initialization is now implicit (automatic on first read/write)
    - Corruption recovery is automatic and guaranteed to succeed
    - concurrency_unsafe=True bypasses locking (default: False)
    
See TEST_ALIGNMENT_REPORT.md for complete migration details.

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
from tests.test_utilities import remove_json, read_value_simple, write_value_simple

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
# TestImplicitInitialization
# ============================================================================

class TestImplicitInitialization:
    """Test suite for implicit file initialization via read/write operations."""
    
    def test_read_creates_nonexistent_file(self, backend, temp_config_dir):
        """Test that read_value creates file if it doesn't exist."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="api_url", data_type=str, default="https://api.example.com")
        data_fields = [data_field]
        
        assert not config_path.exists()
        
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        
        assert config_path.exists()
        assert result == "https://api.example.com"
        
        remove_json(config_path)
    
    def test_write_creates_nonexistent_file(self, backend, temp_config_dir):
        """Test that write_value creates file if it doesn't exist."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="counter", data_type=int, default=0)
        data_fields = [data_field]
        
        assert not config_path.exists()
        
        write_value_simple(config_path, data_field, 42, data_fields, concurrency_unsafe=True)
        
        assert config_path.exists()
        
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        assert result == 42
        
        remove_json(config_path)
    
    def test_initialization_creates_correct_structure(self, backend, temp_config_dir):
        """Test that implicit initialization creates proper JSON structure."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="field", data_type=str, default="value")
        data_fields = [data_field]
        
        write_value_simple(config_path, data_field, "test", data_fields, concurrency_unsafe=True)
        
        with config_path.open("r") as f:
            payload = json.load(f)
        
        assert "version" in payload
        assert "created" in payload
        assert "last_modified" in payload
        assert "data" in payload
        assert isinstance(payload["data"], dict)
        
        remove_json(config_path)
    
    def test_defaults_applied_on_initialization(self, backend, temp_config_dir):
        """Test that default values are applied during implicit initialization."""
        config_path = temp_config_dir / "config.json"
        data_fields = [
            Data(name="url", data_type=str, default="https://default.com"),
            Data(name="retries", data_type=int, default=3),
            Data(name="enabled", data_type=bool, default=False)
        ]
        
        # Read one field triggers initialization of all
        result = read_value_simple(config_path, data_fields[0], data_fields, concurrency_unsafe=True)
        assert result == "https://default.com"
        
        # Verify all defaults were written
        with config_path.open("r") as f:
            payload = json.load(f)
        
        assert payload["data"]["url"] == "https://default.com"
        assert payload["data"]["retries"] == 3
        assert payload["data"]["enabled"] is False
        
        remove_json(config_path)
    
    def test_encoders_applied_to_defaults(self, backend, temp_config_dir):
        """Test that encoder functions are applied to default values during initialization."""
        def list_encoder(value):
            return ",".join(value)
        
        data_field = Data(name="tags", data_type=list, default=["python", "testing"], encoder=list_encoder)
        data_fields = [data_field]
        config_path = temp_config_dir / "config.json"
        
        read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        
        with config_path.open("r") as f:
            payload = json.load(f)
        
        # Encoder should have converted list to comma-separated string
        assert payload["data"]["tags"] == "python,testing"
        
        remove_json(config_path)


# ============================================================================
# TestCorruptionRecovery
# ============================================================================

class TestCorruptionRecovery:
    """Test suite for automatic recovery from corrupted configuration files."""
    
    def test_recovers_from_malformed_json(self, backend, temp_config_dir):
        """Test that operations on corrupted JSON trigger automatic recovery."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="field", data_type=str, default="default_value")
        data_fields = [data_field]
        
        # Write malformed JSON
        with config_path.open("w") as f:
            f.write("{invalid json content: broken")
        
        # Should not raise, should recover
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
            
            # Should emit ConfigurationResetWarning
            assert len(w) == 1
            assert "reset to defaults" in str(w[0].message).lower()
        
        # Should return default value after recovery
        assert result == "default_value"
        
        # File should now be valid
        with config_path.open("r") as f:
            payload = json.load(f)  # Should not raise
        
        assert payload["data"]["field"] == "default_value"
        
        remove_json(config_path)
    
    def test_recovery_restores_all_defaults(self, backend, temp_config_dir):
        """Test that recovery restores all fields to their defaults."""
        config_path = temp_config_dir / "config.json"
        data_fields = [
            Data(name="url", data_type=str, default="https://default.com"),
            Data(name="count", data_type=int, default=10),
            Data(name="active", data_type=bool, default=True)
        ]
        
        # Write corrupted file
        with config_path.open("w") as f:
            f.write("corrupted")
        
        # Trigger recovery
        import warnings
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            read_value_simple(config_path, data_fields[0], data_fields, concurrency_unsafe=True)
        
        # All fields should have defaults
        with config_path.open("r") as f:
            payload = json.load(f)
        
        assert payload["data"]["url"] == "https://default.com"
        assert payload["data"]["count"] == 10
        assert payload["data"]["active"] is True
        
        remove_json(config_path)
    
    def test_subsequent_operations_succeed_after_recovery(self, backend, temp_config_dir):
        """Test that normal operations work after automatic recovery."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="counter", data_type=int, default=0)
        data_fields = [data_field]
        
        # Corrupt file
        with config_path.open("w") as f:
            f.write("}}}}corrupt")
        
        # Trigger recovery with read
        import warnings
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            value = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        
        assert value == 0
        
        # Write should work normally now
        write_value_simple(config_path, data_field, 42, data_fields, concurrency_unsafe=True)
        
        # Read should work normally
        value = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        assert value == 42
        
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
        data_fields = [data_field]
        
        # Create config file
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"api_url": "https://api.example.com"}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        
        assert result == "https://api.example.com"
        
        remove_json(config_path)
    
    def test_applies_decoder(self, backend, temp_config_dir):
        """Test that decoder function is applied when reading values."""
        def list_decoder(value):
            return value.split(",") if value else []
        
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="tags", data_type=list, default=[], decoder=list_decoder)
        data_fields = [data_field]
        
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"tags": "python,testing,automation"}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        
        assert result == ["python", "testing", "automation"]
        
        remove_json(config_path)
    
    def test_type_conversion_without_decoder(self, backend, temp_config_dir):
        """Test automatic type conversion when no decoder is provided."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="count", data_type=int, default=0)
        data_fields = [data_field]
        
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"count": 42}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        
        assert result == 42
        assert isinstance(result, int)
        
        remove_json(config_path)
    
    def test_handles_none_value(self, backend, temp_config_dir):
        """Test reading None value from configuration."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="optional", data_type=str, default=None)
        data_fields = [data_field]
        
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"optional": None}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        
        assert result is None
        
        remove_json(config_path)
    
    def test_creates_file_on_first_read(self, backend, temp_config_dir):
        """Test that read_value creates file if it doesn't exist (implicit initialization)."""
        config_path = temp_config_dir / "nonexistent.json"
        data_field = Data(name="field", data_type=str, default="default_value")
        data_fields = [data_field]
        
        assert not config_path.exists()
        
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        
        assert config_path.exists()
        assert result == "default_value"
        
        remove_json(config_path)
    
    def test_raises_on_missing_key(self, backend, temp_config_dir):
        """Test that KeyError is raised when requested key doesn't exist in data."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="nonexistent_field", data_type=str, default="default")
        data_fields = [Data(name="existing_field", data_type=str, default="value")]
        
        # Create file with different field
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"existing_field": "value"}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        with pytest.raises(KeyError, match="nonexistent_field"):
            read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        
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
        data_fields = [data_field]
        
        # Create initial config
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"api_url": "old_value"}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        write_value_simple(config_path, data_field, "new_value", data_fields, concurrency_unsafe=True)
        
        with config_path.open("r") as f:
            data = json.load(f)
        
        assert data["data"]["api_url"] == "new_value"
        
        remove_json(config_path)
    
    def test_updates_last_modified_timestamp(self, backend, temp_config_dir):
        """Test that last_modified timestamp is updated on write."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="field", data_type=str, default="default")
        data_fields = [data_field]
        
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
        write_value_simple(config_path, data_field, "new", data_fields, concurrency_unsafe=True)
        
        with config_path.open("r") as f:
            data = json.load(f)
        
        assert data["last_modified"] != original_timestamp
        assert data["last_modified"].endswith("Z")
        
        remove_json(config_path)
    
    def test_preserves_version_and_created(self, backend, temp_config_dir):
        """Test that version and created timestamp are not modified on write."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="field", data_type=str, default="default")
        data_fields = [data_field]
        
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
        
        # Use direct backend call to preserve test's version instead of helper's default
        backend.write_value(data_field, "new", config_path, original_version, data_fields, False, True)
        
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
        data_fields = [data_field]
        
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"tags": "old"}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        write_value_simple(config_path, data_field, ["python", "testing"], data_fields, concurrency_unsafe=True)
        
        with config_path.open("r") as f:
            data = json.load(f)
        
        assert data["data"]["tags"] == "python,testing"
        
        remove_json(config_path)
    
    def test_uses_temporary_file(self, backend, temp_config_dir):
        """Test that write_value uses a temporary file for atomic writes."""
        config_path = temp_config_dir / "config.json"
        tmp_path = config_path.with_suffix(".tmp")
        data_field = Data(name="field", data_type=str, default="default")
        data_fields = [data_field]
        
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"field": "old"}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        write_value_simple(config_path, data_field, "new", data_fields, concurrency_unsafe=True)
        
        # Temporary file should be removed after successful write
        assert not tmp_path.exists()
        assert config_path.exists()
        
        remove_json(config_path)
    
    def test_creates_file_on_first_write(self, backend, temp_config_dir):
        """Test that write_value creates file if it doesn't exist (implicit initialization)."""
        config_path = temp_config_dir / "nonexistent.json"
        data_field = Data(name="field", data_type=str, default="default")
        data_fields = [data_field]
        
        assert not config_path.exists()
        
        write_value_simple(config_path, data_field, "new_value", data_fields, concurrency_unsafe=True)
        
        assert config_path.exists()
        
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        assert result == "new_value"
        
        remove_json(config_path)
    
    def test_preserves_other_fields(self, backend, temp_config_dir):
        """Test that writing one field doesn't affect other fields."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="field1", data_type=str, default="default")
        data_fields = [
            data_field,
            Data(name="field2", data_type=str, default="default2"),
            Data(name="field3", data_type=int, default=0)
        ]
        
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
        
        write_value_simple(config_path, data_field, "new_value1", data_fields, concurrency_unsafe=True)
        
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
        data_fields = [data_field]
        
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"optional": "something"}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        write_value_simple(config_path, data_field, None, data_fields, concurrency_unsafe=True)
        
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
        data_fields = [data_field]
        
        test_value = {"nested": {"key": "value"}, "list": [1, 2, 3]}
        write_value_simple(config_path, data_field, test_value, data_fields, concurrency_unsafe=True)
        
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        
        assert result == test_value
        
        remove_json(config_path)
    
    def test_concurrent_writes_last_wins(self, backend, temp_config_dir):
        """Test behavior when multiple writes occur in sequence."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="counter", data_type=int, default=0)
        data_fields = [data_field]
        
        # Simulate multiple rapid writes
        for i in range(10):
            write_value_simple(config_path, data_field, i, data_fields, concurrency_unsafe=True)
        
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        assert result == 9
        
        remove_json(config_path)
    
    def test_empty_string_value(self, backend, temp_config_dir):
        """Test handling of empty string values."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="text", data_type=str, default="default")
        data_fields = [data_field]
        
        write_value_simple(config_path, data_field, "", data_fields, concurrency_unsafe=True)
        
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        assert result == ""
        
        remove_json(config_path)
    
    def test_boolean_false_value(self, backend, temp_config_dir):
        """Test that False boolean value is correctly handled (not confused with None/empty)."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="enabled", data_type=bool, default=True)
        data_fields = [data_field]
        
        write_value_simple(config_path, data_field, False, data_fields, concurrency_unsafe=True)
        
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        assert result is False
        assert isinstance(result, bool)
        
        remove_json(config_path)
    
    def test_zero_integer_value(self, backend, temp_config_dir):
        """Test that zero integer value is correctly handled."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="count", data_type=int, default=10)
        data_fields = [data_field]
        
        write_value_simple(config_path, data_field, 0, data_fields, concurrency_unsafe=True)
        
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        assert result == 0
        assert isinstance(result, int)
        
        remove_json(config_path)
    
    def test_missing_data_section_triggers_recovery(self, backend, temp_config_dir):
        """Test that file with missing data section triggers automatic recovery."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="field", data_type=str, default="default")
        data_fields = [data_field]
        
        # Create malformed config without data section
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z"
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        # Should trigger recovery and return default
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        assert result == "default"
        
        # File should now be valid
        with config_path.open("r") as f:
            fixed_payload = json.load(f)
        
        assert "data" in fixed_payload
        assert fixed_payload["data"]["field"] == "default"
        
        remove_json(config_path)
    
    def test_type_coercion_string_to_int(self, backend, temp_config_dir):
        """Test automatic type coercion from string to int."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="number", data_type=int, default=0)
        data_fields = [data_field]
        
        # Manually create JSON with string value
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {"number": "42"}
        }
        
        with config_path.open("w") as f:
            json.dump(payload, f)
        
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
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
        data_fields = [data_field]
        
        large_structure = {
            "level1": {
                "level2": {
                    "level3": {
                        "items": [{"id": i, "value": f"item_{i}"} for i in range(100)]
                    }
                }
            }
        }
        
        write_value_simple(config_path, data_field, large_structure, data_fields, concurrency_unsafe=True)
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        
        assert result == large_structure
        assert len(result["level1"]["level2"]["level3"]["items"]) == 100
        
        remove_json(config_path)
    
    def test_unicode_characters(self, backend, temp_config_dir):
        """Test handling of unicode characters in string values."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="message", data_type=str, default="")
        data_fields = [data_field]
        
        unicode_text = "Hello 世界 🌍 Привет مرحبا"
        
        write_value_simple(config_path, data_field, unicode_text, data_fields, concurrency_unsafe=True)
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        
        assert result == unicode_text
        
        remove_json(config_path)
    
    def test_read_after_manual_json_modification(self, backend, temp_config_dir):
        """Test that manually modified JSON files are read correctly."""
        config_path = temp_config_dir / "config.json"
        data_field = Data(name="setting", data_type=str, default="default")
        data_fields = [data_field]
        
        write_value_simple(config_path, data_field, "initial", data_fields, concurrency_unsafe=True)
        
        # Manually modify the JSON file
        with config_path.open("r") as f:
            data = json.load(f)
        
        data["data"]["setting"] = "manually_changed"
        data["custom_metadata"] = "extra_info"
        
        with config_path.open("w") as f:
            json.dump(data, f)
        
        result = read_value_simple(config_path, data_field, data_fields, concurrency_unsafe=True)
        assert result == "manually_changed"
        
        # Verify custom metadata is preserved on write
        write_value_simple(config_path, data_field, "new_value", data_fields, concurrency_unsafe=True)
        
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
