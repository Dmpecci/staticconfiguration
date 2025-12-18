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
