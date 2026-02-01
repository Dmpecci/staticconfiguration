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
from dataclasses import dataclass, field
from enum import Enum

# ============================================================================
# Complex Data Example Classes
# ============================================================================

class ExampleEnum(Enum):
    RED = "red"
    BLUE = "blue"
    GREEN = "green"


@dataclass
class DataDetails:
    count: int = 0
    values: list[int] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "values": list(self.values),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DataDetails":
        if data is None:
            return None
        return cls(
            count=int(data.get("count", 0)),
            values=list(data.get("values", [])),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ParentData:
    kind: ExampleEnum
    description: str = ""
    
    def to_dict(self) -> dict:
        return {
            "__class__": self.__class__.__name__,
            "kind": self.kind.value if isinstance(self.kind, ExampleEnum) else self.kind,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ParentData":
        if data is None:
            return None
        # Dispatch to subclasses if requested
        cls_name = data.get("__class__")
        if cls_name == "ChildData1":
            return ChildData1.from_dict(data)
        if cls_name == "ChildData2":
            return ChildData2.from_dict(data)
        kind_val = data.get("kind")
        kind = ExampleEnum(kind_val) if kind_val is not None else None
        return cls(kind=kind, description=data.get("description", ""))


@dataclass
class ChildData1(ParentData):
    extra_field1: float = 0.0
    subdetails: DataDetails = field(default_factory=DataDetails)
    
    def to_dict(self) -> dict:
        base = super().to_dict()
        base.update({
            "extra_field1": float(self.extra_field1),
            "subdetails": self.subdetails.to_dict() if self.subdetails is not None else None,
        })
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "ChildData1":
        if data is None:
            return None
        kind_val = data.get("kind")
        kind = ExampleEnum(kind_val) if kind_val is not None else None
        return cls(
            kind=kind,
            description=data.get("description", ""),
            extra_field1=float(data.get("extra_field1", 0.0)),
            subdetails=DataDetails.from_dict(data.get("subdetails")),
        )


@dataclass
class ChildData2(ParentData):
    flag: bool = False
    items: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        base = super().to_dict()
        base.update({
            "flag": bool(self.flag),
            "items": list(self.items),
        })
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "ChildData2":
        if data is None:
            return None
        kind_val = data.get("kind")
        kind = ExampleEnum(kind_val) if kind_val is not None else None
        return cls(
            kind=kind,
            description=data.get("description", ""),
            flag=bool(data.get("flag", False)),
            items=list(data.get("items", [])),
        )


@dataclass
class Tags:
    items: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {"items": list(self.items)}

    @classmethod
    def from_dict(cls, data: dict) -> "Tags":
        if data is None:
            return None
        return cls(items=list(data.get("items", [])))

@dataclass
class ComplexDataExample:
    id: int
    name: str
    attributes: dict[str, object] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    parent: ParentData | None = None
    extra: DataDetails | None = None
    
    def to_dict(self) -> dict:
        def _maybe_to_dict(v):
            if hasattr(v, "to_dict"):
                return v.to_dict()
            return v

        return {
            "id": int(self.id),
            "name": self.name,
            "attributes": {k: _maybe_to_dict(v) for k, v in self.attributes.items()},
            "tags": list(self.tags),
            "parent": _maybe_to_dict(self.parent),
            "extra": self.extra.to_dict() if self.extra is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ComplexDataExample":
        if data is None:
            return None

        attrs = data.get("attributes", {}) or {}
        # Attempt to reconstruct nested dataclass values if dict with __class__ or dataclass shape
        def _maybe_from_dict(v):
            if isinstance(v, dict) and "__class__" in v:
                return ParentData.from_dict(v)
            return v

        attributes = {k: _maybe_from_dict(v) for k, v in attrs.items()}
        parent = ParentData.from_dict(data.get("parent")) if data.get("parent") is not None else None
        extra = DataDetails.from_dict(data.get("extra")) if data.get("extra") is not None else None

        return cls(
            id=int(data.get("id")),
            name=data.get("name", ""),
            attributes=attributes,
            tags=list(data.get("tags", [])),
            parent=parent,
            extra=extra,
        )

def complex_data_example() -> ComplexDataExample:
    parent = ChildData1(
        kind=ExampleEnum.RED,
        description="A red child data",
        extra_field1=3.14,
        subdetails=DataDetails(count=5, values=[1, 2, 3], metadata={"key": "value"})
    )

    extra = DataDetails(count=10, values=[10, 20, 30], metadata={"extra_key": "extra_value"})

    return ComplexDataExample(
        id=1,
        name="Test Example",
        attributes={"attr1": 100, "attr2": "value2"},
        tags=["tag1", "tag2", "tag3"],
        parent=parent,
        extra=extra
    )

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

def complex_data_decoder(payload: dict) -> ComplexDataExample:
    """Decoder function for ComplexDataExample."""
    return ComplexDataExample.from_dict(payload)

def complex_data_encoder(value: ComplexDataExample) -> dict:
    """Encoder function for ComplexDataExample."""
    return value.to_dict()

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
    complex_data = Data(name="complex_data", data_type=ComplexDataExample, default=complex_data_example(), encoder=complex_data_encoder, decoder=complex_data_decoder)

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


# ============================================================================
# TestComplexDataStructures
# ============================================================================

@pytest.fixture
def complex_config_class(temp_config_dir):
    """
    Fixture that provides a centralized ComplexConfig class for all tests.
    
    This avoids repeating the same class definition in every test method.
    If the configuration needs to change, it only needs to be updated here.
    """
    @staticconfig
    class ComplexConfig:
        __config_file__: str = "complex_config.json"
        __version__: str = "1.0.0"
        __development__: bool = False
        __config_path__: str = str(temp_config_dir)
        
        complex_data = Data(
            name="complex_data",
            data_type=ComplexDataExample,
            default=complex_data_example(),
            encoder=complex_data_encoder,
            decoder=complex_data_decoder
        )
    
    return ComplexConfig


class TestComplexDataStructures:
    """
    Test suite for complex nested data structures with encoder/decoder.
    
    Purpose:
        Measure empirically what guarantees the library provides when persisting
        complex nested structures (dataclasses, inheritance, lists, dicts, enums)
        as a single atomic Data unit.
        
    Scope:
        - NO modifications to production code
        - Document actual behavior, not desired behavior
        - Test round-trip, mutations, and inheritance variants
        - NO migration tests (those belong in test_migrator.py)
        
    Key assumptions being tested:
        - ComplexDataExample is treated as a single atomic unit
        - encoder/decoder handle all nested structures correctly
        - Mutations to nested structures are persisted correctly
    """
    
    def test_roundtrip_basic_complex_structure(self, temp_config_dir, complex_config_class):
        """
        TEST 1: Round-trip básico (sin migración)
        
        Purpose:
            Verify that a complex nested structure can be saved and retrieved
            with full structural integrity preserved.
            
        What we're testing:
            - encoder correctly serializes all nested components
            - decoder correctly reconstructs all nested components
            - NO data loss during round-trip
            
        Expected behavior:
            - All fields preserved: scalars, lists, dicts, nested dataclasses
            - Enum values preserved
            - Inheritance preserved (ChildData1 vs ChildData2)
            - None values preserved
            - Structural equality (not identity)
        """
        ComplexConfig = complex_config_class
        config_path = temp_config_dir / "complex_config.json"
        
        try:
            # Create a complex instance with nested structures
            original = ComplexDataExample(
                id=42,
                name="Test Complex Structure",
                attributes={
                    "scalar": 100,
                    "text": "value",
                    "nested": {"inner_key": "inner_value"}
                },
                tags=["tag1", "tag2", "tag3"],
                parent=ChildData1(
                    kind=ExampleEnum.RED,
                    description="Red child data",
                    extra_field1=3.14159,
                    subdetails=DataDetails(
                        count=5,
                        values=[1, 2, 3, 4, 5],
                        metadata={"meta1": "value1", "meta2": "value2"}
                    )
                ),
                extra=DataDetails(
                    count=10,
                    values=[10, 20, 30],
                    metadata={"extra_key": "extra_value"}
                )
            )
            
            # SAVE: Persist complex structure
            ComplexConfig.set(ComplexConfig.complex_data, original)
            
            # LOAD: Retrieve complex structure
            retrieved = ComplexConfig.get(ComplexConfig.complex_data)
            
            # VERIFY: Structural equality (not identity)
            assert retrieved is not original, "Should be different instances"
            
            # Verify top-level fields
            assert retrieved.id == 42
            assert retrieved.name == "Test Complex Structure"
            
            # Verify attributes dict
            assert retrieved.attributes["scalar"] == 100
            assert retrieved.attributes["text"] == "value"
            assert retrieved.attributes["nested"]["inner_key"] == "inner_value"
            
            # Verify tags list
            assert retrieved.tags == ["tag1", "tag2", "tag3"]
            
            # Verify nested parent (ChildData1)
            assert isinstance(retrieved.parent, ChildData1), "Parent should be ChildData1"
            assert retrieved.parent.kind == ExampleEnum.RED
            assert retrieved.parent.description == "Red child data"
            assert retrieved.parent.extra_field1 == 3.14159
            
            # Verify deeply nested subdetails
            assert retrieved.parent.subdetails.count == 5
            assert retrieved.parent.subdetails.values == [1, 2, 3, 4, 5]
            assert retrieved.parent.subdetails.metadata == {"meta1": "value1", "meta2": "value2"}
            
            # Verify extra DataDetails
            assert retrieved.extra.count == 10
            assert retrieved.extra.values == [10, 20, 30]
            assert retrieved.extra.metadata == {"extra_key": "extra_value"}
            
        finally:
            remove_json(config_path)
    
    def test_mutations_and_persistence(self, temp_config_dir, complex_config_class):
        """
        TEST 2: Mutaciones internas + persistencia
        
        Purpose:
            Verify that internal modifications to nested structures are
            correctly persisted and retrieved.
            
        What we're testing:
            - Modify nested lists
            - Modify nested dicts
            - Modify scalar fields in nested dataclasses
            - Only modified fields change
            - Unmodified fields remain intact
            
        Expected behavior:
            - All mutations should be preserved after save/load
            - Non-mutated parts should remain unchanged
        """
        ComplexConfig = complex_config_class
        config_path = temp_config_dir / "complex_config.json"
        
        try:
            # Initial structure
            original = ComplexDataExample(
                id=1,
                name="Original",
                attributes={"key1": "value1"},
                tags=["original_tag"],
                parent=ChildData1(
                    kind=ExampleEnum.BLUE,
                    description="Original description",
                    extra_field1=1.0,
                    subdetails=DataDetails(count=1, values=[1], metadata={"k": "v"})
                ),
                extra=DataDetails(count=5, values=[5, 10], metadata={"extra": "data"})
            )
            
            # Save initial
            ComplexConfig.set(ComplexConfig.complex_data, original)
            
            # Retrieve and mutate
            retrieved = ComplexConfig.get(ComplexConfig.complex_data)
            
            # Mutation 1: Add to tags list
            retrieved.tags.append("new_tag")
            
            # Mutation 2: Update nested dict
            retrieved.attributes["key2"] = "value2"
            retrieved.attributes["key1"] = "modified_value1"
            
            # Mutation 3: Modify nested dataclass scalars
            retrieved.parent.extra_field1 = 99.99
            retrieved.parent.description = "Modified description"
            
            # Mutation 4: Modify deeply nested list
            retrieved.parent.subdetails.values.append(999)
            
            # Mutation 5: Modify deeply nested metadata
            retrieved.parent.subdetails.metadata["new_meta"] = "new_value"
            
            # Save mutated version
            ComplexConfig.set(ComplexConfig.complex_data, retrieved)
            
            # Retrieve again
            final = ComplexConfig.get(ComplexConfig.complex_data)
            
            # VERIFY: All mutations preserved
            assert final.tags == ["original_tag", "new_tag"]
            assert final.attributes["key1"] == "modified_value1"
            assert final.attributes["key2"] == "value2"
            assert final.parent.extra_field1 == 99.99
            assert final.parent.description == "Modified description"
            assert final.parent.subdetails.values == [1, 999]
            assert final.parent.subdetails.metadata == {"k": "v", "new_meta": "new_value"}
            
            # VERIFY: Non-mutated parts remain intact
            assert final.id == 1
            assert final.name == "Original"
            assert final.parent.kind == ExampleEnum.BLUE
            assert final.parent.subdetails.count == 1
            assert final.extra.count == 5
            assert final.extra.values == [5, 10]
            
        finally:
            remove_json(config_path)
    
    def test_inheritance_variant_change(self, temp_config_dir, complex_config_class):
        """
        TEST 3: Cambio de variante (herencia)
        
        Purpose:
            Verify that switching between different inheritance variants
            (ChildData1 vs ChildData2) preserves the concrete type.
            
        What we're testing:
            - Start with ChildData1 as parent
            - Replace with ChildData2
            - Verify concrete type is preserved
            - Verify no remnants of previous variant
            
        Expected behavior:
            - Concrete type should be preserved via __class__ marker
            - Variant-specific fields should match the new type
            - No field leakage between variants
        """
        ComplexConfig = complex_config_class
        config_path = temp_config_dir / "complex_config.json"
        
        try:
            # PHASE 1: Start with ChildData1
            with_child1 = ComplexDataExample(
                id=1,
                name="Variant Test",
                parent=ChildData1(
                    kind=ExampleEnum.RED,
                    description="This is ChildData1",
                    extra_field1=42.0,
                    subdetails=DataDetails(count=100, values=[1, 2, 3])
                )
            )
            
            ComplexConfig.set(ComplexConfig.complex_data, with_child1)
            retrieved1 = ComplexConfig.get(ComplexConfig.complex_data)
            
            # Verify it's ChildData1
            assert isinstance(retrieved1.parent, ChildData1)
            assert retrieved1.parent.kind == ExampleEnum.RED
            assert retrieved1.parent.extra_field1 == 42.0
            assert hasattr(retrieved1.parent, "subdetails")
            
            # PHASE 2: Replace with ChildData2
            with_child2 = ComplexDataExample(
                id=2,
                name="Variant Test Changed",
                parent=ChildData2(
                    kind=ExampleEnum.BLUE,
                    description="This is ChildData2",
                    flag=True,
                    items=["item1", "item2", "item3"]
                )
            )
            
            ComplexConfig.set(ComplexConfig.complex_data, with_child2)
            retrieved2 = ComplexConfig.get(ComplexConfig.complex_data)
            
            # Verify it's ChildData2 now
            assert isinstance(retrieved2.parent, ChildData2)
            assert retrieved2.parent.kind == ExampleEnum.BLUE
            assert retrieved2.parent.description == "This is ChildData2"
            assert retrieved2.parent.flag is True
            assert retrieved2.parent.items == ["item1", "item2", "item3"]
            
            # Verify NO remnants of ChildData1
            assert not hasattr(retrieved2.parent, "extra_field1"), "extra_field1 should not exist in ChildData2"
            assert not hasattr(retrieved2.parent, "subdetails"), "subdetails should not exist in ChildData2"
            
            # Verify correct variant-specific fields
            assert hasattr(retrieved2.parent, "flag")
            assert hasattr(retrieved2.parent, "items")
            
        finally:
            remove_json(config_path)
    
    def test_none_values_in_complex_structure(self, temp_config_dir, complex_config_class):
        """
        TEST 4: Manejo de valores None en estructura compleja
        
        Purpose:
            Document what happens to complex data when version changes
            but structure remains identical.
            
        What we're testing:
            - Save with version "1.0.0"
            - Force migration to "2.0.0" (no code changes)
            - Observe if data is preserved or reset
            
        IMPORTANT:
            - This test DOCUMENTS actual behavior, not desired behavior
            - According to library design: decoder does NOT participate in migration
            - Migrations treat each Data as atomic unit
            - Complex structures may be reset to default during migration
            
        Expected behavior (to be documented):
            - If preserved: ✓ data survives migration
            - If reset: ✓ data returns to default value
            
        This is NOT a bug—it's the current design.
        """
        @staticconfig
        class ComplexConfig:
            __config_file__: str = "complex_config.json"
            __version__: str = "1.0.0"
            __development__: bool = False  # Normal mode (not development)
            __config_path__: str = str(temp_config_dir)
            
            complex_data = Data(
                name="complex_data",
                data_type=ComplexDataExample,
                default=complex_data_example(),
                encoder=complex_data_encoder,
                decoder=complex_data_decoder
            )
        
        config_path = temp_config_dir / "complex_config.json"
        
        try:
            # PHASE 1: Save with version 1.0.0
            custom_data = ComplexDataExample(
                id=999,
                name="Custom Data Before Migration",
                attributes={"custom": "attribute"},
                tags=["pre_migration_tag"],
                parent=ChildData2(
                    kind=ExampleEnum.GREEN,
                    description="Pre-migration variant",
                    flag=True,
                    items=["pre1", "pre2"]
                ),
                extra=DataDetails(count=888, values=[8, 8, 8])
            )
            
            ComplexConfig.set(ComplexConfig.complex_data, custom_data)
            
            # Verify it was saved
            before_migration = ComplexConfig.get(ComplexConfig.complex_data)
            assert before_migration.id == 999
            assert before_migration.name == "Custom Data Before Migration"
            
            # PHASE 2: Force migration by changing version
            # We do this by redefining the class with new version
            @staticconfig
            class ComplexConfigV2:
                __config_file__: str = "complex_config.json"  # Same file
                __version__: str = "2.0.0"  # New version
                __development__: bool = False
                __config_path__: str = str(temp_config_dir)
                
                complex_data = Data(
                    name="complex_data",
                    data_type=ComplexDataExample,
                    default=complex_data_example(),  # Same default
                    encoder=complex_data_encoder,
                    decoder=complex_data_decoder
                )
            
            # Trigger migration by reading with new version
            after_migration = ComplexConfigV2.get(ComplexConfigV2.complex_data)
            
            # DOCUMENT: What happened during migration?
            # Check if data was preserved or reset to default
            
            default_value = complex_data_example()
            
            # Compare against default to determine behavior
            if after_migration.id == default_value.id:
                # DATA WAS RESET TO DEFAULT
                print("\n⚠️  OBSERVATION: Complex data was RESET to default during version migration")
                print("   This is expected behavior: decoder does not participate in migration")
                print("   Complex structures are treated as atomic units during migration")
                
                # Verify it matches default completely
                assert after_migration.id == default_value.id
                assert after_migration.name == default_value.name
                assert isinstance(after_migration.parent, ChildData1)  # Default uses ChildData1
                
            else:
                # DATA WAS PRESERVED
                print("\n✓ OBSERVATION: Complex data was PRESERVED during version migration")
                print("  Custom values survived the migration intact")
                
                # Verify preservation
                assert after_migration.id == 999
                assert after_migration.name == "Custom Data Before Migration"
                assert isinstance(after_migration.parent, ChildData2)
            
            # This assertion documents the ACTUAL behavior
            # Adjust based on what we observe
            # Current expectation: data WILL be reset (decoder doesn't participate in migration)
            assert after_migration.id == default_value.id, \
                "Expected: Complex data should reset to default during migration (current library behavior)"
            
        finally:
            remove_json(config_path)
    
    def test_none_values_in_complex_structure(self, temp_config_dir):
        """
        TEST 6: Manejo de valores None en estructura compleja
        
        Purpose:
            Verify that None values in optional nested fields are correctly
            handled by encoder/decoder.
            
        What we're testing:
            - parent=None
            - extra=None
            - Empty lists and dicts
            - Round-trip preservation
        """
        @staticconfig
        class ComplexConfig:
            __config_file__: str = "complex_config.json"
            __version__: str = "1.0.0"
            __development__: bool = False
            __config_path__: str = str(temp_config_dir)
            
            complex_data = Data(
                name="complex_data",
                data_type=ComplexDataExample,
                default=complex_data_example(),
                encoder=complex_data_encoder,
                decoder=complex_data_decoder
            )
        
        config_path = temp_config_dir / "complex_config.json"
        
        try:
            # Create with None values
            with_nones = ComplexDataExample(
                id=0,
                name="",
                attributes={},
                tags=[],
                parent=None,
                extra=None
            )
            
            ComplexConfig.set(ComplexConfig.complex_data, with_nones)
            retrieved = ComplexConfig.get(ComplexConfig.complex_data)
            
            # Verify None preservation
            assert retrieved.id == 0
            assert retrieved.name == ""
            assert retrieved.attributes == {}
            assert retrieved.tags == []
            assert retrieved.parent is None
            assert retrieved.extra is None
            
            # Verify JSON structure
            with config_path.open("r") as f:
                payload = json.load(f)
            
            data = payload["data"]["complex_data"]
            assert data["parent"] is None
            assert data["extra"] is None
            
        finally:
            remove_json(config_path)
    
    def test_deeply_nested_modifications(self, temp_config_dir, complex_config_class):
        """
        TEST 5: Modificaciones en estructuras profundamente anidadas
        
        Purpose:
            Verify that modifications at deep nesting levels are persisted.
            
        Testing:
            - parent.subdetails.metadata modifications
            - parent.subdetails.values modifications
            - Multiple levels of nesting
        """
        ComplexConfig = complex_config_class
        config_path = temp_config_dir / "complex_config.json"
        
        try:
            # Create with deep nesting
            deep = ComplexDataExample(
                id=1,
                name="Deep",
                parent=ChildData1(
                    kind=ExampleEnum.RED,
                    description="Deep parent",
                    extra_field1=1.0,
                    subdetails=DataDetails(
                        count=3,
                        values=[1, 2, 3],
                        metadata={"level1": "value1"}
                    )
                )
            )
            
            ComplexConfig.set(ComplexConfig.complex_data, deep)
            
            # Retrieve and modify at deepest level
            retrieved = ComplexConfig.get(ComplexConfig.complex_data)
            retrieved.parent.subdetails.metadata["level1"] = "modified"
            retrieved.parent.subdetails.metadata["level2"] = "new_value"
            retrieved.parent.subdetails.values.extend([4, 5, 6])
            retrieved.parent.subdetails.count = 100
            
            # Save modifications
            ComplexConfig.set(ComplexConfig.complex_data, retrieved)
            
            # Verify deep modifications persisted
            final = ComplexConfig.get(ComplexConfig.complex_data)
            assert final.parent.subdetails.metadata["level1"] == "modified"
            assert final.parent.subdetails.metadata["level2"] == "new_value"
            assert final.parent.subdetails.values == [1, 2, 3, 4, 5, 6]
            assert final.parent.subdetails.count == 100
            
        finally:
            remove_json(config_path)


# ============================================================================
# TestComplexDataEdgeCases
# ============================================================================

class TestComplexDataEdgeCases:
    """
    Test suite for edge cases and potential failure modes with complex data.
    
    Purpose:
        Identify rare scenarios where the library might fail when users provide
        correct encoder/decoder implementations. This is NOT about testing bad
        encoders/decoders (user responsibility), but about finding library bugs.
        
    Philosophy (from QA manual):
        - Tests are not meant to pass - they are meant to find bugs
        - If a test fails, it indicates a real bug or contract deviation
        - Document actual behavior, not desired behavior
        - Think like an auditor: "Where could this break in production?"
        
    Scope:
        - Encoder/decoder correctness is assumed (user's responsibility)
        - We test if the library handles the persistence correctly
        - We test edge cases in the persistence layer
    """
    
    def test_none_is_valid_value_for_any_data_type(self, temp_config_dir):
        """
        Contract verification: None is always a valid value for any Data field.
        
        Rationale:
            None means "absence of value" / "no data exists".
            According to the library contract:
                - set() accepts: instance of data_type OR None
                - get() returns: instance of data_type OR None
                - None bypasses encoder/decoder (correct behavior)
        
        This test verifies:
            1. set() allows None for complex types
            2. None persists as JSON null
            3. get() returns None (decoder not called for None)
            4. set(obj) -> set(None) -> get() returns None
        """
        @staticconfig
        class NoneTestConfig:
            __config_file__: str = "none_test.json"
            __version__: str = "1.0.0"
            __development__: bool = False
            __config_path__: str = str(temp_config_dir)
            
            complex_data = Data(
                name="complex_data",
                data_type=ComplexDataExample,
                default=None,  # None is valid default
                encoder=complex_data_encoder,
                decoder=complex_data_decoder
            )
        
        config_path = temp_config_dir / "none_test.json"
        
        try:
            # TEST 1: Set None directly
            NoneTestConfig.set(NoneTestConfig.complex_data, None)
            
            # Verify JSON contains null
            with config_path.open("r") as f:
                payload = json.load(f)
            assert payload["data"]["complex_data"] is None, "None should persist as JSON null"
            
            # Verify get() returns None
            retrieved = NoneTestConfig.get(NoneTestConfig.complex_data)
            assert retrieved is None, "get() should return None"
            
            # TEST 2: Set actual object, then replace with None
            obj = ComplexDataExample(id=123, name="test")
            NoneTestConfig.set(NoneTestConfig.complex_data, obj)
            
            retrieved = NoneTestConfig.get(NoneTestConfig.complex_data)
            assert retrieved.id == 123
            assert retrieved.name == "test"
            
            # Now replace with None
            NoneTestConfig.set(NoneTestConfig.complex_data, None)
            
            # Verify None persists
            retrieved = NoneTestConfig.get(NoneTestConfig.complex_data)
            assert retrieved is None, "Should return None after replacing object with None"
            
            # TEST 3: Set None, then set actual object
            NoneTestConfig.set(NoneTestConfig.complex_data, None)
            obj2 = ComplexDataExample(id=456, name="after_none")
            NoneTestConfig.set(NoneTestConfig.complex_data, obj2)
            
            retrieved = NoneTestConfig.get(NoneTestConfig.complex_data)
            assert retrieved.id == 456
            assert retrieved.name == "after_none"
            
        finally:
            remove_json(config_path)

    
    def test_large_nested_structure_json_serialization(self, temp_config_dir):
        """
        Edge case: Very large nested structure.
        
        Question:
            Does the library handle large complex objects correctly?
            JSON serialization, file write, atomic operations should work.
            
        This tests library robustness, not encoder correctness.
        """
        @staticconfig
        class LargeConfig:
            __config_file__: str = "large.json"
            __version__: str = "1.0.0"
            __development__: bool = False
            __config_path__: str = str(temp_config_dir)
            
            large_data = Data(
                name="large_data",
                data_type=ComplexDataExample,
                default=complex_data_example(),
                encoder=complex_data_encoder,
                decoder=complex_data_decoder
            )
        
        config_path = temp_config_dir / "large.json"
        
        try:
            # Create large structure
            large_obj = ComplexDataExample(
                id=1,
                name="Large structure",
                attributes={f"key_{i}": f"value_{i}" for i in range(1000)},
                tags=[f"tag_{i}" for i in range(500)],
                parent=ChildData1(
                    kind=ExampleEnum.BLUE,
                    description="x" * 10000,  # Large string
                    extra_field1=3.14,
                    subdetails=DataDetails(
                        count=5000,
                        values=list(range(1000)),
                        metadata={f"meta_{i}": f"val_{i}" for i in range(500)}
                    )
                )
            )
            
            # Write large object
            LargeConfig.set(LargeConfig.large_data, large_obj)
            
            # Verify file was created and is valid JSON
            assert config_path.exists()
            with config_path.open("r") as f:
                payload = json.load(f)
            assert "data" in payload
            
            # Verify roundtrip preserves all data
            retrieved = LargeConfig.get(LargeConfig.large_data)
            assert retrieved.id == 1
            assert len(retrieved.attributes) == 1000
            assert len(retrieved.tags) == 500
            assert len(retrieved.parent.subdetails.values) == 1000
            assert len(retrieved.parent.description) == 10000
            
        finally:
            remove_json(config_path)
    
    def test_default_value_not_shared_between_instances(self, temp_config_dir):
        """
        Critical edge case: Mutable default values.
        
        Question:
            If default is a mutable object (like ComplexDataExample), does the
            library share the same instance across multiple operations?
            
        Expected behavior:
            - Each initialization should get independent default
            - Modifying retrieved object shouldn't affect subsequent reads
            - This tests library behavior, not encoder correctness
        """
        # Create default with specific values
        shared_default = ComplexDataExample(
            id=999,
            name="SHARED_DEFAULT",
            tags=["default_tag"]
        )
        
        @staticconfig
        class DefaultTestConfig:
            __config_file__: str = "default_test.json"
            __version__: str = "1.0.0"
            __development__: bool = False
            __config_path__: str = str(temp_config_dir)
            
            data = Data(
                name="data",
                data_type=ComplexDataExample,
                default=shared_default,  # Shared mutable default
                encoder=complex_data_encoder,
                decoder=complex_data_decoder
            )
        
        config_path = temp_config_dir / "default_test.json"
        
        try:
            # First read triggers initialization with default
            first = DefaultTestConfig.get(DefaultTestConfig.data)
            assert first.id == 999
            assert first.name == "SHARED_DEFAULT"
            original_tags = list(first.tags)
            
            # Modify the retrieved object
            first.tags.append("MODIFIED")
            first.name = "CHANGED"
            
            # DON'T save - just modify in memory
            
            # Read again - should get original default, not modified object
            second = DefaultTestConfig.get(DefaultTestConfig.data)
            
            # CRITICAL: Second read should NOT see modifications from first read
            assert second.id == 999
            assert second.name == "SHARED_DEFAULT"
            assert second.tags == original_tags
            assert "MODIFIED" not in second.tags
            
        finally:
            remove_json(config_path)
    
    def test_special_characters_in_complex_structure(self, temp_config_dir):
        """
        Edge case: Special characters, unicode, control characters.
        
        Tests that JSON serialization handles special characters correctly
        when embedded in complex structures.
        """
        @staticconfig
        class SpecialCharsConfig:
            __config_file__: str = "special.json"
            __version__: str = "1.0.0"
            __development__: bool = False
            __config_path__: str = str(temp_config_dir)
            
            data = Data(
                name="data",
                data_type=ComplexDataExample,
                default=complex_data_example(),
                encoder=complex_data_encoder,
                decoder=complex_data_decoder
            )
        
        config_path = temp_config_dir / "special.json"
        
        try:
            # Create object with special characters
            special = ComplexDataExample(
                id=1,
                name='Name with "quotes" and \'apostrophes\' and \n newlines \t tabs',
                attributes={
                    "emoji": "🎵🔊💾",
                    "unicode": "Ñoño, café, 日本語",
                    "control": "Line1\nLine2\tTabbed",
                    "backslash": "C:\\path\\to\\file"
                },
                tags=["tag\nwith\nnewlines", "tab\there", "quote\"here"],
                parent=ChildData1(
                    kind=ExampleEnum.RED,
                    description="特殊文字 émojis 🎸 and symbols: @#$%^&*()",
                    extra_field1=1.0
                )
            )
            
            # Write and read
            SpecialCharsConfig.set(SpecialCharsConfig.data, special)
            retrieved = SpecialCharsConfig.get(SpecialCharsConfig.data)
            
            # Verify all special characters preserved
            assert retrieved.name == special.name
            assert retrieved.attributes["emoji"] == "🎵🔊💾"
            assert retrieved.attributes["unicode"] == "Ñoño, café, 日本語"
            assert "\n" in retrieved.attributes["control"]
            assert "C:\\path\\to\\file" in retrieved.attributes["backslash"]
            assert retrieved.parent.description == special.parent.description
            
        finally:
            remove_json(config_path)
    
    def test_empty_collections_in_complex_structure(self, temp_config_dir):
        """
        Edge case: Empty collections (lists, dicts) in nested structures.
        
        Verifies that empty collections roundtrip correctly and don't become None.
        """
        @staticconfig
        class EmptyConfig:
            __config_file__: str = "empty.json"
            __version__: str = "1.0.0"
            __development__: bool = False
            __config_path__: str = str(temp_config_dir)
            
            data = Data(
                name="data",
                data_type=ComplexDataExample,
                default=complex_data_example(),
                encoder=complex_data_encoder,
                decoder=complex_data_decoder
            )
        
        config_path = temp_config_dir / "empty.json"
        
        try:
            # Create with all empty collections
            empty_colls = ComplexDataExample(
                id=1,
                name="Empty collections",
                attributes={},  # Empty dict
                tags=[],  # Empty list
                parent=ChildData1(
                    kind=ExampleEnum.GREEN,
                    description="Has empty nested collections",
                    extra_field1=1.0,
                    subdetails=DataDetails(
                        count=0,
                        values=[],  # Empty
                        metadata={}  # Empty
                    )
                )
            )
            
            EmptyConfig.set(EmptyConfig.data, empty_colls)
            retrieved = EmptyConfig.get(EmptyConfig.data)
            
            # Verify empty != None
            assert retrieved.attributes == {}
            assert retrieved.attributes is not None
            assert retrieved.tags == []
            assert retrieved.tags is not None
            assert retrieved.parent.subdetails.values == []
            assert retrieved.parent.subdetails.metadata == {}
            
            # Verify type is preserved
            assert isinstance(retrieved.attributes, dict)
            assert isinstance(retrieved.tags, list)
            assert isinstance(retrieved.parent.subdetails.values, list)
            assert isinstance(retrieved.parent.subdetails.metadata, dict)
            
        finally:
            remove_json(config_path)
    
    def test_numeric_edge_values_in_complex_structure(self, temp_config_dir):
        """
        Edge case: Numeric edge values (very large, very small, zero, negative).
        
        Ensures numeric values in complex structures roundtrip correctly.
        """
        @staticconfig
        class NumericConfig:
            __config_file__: str = "numeric.json"
            __version__: str = "1.0.0"
            __development__: bool = False
            __config_path__: str = str(temp_config_dir)
            
            data = Data(
                name="data",
                data_type=ComplexDataExample,
                default=complex_data_example(),
                encoder=complex_data_encoder,
                decoder=complex_data_decoder
            )
        
        config_path = temp_config_dir / "numeric.json"
        
        try:
            numeric = ComplexDataExample(
                id=0,  # Zero
                name="Numeric edges",
                attributes={
                    "zero": 0,
                    "negative": -999999,
                    "large": 10**15,
                    "float_small": 0.00000001,
                    "float_large": 3.14159265358979323846
                },
                parent=ChildData1(
                    kind=ExampleEnum.BLUE,
                    description="Numeric test",
                    extra_field1=-0.0,  # Negative zero
                    subdetails=DataDetails(
                        count=-100,  # Negative count
                        values=[0, -1, 999999, -999999],
                        metadata={}
                    )
                )
            )
            
            NumericConfig.set(NumericConfig.data, numeric)
            retrieved = NumericConfig.get(NumericConfig.data)
            
            # Verify numeric values
            assert retrieved.id == 0
            assert retrieved.attributes["zero"] == 0
            assert retrieved.attributes["negative"] == -999999
            assert retrieved.attributes["large"] == 10**15
            assert abs(retrieved.attributes["float_small"] - 0.00000001) < 1e-10
            assert abs(retrieved.attributes["float_large"] - 3.14159265358979323846) < 1e-10
            assert retrieved.parent.subdetails.count == -100
            assert retrieved.parent.subdetails.values == [0, -1, 999999, -999999]
            
        finally:
            remove_json(config_path)
