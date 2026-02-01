"""
Test suite for ConfigPayloadMigrator - Deterministic payload migration and normalization.

This module validates the stateless, pure-function behavior of the payload migrator,
which is responsible for reconstructing configuration payloads according to schema
definitions without filesystem access or side effects.

Component Under Test:
    ConfigPayloadMigrator - Pure stateless migrator for configuration payloads

Key Behaviors:
    - Reconstruct data dictionary deterministically from schema (data_fields)
    - Drop fields not present in current schema
    - Add new fields with defaults
    - Type coercion with fallback to defaults
    - Encoder application
    - Timestamp validation and normalization
    - Preservation of valid created timestamp
    - None/null semantics preservation

Critical Invariants:
    - Input payload is never mutated
    - Output structure is always: {version, created, last_modified, data}
    - data dictionary contains EXACTLY the fields from data_fields (no more, no less)
    - Deterministic: same input + schema = same output (except timestamps)
    - Timestamps are always valid ISO 8601 with Z suffix

Test Organization:
    - TestSchemaEvolution: Adding/removing/changing fields during migration
    - TestFieldValueResolution: Type coercion, defaults, encoder application
    - TestTimestampHandling: Created preservation, last_modified update, validation
    - TestEdgeCases: None semantics, empty payloads, malformed data
    - TestDeterminism: Idempotence and mutation safety

All tests validate ACTUAL behavior, not assumptions.
"""

import pytest
from datetime import datetime, timezone, timedelta
from staticconfiguration.entities import Data
from staticconfiguration.json_backend.config_payload_migrator import ConfigPayloadMigrator


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def old_schema_fields():
    """Schema v1.0 with basic fields."""
    return [
        Data(name="url", data_type=str, default="http://localhost"),
        Data(name="timeout", data_type=int, default=30),
        Data(name="enabled", data_type=bool, default=True),
    ]


@pytest.fixture
def new_schema_fields():
    """Schema v2.0 with added field and removed field."""
    return [
        Data(name="url", data_type=str, default="http://localhost"),
        Data(name="timeout", data_type=int, default=30),
        # removed: enabled
        Data(name="retries", data_type=int, default=3),  # NEW field
        Data(name="debug", data_type=bool, default=False),  # NEW field
    ]


# ============================================================================
# TestSchemaEvolution: Migration between schema versions
# ============================================================================

class TestSchemaEvolution:
    """Test migration behavior when schema changes (add/remove/change fields)."""
    
    def test_adds_new_fields_with_defaults(self, new_schema_fields):
        """
        Test that fields new to the schema are added with their default values.
        
        Critical behavior: New fields appear with defaults even if not in payload.
        """
        old_payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {
                "url": "http://example.com",
                "timeout": 60,
                "enabled": True,  # This field will be dropped
            }
        }
        
        result = ConfigPayloadMigrator.migrate_payload(old_payload, "2.0", new_schema_fields)
        
        # New fields should appear with defaults
        assert result["data"]["retries"] == 3
        assert result["data"]["debug"] is False
        
        # Existing fields should be preserved
        assert result["data"]["url"] == "http://example.com"
        assert result["data"]["timeout"] == 60
        
        # Version should be updated
        assert result["version"] == "2.0"
    
    def test_removes_fields_not_in_schema(self, new_schema_fields):
        """
        Test that fields present in payload but not in schema are dropped.
        
        Critical behavior: Only fields in data_fields appear in output.
        """
        payload_with_extra_fields = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {
                "url": "http://example.com",
                "timeout": 60,
                "enabled": True,  # NOT in new schema
                "obsolete_field": "should be dropped",  # NOT in new schema
                "another_old_field": 999,  # NOT in new schema
            }
        }
        
        result = ConfigPayloadMigrator.migrate_payload(
            payload_with_extra_fields, 
            "2.0", 
            new_schema_fields
        )
        
        # Dropped fields should NOT appear
        assert "enabled" not in result["data"]
        assert "obsolete_field" not in result["data"]
        assert "another_old_field" not in result["data"]
        
        # Only schema fields should be present
        assert set(result["data"].keys()) == {"url", "timeout", "retries", "debug"}
    
    def test_type_change_with_coercion(self):
        """
        Test migration when field type changes and value can be coerced.
        
        Example: timeout stored as string "60" migrates to int 60.
        """
        old_payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {
                "timeout": "60",  # String in old schema
            }
        }
        
        new_fields = [
            Data(name="timeout", data_type=int, default=30)  # Now expects int
        ]
        
        result = ConfigPayloadMigrator.migrate_payload(old_payload, "2.0", new_fields)
        
        # Should coerce string "60" to int 60
        assert result["data"]["timeout"] == 60
        assert isinstance(result["data"]["timeout"], int)
    
    def test_type_change_coercion_fails_uses_default(self):
        """
        Test that when type coercion fails, the default value is used.
        
        Critical behavior: Invalid cast → fallback to default.
        """
        payload_with_invalid_type = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {
                "timeout": "not_a_number",  # Cannot convert to int
            }
        }
        
        new_fields = [
            Data(name="timeout", data_type=int, default=30)
        ]
        
        result = ConfigPayloadMigrator.migrate_payload(
            payload_with_invalid_type, 
            "2.0", 
            new_fields
        )
        
        # Should fall back to default when coercion fails
        assert result["data"]["timeout"] == 30
    
    def test_empty_payload_creates_all_defaults(self, old_schema_fields):
        """
        Test migration with completely empty payload.
        
        Critical behavior: Missing data section → all fields use defaults.
        """
        empty_payload = {
            "version": "0.0",
        }
        
        result = ConfigPayloadMigrator.migrate_payload(
            empty_payload, 
            "1.0", 
            old_schema_fields
        )
        
        # All fields should have defaults
        assert result["data"]["url"] == "http://localhost"
        assert result["data"]["timeout"] == 30
        assert result["data"]["enabled"] is True
        
        # Structure should be complete
        assert "version" in result
        assert "created" in result
        assert "last_modified" in result
        assert "data" in result


# ============================================================================
# TestFieldValueResolution: _resolve_field_value behavior
# ============================================================================

class TestFieldValueResolution:
    """Test value resolution with type coercion, defaults, and encoders."""
    
    def test_preserves_value_with_exact_type_match(self):
        """
        Test that values matching the expected type are preserved as-is.
        
        Critical: Uses `type() is` check, not isinstance.
        """
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {
                "count": 42,  # Already int
                "message": "hello",  # Already str
                "flag": True,  # Already bool
            }
        }
        
        fields = [
            Data(name="count", data_type=int, default=0),
            Data(name="message", data_type=str, default=""),
            Data(name="flag", data_type=bool, default=False),
        ]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "1.0", fields)
        
        # Values should be preserved exactly
        assert result["data"]["count"] == 42
        assert result["data"]["message"] == "hello"
        assert result["data"]["flag"] is True
    
    def test_coerces_compatible_types(self):
        """
        Test successful type coercion for compatible types.
        
        Examples: string to int, int to string, int to bool, etc.
        """
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {
                "count": "123",  # String to int
                "port": 8080,  # Int to string
                "enabled": 1,  # Int to bool (truthy)
            }
        }
        
        fields = [
            Data(name="count", data_type=int, default=0),
            Data(name="port", data_type=str, default="8000"),
            Data(name="enabled", data_type=bool, default=False),
        ]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "1.0", fields)
        
        assert result["data"]["count"] == 123
        assert result["data"]["port"] == "8080"
        assert result["data"]["enabled"] is True
    
    def test_coercion_fails_falls_back_to_default(self):
        """
        Test fallback to default when type coercion raises exception.
        
        Critical behavior: Exception during cast → use field.default.
        
        Note: list("string") succeeds (converts to ['s','t','r'...]), so we need
        a value that truly fails coercion.
        """
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {
                "count": "invalid_number",  # int("invalid_number") fails
                "flag": "not_a_bool",  # bool("not_a_bool") doesn't fail, but...
            }
        }
        
        fields = [
            Data(name="count", data_type=int, default=99),
            # For bool, any non-empty string is truthy, so use different test
            Data(name="port", data_type=int, default=8080),  # Will test with complex object
        ]
        
        # Add a case that truly fails: trying to cast a dict to int
        payload["data"]["port"] = {"nested": "object"}
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "1.0", fields)
        
        # Should use defaults when coercion fails
        assert result["data"]["count"] == 99
        assert result["data"]["port"] == 8080
    
    def test_applies_encoder_to_resolved_value(self):
        """
        Test that encoder is applied after value resolution.
        
        Critical: Encoder runs on the resolved value, not the raw value.
        """
        def list_to_csv(items):
            return ",".join(items)
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {
                "tags": ["python", "testing", "qa"]
            }
        }
        
        fields = [
            Data(name="tags", data_type=list, default=[], encoder=list_to_csv)
        ]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "1.0", fields)
        
        # Encoder should convert list to CSV string
        assert result["data"]["tags"] == "python,testing,qa"
    
    def test_applies_encoder_to_default_when_field_missing(self):
        """
        Test that encoder is applied to default value when field is missing.
        
        Critical: In migrate_payload, encoder runs on default if field.default is not None.
        """
        def list_to_csv(items):
            return ",".join(items)
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {}  # tags field missing
        }
        
        fields = [
            Data(
                name="tags", 
                data_type=list, 
                default=["default", "values"], 
                encoder=list_to_csv
            )
        ]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "1.0", fields)
        
        # Encoder should be applied to default
        assert result["data"]["tags"] == "default,values"
    
    def test_encoder_not_applied_when_default_is_none(self):
        """
        Test that encoder is NOT applied when default is None.
        
        Critical: Check in migrate_payload: field.encoder and field.default is not None
        """
        def dummy_encoder(x):
            return f"encoded_{x}"
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {}  # field missing
        }
        
        fields = [
            Data(name="optional", data_type=str, default=None, encoder=dummy_encoder)
        ]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "1.0", fields)
        
        # Should be None, encoder not applied
        assert result["data"]["optional"] is None
    
    def test_none_value_preserved_with_encoder_present(self):
        """
        Test that explicit None in payload is preserved even with encoder.
        
        Critical: _resolve_field_value returns None early if raw_value is None.
        """
        def dummy_encoder(x):
            return f"encoded_{x}"
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {
                "optional": None  # Explicit None
            }
        }
        
        fields = [
            Data(name="optional", data_type=str, default="default", encoder=dummy_encoder)
        ]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "1.0", fields)
        
        # None should be preserved, encoder not applied
        assert result["data"]["optional"] is None
    
    def test_coercion_to_none_default_preserves_none(self):
        """
        Test edge case: cast fails, default is None, result should be None.
        
        Critical: After value = field.default, check if value is None before encoder.
        """
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {
                "optional": "invalid_for_cast"  # Will fail to cast to int
            }
        }
        
        fields = [
            Data(name="optional", data_type=int, default=None)  # Default is None
        ]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "1.0", fields)
        
        # Cast fails → uses default (None) → returns None
        assert result["data"]["optional"] is None


# ============================================================================
# TestTimestampHandling: Created preservation and last_modified update
# ============================================================================

class TestTimestampHandling:
    """Test timestamp validation, normalization, and preservation."""
    
    def test_preserves_valid_created_timestamp(self):
        """
        Test that valid created timestamp is preserved during migration.
        
        Critical: Original created should remain unchanged.
        """
        original_created = "2023-01-01T12:00:00Z"
        payload = {
            "version": "1.0",
            "created": original_created,
            "last_modified": "2023-01-01T12:00:00Z",
            "data": {"field": "value"}
        }
        
        fields = [Data(name="field", data_type=str, default="")]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # Created should be preserved exactly
        assert result["created"] == original_created
    
    def test_normalizes_invalid_created_to_current_time(self):
        """
        Test that invalid created timestamp is normalized to current time.
        
        Invalid cases: missing, empty string, malformed, non-string.
        """
        test_cases = [
            {},  # Missing created
            {"created": ""},  # Empty string
            {"created": "invalid-timestamp"},  # Malformed
            {"created": 12345},  # Non-string
            {"created": None},  # None
        ]
        
        fields = [Data(name="field", data_type=str, default="value")]
        
        for payload in test_cases:
            payload["version"] = "1.0"
            payload["data"] = {"field": "test"}
            
            before = datetime.now(timezone.utc)
            result = ConfigPayloadMigrator.migrate_payload(payload, "1.0", fields)
            after = datetime.now(timezone.utc)
            
            # Created should be normalized to current time
            created_dt = datetime.fromisoformat(result["created"].replace("Z", "+00:00"))
            assert before.replace(microsecond=0) <= created_dt <= after.replace(microsecond=0) + timedelta(seconds=1)
    
    def test_last_modified_always_updated_to_current_time(self):
        """
        Test that last_modified is always set to current time.
        
        Critical: Even with valid timestamps in payload, last_modified is now.
        """
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",  # Old timestamp
            "data": {"field": "value"}
        }
        
        fields = [Data(name="field", data_type=str, default="")]
        
        before = datetime.now(timezone.utc)
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        after = datetime.now(timezone.utc)
        
        # last_modified should be current time, not preserved from payload
        last_mod_dt = datetime.fromisoformat(result["last_modified"].replace("Z", "+00:00"))
        assert before.replace(microsecond=0) <= last_mod_dt <= after.replace(microsecond=0) + timedelta(seconds=1)
        
        # Should NOT equal old timestamp
        assert result["last_modified"] != "2023-01-01T00:00:00Z"
    
    def test_timestamps_format_iso8601_with_z_suffix(self):
        """
        Test that all timestamps are in ISO 8601 format with Z suffix.
        
        Critical: Format YYYY-MM-DDTHH:MM:SSZ (no microseconds).
        """
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {}
        }
        
        fields = [Data(name="field", data_type=str, default="")]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "1.0", fields)
        
        # Both timestamps should end with Z
        assert result["created"].endswith("Z")
        assert result["last_modified"].endswith("Z")
        
        # Should not contain microseconds (no dot)
        assert "." not in result["created"]
        assert "." not in result["last_modified"]
        
        # Should be parseable
        datetime.fromisoformat(result["created"].replace("Z", "+00:00"))
        datetime.fromisoformat(result["last_modified"].replace("Z", "+00:00"))
    
    def test_timestamp_validation_accepts_valid_formats(self):
        """
        Test _is_valid_timestamp accepts various valid ISO 8601 formats.
        """
        valid_timestamps = [
            "2024-01-01T00:00:00Z",
            "2024-12-31T23:59:59Z",
            "2024-06-15T12:30:45+00:00",
            "2024-06-15T12:30:45-05:00",
        ]
        
        for ts in valid_timestamps:
            assert ConfigPayloadMigrator._is_valid_timestamp(ts) is True
    
    def test_timestamp_validation_rejects_invalid_formats(self):
        """
        Test _is_valid_timestamp rejects invalid formats.
        """
        invalid_timestamps = [
            "not-a-timestamp",
            "2024-13-01T00:00:00Z",  # Invalid month
            "2024-01-32T00:00:00Z",  # Invalid day
            "",
            "   ",
            12345,
            None,
            True,
            [],
        ]
        
        for ts in invalid_timestamps:
            assert ConfigPayloadMigrator._is_valid_timestamp(ts) is False


# ============================================================================
# TestEdgeCases: None semantics, empty payloads, malformed structures
# ============================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_payload_data_not_dict_treated_as_empty(self):
        """
        Test that non-dict data is treated as empty dict.
        
        Critical: raw_data = {} if payload.get("data") is not isinstance(dict).
        """
        test_cases = [
            {"data": None},
            {"data": "not a dict"},
            {"data": 123},
            {"data": []},
            {"data": True},
        ]
        
        fields = [
            Data(name="field1", data_type=str, default="default1"),
            Data(name="field2", data_type=int, default=42),
        ]
        
        for payload in test_cases:
            payload["version"] = "1.0"
            result = ConfigPayloadMigrator.migrate_payload(payload, "1.0", fields)
            
            # Should use defaults for all fields
            assert result["data"]["field1"] == "default1"
            assert result["data"]["field2"] == 42
    
    def test_empty_schema_creates_empty_data(self):
        """
        Test migration with empty schema (no fields).
        
        Critical: Output data should be empty dict.
        """
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {
                "some_field": "should be dropped"
            }
        }
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", [])
        
        # Data should be empty
        assert result["data"] == {}
        
        # Structure should still be complete
        assert "version" in result
        assert "created" in result
        assert "last_modified" in result
    
    def test_bool_int_type_distinction(self):
        """
        Test that bool and int are treated as distinct types.
        
        Critical: Uses type() is check, so bool(1) won't match int type.
        In Python, bool is subclass of int, so this tests exact type matching.
        """
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {
                "flag": 1,  # int, not bool
            }
        }
        
        fields = [
            Data(name="flag", data_type=bool, default=False)
        ]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "1.0", fields)
        
        # Should coerce int to bool (bool(1) = True)
        assert result["data"]["flag"] is True
        assert isinstance(result["data"]["flag"], bool)
    
    def test_payload_with_extra_top_level_keys_ignored(self):
        """
        Test that extra keys in payload (not version/created/data) are ignored.
        
        Critical: Only extracts data, created, version - drops everything else.
        """
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"field": "value"},
            "extra_key_1": "ignored",
            "extra_key_2": 123,
            "metadata": {"should": "be ignored"},
        }
        
        fields = [Data(name="field", data_type=str, default="")]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "1.0", fields)
        
        # Output should only have standard keys
        assert set(result.keys()) == {"version", "created", "last_modified", "data"}
        assert "extra_key_1" not in result
        assert "extra_key_2" not in result
        assert "metadata" not in result


# ============================================================================
# TestDeterminism: Idempotence and mutation safety
# ============================================================================

class TestDeterminism:
    """Test deterministic behavior and immutability."""
    
    def test_does_not_mutate_input_payload(self):
        """
        Test that original payload is never mutated.
        
        Critical: Stateless pure function - no side effects.
        """
        original_payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "last_modified": "2023-01-01T00:00:00Z",
            "data": {
                "field1": "value1",
                "field2": 42,
            }
        }
        
        # Create deep copy to compare
        import copy
        payload_copy = copy.deepcopy(original_payload)
        
        fields = [
            Data(name="field1", data_type=str, default=""),
            Data(name="field3", data_type=int, default=0),
        ]
        
        ConfigPayloadMigrator.migrate_payload(original_payload, "2.0", fields)
        
        # Original payload should be unchanged
        assert original_payload == payload_copy
    
    def test_idempotent_migration_with_matching_schema(self):
        """
        Test that migrating with the same schema multiple times is idempotent.
        
        Note: Timestamps will change, but data structure should stabilize.
        """
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {
                "field1": "value1",
                "field2": 42,
            }
        }
        
        fields = [
            Data(name="field1", data_type=str, default="default1"),
            Data(name="field2", data_type=int, default=0),
        ]
        
        # First migration
        result1 = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # Second migration with result1 as input
        result2 = ConfigPayloadMigrator.migrate_payload(result1, "2.0", fields)
        
        # Data should be identical
        assert result1["data"] == result2["data"]
        
        # Version should be stable
        assert result1["version"] == result2["version"] == "2.0"
        
        # Created should be stable (from first migration)
        assert result2["created"] == result1["created"]
    
    def test_deterministic_field_order(self):
        """
        Test that output data dictionary has deterministic field order.
        
        Critical: Order follows data_fields list order.
        """
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {}
        }
        
        fields = [
            Data(name="zebra", data_type=str, default="z"),
            Data(name="alpha", data_type=str, default="a"),
            Data(name="mike", data_type=str, default="m"),
        ]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "1.0", fields)
        
        # Keys should follow schema order, not alphabetical
        assert list(result["data"].keys()) == ["zebra", "alpha", "mike"]


# ============================================================================
# Complex Data Types for Migration Testing
# ============================================================================

from dataclasses import dataclass, field as dataclass_field
from enum import Enum


class Priority(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class Address:
    """Simple nested dataclass."""
    street: str = ""
    city: str = ""
    zip_code: str = ""
    
    def to_dict(self) -> dict:
        return {"street": self.street, "city": self.city, "zip_code": self.zip_code}
    
    @classmethod
    def from_dict(cls, data: dict) -> "Address":
        return cls(
            street=data.get("street", ""),
            city=data.get("city", ""),
            zip_code=data.get("zip_code", "")
        )


@dataclass
class Person:
    """Base class for inheritance testing."""
    name: str
    age: int = 0
    
    def to_dict(self) -> dict:
        return {"__class__": self.__class__.__name__, "name": self.name, "age": self.age}
    
    @classmethod
    def from_dict(cls, data: dict) -> "Person":
        class_name = data.get("__class__", "Person")
        if class_name == "Employee":
            return Employee.from_dict(data)
        elif class_name == "Manager":
            return Manager.from_dict(data)
        return cls(name=data.get("name", ""), age=data.get("age", 0))


@dataclass
class Employee(Person):
    """Subclass for inheritance testing."""
    employee_id: str = ""
    department: str = ""
    
    def to_dict(self) -> dict:
        base = super().to_dict()
        base.update({"employee_id": self.employee_id, "department": self.department})
        return base
    
    @classmethod
    def from_dict(cls, data: dict) -> "Employee":
        return cls(
            name=data.get("name", ""),
            age=data.get("age", 0),
            employee_id=data.get("employee_id", ""),
            department=data.get("department", "")
        )


@dataclass
class Manager(Person):
    """Another subclass for variant switching tests."""
    team_size: int = 0
    budget: float = 0.0
    
    def to_dict(self) -> dict:
        base = super().to_dict()
        base.update({"team_size": self.team_size, "budget": self.budget})
        return base
    
    @classmethod
    def from_dict(cls, data: dict) -> "Manager":
        return cls(
            name=data.get("name", ""),
            age=data.get("age", 0),
            team_size=data.get("team_size", 0),
            budget=data.get("budget", 0.0)
        )


@dataclass
class ComplexConfig:
    """Complex structure with nested dataclasses, lists, dicts, enums."""
    id: int
    name: str
    priority: Priority = Priority.MEDIUM
    tags: list[str] = dataclass_field(default_factory=list)
    metadata: dict[str, str] = dataclass_field(default_factory=dict)
    address: Address | None = None
    owner: Person | None = None
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "priority": self.priority.value if isinstance(self.priority, Priority) else self.priority,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
            "address": self.address.to_dict() if self.address else None,
            "owner": self.owner.to_dict() if self.owner else None,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ComplexConfig":
        priority = data.get("priority", "medium")
        if isinstance(priority, str):
            priority = Priority(priority)
        
        address_data = data.get("address")
        address = Address.from_dict(address_data) if address_data else None
        
        owner_data = data.get("owner")
        owner = Person.from_dict(owner_data) if owner_data else None
        
        return cls(
            id=data.get("id", 0),
            name=data.get("name", ""),
            priority=priority,
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
            address=address,
            owner=owner,
        )


def complex_encoder(obj: ComplexConfig) -> dict:
    return obj.to_dict()


def complex_decoder(data: dict) -> ComplexConfig:
    return ComplexConfig.from_dict(data)


# ============================================================================
# TestComplexMigrationToleratedChanges
# ============================================================================

class TestComplexMigrationToleratedChanges:
    """
    Document which structural changes SURVIVE migration.
    
    Purpose:
        Empirically determine what modifications to complex objects
        are tolerated by the migrator without resetting to default.
        
    Key insight:
        The migrator uses decoder as semantic validator. If decoder
        succeeds AND returns instance of data_type, the RAW VALUE
        (serialized dict) is preserved as-is.
    """
    
    def test_identical_structure_preserved(self):
        """
        Baseline: Same structure with same values survives migration.
        
        EXPECTED: Value preserved (decoder validates successfully)
        """
        original_data = {
            "id": 42,
            "name": "Test Config",
            "priority": "high",
            "tags": ["tag1", "tag2"],
            "metadata": {"key": "value"},
            "address": {"street": "123 Main", "city": "NYC", "zip_code": "10001"},
            "owner": {"__class__": "Employee", "name": "Alice", "age": 30, "employee_id": "E001", "department": "Engineering"},
        }
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": original_data}
        }
        
        default_obj = ComplexConfig(id=0, name="default")
        fields = [Data(name="config", data_type=ComplexConfig, default=default_obj, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Original structure preserved exactly
        assert result["data"]["config"] == original_data
        assert result["data"]["config"]["id"] == 42
        assert result["data"]["config"]["owner"]["employee_id"] == "E001"
    
    def test_add_new_field_to_schema_preserves_existing(self):
        """
        Adding a NEW field to schema preserves existing field values.
        
        EXPECTED: Old field preserved, new field gets default
        """
        existing_data = {"id": 100, "name": "Existing"}
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": existing_data}
        }
        
        # Schema now has TWO fields (config + new_field)
        default_config = ComplexConfig(id=0, name="default")
        fields = [
            Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder),
            Data(name="new_field", data_type=str, default="new_default"),
        ]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Existing field preserved
        assert result["data"]["config"]["id"] == 100
        assert result["data"]["config"]["name"] == "Existing"
        
        # DOCUMENT: New field added with default
        assert result["data"]["new_field"] == "new_default"
    
    def test_remove_field_from_schema_drops_it(self):
        """
        Removing a field from schema drops it from output.
        
        EXPECTED: Field not in schema disappears, other fields preserved
        """
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {
                "config": {"id": 1, "name": "test"},
                "obsolete_field": "will be dropped",
            }
        }
        
        # Schema only has config, not obsolete_field
        default_config = ComplexConfig(id=0, name="default")
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Obsolete field dropped
        assert "obsolete_field" not in result["data"]
        
        # DOCUMENT: config preserved
        assert result["data"]["config"]["id"] == 1
    
    def test_extra_nested_fields_tolerated_by_decoder(self):
        """
        Extra fields INSIDE complex object are tolerated if decoder ignores them.
        
        EXPECTED: Raw value preserved (decoder doesn't validate internal structure strictly)
        """
        # Payload has EXTRA fields not in dataclass
        data_with_extras = {
            "id": 42,
            "name": "test",
            "priority": "low",
            "tags": [],
            "metadata": {},
            "address": None,
            "owner": None,
            "unknown_field": "should_survive",  # Extra field
            "another_extra": 12345,  # Another extra
        }
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": data_with_extras}
        }
        
        default_config = ComplexConfig(id=0, name="default")
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Raw value preserved including extra fields
        # (migrator preserves raw_value when decoder validates)
        assert result["data"]["config"]["id"] == 42
        assert result["data"]["config"]["unknown_field"] == "should_survive"
        assert result["data"]["config"]["another_extra"] == 12345
    
    def test_optional_nested_none_preserved(self):
        """
        None values for optional nested objects are preserved.
        
        EXPECTED: None → None (no decoder called)
        """
        data_with_none = {
            "id": 1,
            "name": "test",
            "priority": "medium",
            "tags": [],
            "metadata": {},
            "address": None,  # Explicitly None
            "owner": None,    # Explicitly None
        }
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": data_with_none}
        }
        
        default_config = ComplexConfig(id=0, name="default", address=Address("default", "city", "00000"))
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: None fields preserved exactly
        assert result["data"]["config"]["address"] is None
        assert result["data"]["config"]["owner"] is None
    
    def test_inheritance_subclass_preserved(self):
        """
        Subclass instances are preserved if decoder reconstructs correctly.
        
        EXPECTED: Employee data survives (decoder handles __class__ marker)
        """
        employee_data = {
            "id": 1,
            "name": "test",
            "priority": "medium",
            "tags": [],
            "metadata": {},
            "address": None,
            "owner": {
                "__class__": "Employee",
                "name": "Alice",
                "age": 30,
                "employee_id": "E001",
                "department": "Engineering"
            },
        }
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": employee_data}
        }
        
        default_config = ComplexConfig(id=0, name="default")
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Subclass data preserved including specific fields
        assert result["data"]["config"]["owner"]["__class__"] == "Employee"
        assert result["data"]["config"]["owner"]["employee_id"] == "E001"
        assert result["data"]["config"]["owner"]["department"] == "Engineering"
    
    def test_switch_inheritance_variant_preserved(self):
        """
        Switching from one subclass to another works if decoder handles it.
        
        EXPECTED: New variant (Manager) preserved after migration
        """
        # Payload with Manager (different from Employee)
        manager_data = {
            "id": 1,
            "name": "test",
            "priority": "medium",
            "tags": [],
            "metadata": {},
            "address": None,
            "owner": {
                "__class__": "Manager",
                "name": "Bob",
                "age": 45,
                "team_size": 10,
                "budget": 100000.0
            },
        }
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": manager_data}
        }
        
        default_config = ComplexConfig(id=0, name="default")
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Manager variant preserved
        assert result["data"]["config"]["owner"]["__class__"] == "Manager"
        assert result["data"]["config"]["owner"]["team_size"] == 10
        assert result["data"]["config"]["owner"]["budget"] == 100000.0
    
    def test_enum_valid_value_preserved(self):
        """
        Valid enum values are preserved during migration.
        
        EXPECTED: Enum string value survives
        """
        data = {
            "id": 1,
            "name": "test",
            "priority": "high",  # Valid Priority enum
            "tags": [],
            "metadata": {},
            "address": None,
            "owner": None,
        }
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": data}
        }
        
        default_config = ComplexConfig(id=0, name="default", priority=Priority.LOW)
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Enum value preserved
        assert result["data"]["config"]["priority"] == "high"
    
    def test_version_change_alone_preserves_data(self):
        """
        Changing only the version does not affect data.
        
        EXPECTED: Data unchanged, only version number changes
        """
        data = {"id": 999, "name": "versioned", "priority": "low", "tags": ["v1"], "metadata": {"version": "1.0"}, "address": None, "owner": None}
        
        payload = {
            "version": "1.0.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": data}
        }
        
        default_config = ComplexConfig(id=0, name="default")
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "99.99.99", fields)
        
        # DOCUMENT: Version changed
        assert result["version"] == "99.99.99"
        
        # DOCUMENT: Data unchanged
        assert result["data"]["config"]["id"] == 999
        assert result["data"]["config"]["name"] == "versioned"


# ============================================================================
# TestComplexMigrationResetToDefault
# ============================================================================

class TestComplexMigrationResetToDefault:
    """
    Document which structural changes RESET to default.
    
    Purpose:
        Empirically determine what modifications cause the migrator
        to discard the old value and use the field's default.
        
    Key insight:
        Reset happens when:
        1. Decoder raises exception
        2. Decoder returns wrong type
        3. No decoder and type doesn't match
    """
    
    def test_invalid_enum_resets_to_default(self):
        """
        Invalid enum value causes decoder to fail → reset to default.
        
        EXPECTED: Reset to default (decoder raises ValueError for invalid enum)
        """
        data_with_invalid_enum = {
            "id": 1,
            "name": "test",
            "priority": "INVALID_PRIORITY",  # Not a valid Priority value
            "tags": [],
            "metadata": {},
            "address": None,
            "owner": None,
        }
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": data_with_invalid_enum}
        }
        
        default_config = ComplexConfig(id=999, name="DEFAULT_NAME", priority=Priority.MEDIUM)
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Reset to default because decoder failed on invalid enum
        assert result["data"]["config"]["id"] == 999
        assert result["data"]["config"]["name"] == "DEFAULT_NAME"
        assert result["data"]["config"]["priority"] == "medium"  # Default encoded
    
    def test_missing_required_field_in_decoder_resets(self):
        """
        Missing required field (like 'name') that decoder needs.
        
        DOCUMENTED BEHAVIOR:
            Our decoder uses .get() with defaults, so it's resilient to missing fields.
            The decoder succeeds → raw value is preserved (even without 'name' key).
            The missing 'name' field simply doesn't appear in preserved raw value.
        """
        # Data missing 'name' which is required by dataclass
        data_missing_required = {
            "id": 1,
            # "name" is missing - but decoder handles it with .get()
            "priority": "low",
        }
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": data_missing_required}
        }
        
        default_config = ComplexConfig(id=999, name="DEFAULT")
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENTED: Decoder uses .get() with defaults, so it succeeds
        # Raw value is preserved AS-IS (without 'name' key)
        assert result["data"]["config"]["id"] == 1
        assert "name" not in result["data"]["config"]  # Missing key stays missing!
        
        # This is IMPORTANT behavior: migrator preserves raw_value, not decoded value
        # So if a key was missing in the original, it stays missing
    
    def test_wrong_type_for_nested_object_resets(self):
        """
        Wrong type for nested object (string instead of dict) → reset.
        
        EXPECTED: Reset to default (decoder fails to parse)
        """
        data_wrong_nested_type = {
            "id": 1,
            "name": "test",
            "priority": "low",
            "tags": [],
            "metadata": {},
            "address": "NOT_A_DICT",  # Should be dict or None
            "owner": None,
        }
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": data_wrong_nested_type}
        }
        
        default_config = ComplexConfig(id=999, name="DEFAULT", address=Address("default_street", "default_city", "00000"))
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Decoder fails when trying Address.from_dict("NOT_A_DICT")
        # So we get reset to default
        assert result["data"]["config"]["id"] == 999
        assert result["data"]["config"]["name"] == "DEFAULT"
    
    def test_completely_wrong_structure_resets(self):
        """
        Completely wrong structure (not a dict at all) → reset.
        
        EXPECTED: Reset to default
        """
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": "just_a_string_not_a_dict"}
        }
        
        default_config = ComplexConfig(id=999, name="DEFAULT")
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Complete reset because decoder can't handle string
        assert result["data"]["config"]["id"] == 999
        assert result["data"]["config"]["name"] == "DEFAULT"
    
    def test_list_instead_of_dict_resets(self):
        """
        List where dict expected → reset.
        
        EXPECTED: Reset to default
        """
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": [1, 2, 3]}  # List, not dict
        }
        
        default_config = ComplexConfig(id=999, name="DEFAULT")
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Reset because list has no .get() method
        assert result["data"]["config"]["id"] == 999
    
    def test_decoder_returns_wrong_type_resets(self):
        """
        Decoder succeeds but returns wrong type → reset.
        
        EXPECTED: Reset to default (isinstance check fails)
        """
        def bad_decoder(data: dict):
            # Returns a string instead of ComplexConfig
            return "not_a_complex_config"
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": {"id": 1, "name": "test"}}
        }
        
        default_config = ComplexConfig(id=999, name="DEFAULT")
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=bad_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Reset because decoded value is not instance of ComplexConfig
        assert result["data"]["config"]["id"] == 999
    
    def test_field_type_changed_in_schema_resets(self):
        """
        Changing field type in schema (e.g., int to str) → behavior depends on decoder.
        
        Note: With complex types using decoder, the decoder determines outcome.
        """
        # Old payload has complex object
        old_data = {"id": 1, "name": "test", "priority": "low", "tags": [], "metadata": {}, "address": None, "owner": None}
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": old_data}
        }
        
        # New schema expects string, not ComplexConfig
        fields = [Data(name="config", data_type=str, default="string_default")]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: No decoder means type coercion is attempted
        # dict → str gives something like "{'id': 1, ...}"
        # This is actually "successful" coercion to string!
        assert isinstance(result["data"]["config"], str)


# ============================================================================
# TestComplexMigrationNoneSemantics
# ============================================================================

class TestComplexMigrationNoneSemantics:
    """
    Document None handling during migration.
    
    Key insight:
        None is terminal - it means "no value exists".
        None is NOT decoded, NOT encoded, just preserved.
    """
    
    def test_explicit_none_in_payload_preserved(self):
        """
        Explicit None in payload is preserved (not reset to default).
        
        EXPECTED: None → None (bypasses decoder entirely)
        """
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": None}  # Explicitly None
        }
        
        default_config = ComplexConfig(id=999, name="DEFAULT")
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: None preserved, default NOT used
        assert result["data"]["config"] is None
    
    def test_missing_field_with_none_default(self):
        """
        Missing field with None as default → None in output.
        
        EXPECTED: None from default
        """
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {}  # Field missing
        }
        
        fields = [Data(name="config", data_type=ComplexConfig, default=None, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Default None is used, encoder NOT applied
        assert result["data"]["config"] is None
    
    def test_none_with_encoder_not_encoded(self):
        """
        None is NOT passed to encoder even when encoder exists.
        
        EXPECTED: None preserved as-is
        """
        def encoder_that_would_crash(obj):
            return obj.to_dict()  # Would crash on None
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": None}
        }
        
        fields = [Data(name="config", data_type=ComplexConfig, default=None, encoder=encoder_that_would_crash, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: No crash, None preserved
        assert result["data"]["config"] is None
    
    def test_decoder_failure_with_none_default(self):
        """
        When decoder fails AND default is None → None in output.
        
        EXPECTED: None (fallback to None default)
        """
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": "invalid_data_for_decoder"}
        }
        
        # Default is None
        fields = [Data(name="config", data_type=ComplexConfig, default=None, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Decoder fails → fallback to default (None) → None returned
        assert result["data"]["config"] is None
    
    def test_nested_none_in_complex_structure_preserved(self):
        """
        None values deep inside complex structure are preserved.
        
        EXPECTED: Nested None values survive round-trip
        """
        data_with_nested_none = {
            "id": 1,
            "name": "test",
            "priority": "low",
            "tags": [],
            "metadata": {},
            "address": None,  # None nested
            "owner": None,    # None nested
        }
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": data_with_nested_none}
        }
        
        default_config = ComplexConfig(id=0, name="default", address=Address("x", "y", "z"))
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Nested None preserved in raw value
        assert result["data"]["config"]["address"] is None
        assert result["data"]["config"]["owner"] is None


# ============================================================================
# TestComplexMigrationEdgeCases
# ============================================================================

class TestComplexMigrationEdgeCases:
    """
    Edge cases and boundary conditions for complex migrations.
    """
    
    def test_empty_collections_preserved(self):
        """
        Empty lists and dicts are preserved (not reset to default).
        
        EXPECTED: Empty collections survive
        """
        data = {
            "id": 1,
            "name": "test",
            "priority": "low",
            "tags": [],           # Empty list
            "metadata": {},       # Empty dict
            "address": None,
            "owner": None,
        }
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": data}
        }
        
        default_config = ComplexConfig(id=0, name="default", tags=["default_tag"], metadata={"default": "value"})
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Empty collections preserved, not replaced by default's collections
        assert result["data"]["config"]["tags"] == []
        assert result["data"]["config"]["metadata"] == {}
    
    def test_large_nested_lists_preserved(self):
        """
        Large collections survive migration.
        
        EXPECTED: All elements preserved
        """
        large_tags = [f"tag_{i}" for i in range(1000)]
        data = {
            "id": 1,
            "name": "test",
            "priority": "low",
            "tags": large_tags,
            "metadata": {},
            "address": None,
            "owner": None,
        }
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": data}
        }
        
        default_config = ComplexConfig(id=0, name="default")
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Large list preserved
        assert len(result["data"]["config"]["tags"]) == 1000
        assert result["data"]["config"]["tags"][500] == "tag_500"
    
    def test_deeply_nested_structure_preserved(self):
        """
        Deeply nested structures survive if decoder handles them.
        
        EXPECTED: All nesting levels preserved
        """
        # Deep nesting in metadata
        deep_metadata = {
            "level1": {
                "level2": {
                    "level3": {
                        "value": "deep_value"
                    }
                }
            }
        }
        
        data = {
            "id": 1,
            "name": "test",
            "priority": "low",
            "tags": [],
            "metadata": deep_metadata,
            "address": None,
            "owner": None,
        }
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": data}
        }
        
        default_config = ComplexConfig(id=0, name="default")
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Deep nesting preserved
        assert result["data"]["config"]["metadata"]["level1"]["level2"]["level3"]["value"] == "deep_value"
    
    def test_unicode_in_complex_structure_preserved(self):
        """
        Unicode characters survive migration.
        
        EXPECTED: Unicode preserved exactly
        """
        data = {
            "id": 1,
            "name": "テスト 🚀 Тест",
            "priority": "low",
            "tags": ["标签", "תגית", "علامة"],
            "metadata": {"emoji": "👍🏽", "chinese": "中文"},
            "address": {"street": "日本語の通り", "city": "東京", "zip_code": "〒100"},
            "owner": None,
        }
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": data}
        }
        
        default_config = ComplexConfig(id=0, name="default")
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Unicode preserved
        assert result["data"]["config"]["name"] == "テスト 🚀 Тест"
        assert result["data"]["config"]["address"]["city"] == "東京"
    
    def test_boolean_int_distinction_in_complex(self):
        """
        Boolean vs int distinction in nested data.
        
        EXPECTED: Types preserved as-is in raw value
        """
        data = {
            "id": 1,
            "name": "test",
            "priority": "low",
            "tags": [],
            "metadata": {"bool_true": True, "bool_false": False, "int_one": 1, "int_zero": 0},
            "address": None,
            "owner": None,
        }
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": data}
        }
        
        default_config = ComplexConfig(id=0, name="default")
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: Types preserved in raw value
        assert result["data"]["config"]["metadata"]["bool_true"] is True
        assert result["data"]["config"]["metadata"]["bool_false"] is False
        assert result["data"]["config"]["metadata"]["int_one"] == 1
        assert result["data"]["config"]["metadata"]["int_zero"] == 0
    
    def test_numeric_edge_values_preserved(self):
        """
        Numeric edge values (very large, zero, negative) survive.
        
        EXPECTED: All numeric values preserved
        """
        data = {
            "id": 999999999999999,  # Very large
            "name": "test",
            "priority": "low",
            "tags": [],
            "metadata": {
                "zero": 0,
                "negative": -999,
                "float": 3.14159265358979,
                "scientific": 1e100,
            },
            "address": None,
            "owner": None,
        }
        
        payload = {
            "version": "1.0",
            "created": "2023-01-01T00:00:00Z",
            "data": {"config": data}
        }
        
        default_config = ComplexConfig(id=0, name="default")
        fields = [Data(name="config", data_type=ComplexConfig, default=default_config, encoder=complex_encoder, decoder=complex_decoder)]
        
        result = ConfigPayloadMigrator.migrate_payload(payload, "2.0", fields)
        
        # DOCUMENT: All numeric values preserved
        assert result["data"]["config"]["id"] == 999999999999999
        assert result["data"]["config"]["metadata"]["zero"] == 0
        assert result["data"]["config"]["metadata"]["negative"] == -999
        assert result["data"]["config"]["metadata"]["scientific"] == 1e100