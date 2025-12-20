# Migrator Validation

## Purpose

This document describes the behavior and validation of `ConfigPayloadMigrator`, the pure stateless component responsible for deterministic payload migration between schema versions.

---

## Component Characteristics

### Design Properties

| Property | Description |
|----------|-------------|
| **Stateless** | All methods are static, no instance state |
| **Pure** | No side effects, no mutation of inputs |
| **Deterministic** | Same input + schema = same output |
| **Isolated** | No filesystem access, no locking |

### Responsibilities

1. Schema Evolution: Add/remove/change fields during migration
2. Type Coercion: Cast values with fallback to defaults
3. Encoder Application: Transform values via field encoders
4. Timestamp Normalization: Validate and normalize ISO 8601 timestamps
5. Structure Reconstruction: Build output deterministically from schema

---

## Test Coverage

**Total Tests**: 26 (all in `test_migrator.py`)

### Test Organization

| Class | Tests | Purpose |
|-------|-------|---------|
| `TestSchemaEvolution` | 5 | Migration between schema versions |
| `TestFieldValueResolution` | 8 | Type coercion, defaults, encoders |
| `TestTimestampHandling` | 6 | Timestamp validation and normalization |
| `TestEdgeCases` | 4 | None semantics, malformed data |
| `TestDeterminism` | 3 | Immutability, idempotence |

---

## Behavioral Contracts

### Output Structure Invariance

**Guarantee**: Every migration produces exactly `{version, created, last_modified, data}`.

**Behavior**:
- `data` contains EXACTLY fields from `data_fields`
- Extra keys in input payload are ignored
- Missing fields get default values

**Validated by**:
- `test_removes_fields_not_in_schema`
- `test_payload_with_extra_top_level_keys_ignored`
- `test_empty_schema_creates_empty_data`

### Immutability Contract

**Guarantee**: Input payload is never mutated.

**Validated by**:
```python
import copy
original = copy.deepcopy(payload)
ConfigPayloadMigrator.migrate_payload(payload, version, fields)
assert payload == original  # ✓ PASSES
```

### Type Coercion Semantics

| Scenario | Behavior |
|----------|----------|
| Exact type match | Value preserved |
| Compatible types | Successful cast |
| Incompatible types | Fallback to default |
| None value | Preserved (no coercion) |

**Critical**: `bool` and `int` are treated as distinct despite subclass relationship. Uses `type() is` for exact matching.

**Validated by**:
- `test_preserves_value_with_exact_type_match`
- `test_coerces_compatible_types`
- `test_coercion_fails_falls_back_to_default`
- `test_bool_int_type_distinction`

### Encoder Application Rules

| Scenario | Encoder Applied? |
|----------|------------------|
| Resolved value (after coercion) | ✓ Yes |
| Default (field missing, default not None) | ✓ Yes |
| Value is None | ✗ No |
| Default is None | ✗ No |

**Validated by**:
- `test_applies_encoder_to_resolved_value`
- `test_applies_encoder_to_default_when_field_missing`
- `test_encoder_not_applied_when_default_is_none`
- `test_none_value_preserved_with_encoder_present`

### Timestamp Behavior

| Input | `created` | `last_modified` |
|-------|-----------|-----------------|
| Valid timestamp | Preserved | Updated to now |
| Invalid timestamp | Set to now | Updated to now |
| Missing | Set to now | Updated to now |

**Format**: ISO 8601 with Z suffix, no microseconds (e.g., `2024-01-15T10:30:00Z`)

**Invalid timestamp examples**:
- Missing key
- Empty string
- Non-string type
- Malformed string
- Invalid date values

**Validated by**:
- `test_preserves_valid_created_timestamp`
- `test_normalizes_invalid_created_to_current_time`
- `test_last_modified_always_updated_to_current_time`
- `test_timestamps_format_iso8601_with_z_suffix`
- `test_timestamp_validation_accepts_valid_formats`
- `test_timestamp_validation_rejects_invalid_formats`

### None/Null Semantics

**Guarantee**: Explicit `None` values are preserved with JSON null semantics.

| Scenario | Result |
|----------|--------|
| `None` in payload | Preserved as `None` |
| Cast fails with `default=None` | Returns `None` |
| `None` value | Encoder bypassed |

**Validated by**:
- `test_none_value_preserved_with_encoder_present`
- `test_coercion_to_none_default_preserves_none`
- `test_handles_none_value`

---

## Schema Evolution Examples

### Adding New Fields

**Input (v1.0)**:
```json
{
  "version": "1.0",
  "data": {"url": "http://example.com", "timeout": 60}
}
```

**New Schema (v2.0)**: Adds `retries`, `debug`

**Output**:
```json
{
  "version": "2.0",
  "data": {
    "url": "http://example.com",
    "timeout": 60,
    "retries": 3,
    "debug": false
  }
}
```

### Removing Fields

**Input**: Contains `enabled`, `obsolete_field`
**Schema**: Does not include these fields

**Output**: Fields removed, only schema fields present

### Type Changes

**Input**: `"timeout": "60"` (string)
**Schema**: `timeout: int`

**Output**: `"timeout": 60` (coerced to int)

**If coercion fails**: Uses field default

---

## Implementation Details

### Core Methods

```python
@staticmethod
def migrate_payload(payload: dict, version: str, data_fields: list[Data]) -> dict:
    """Main entry point. Returns migrated payload."""

@staticmethod
def _resolve_field_value(raw_value: Any, field: Data) -> Any:
    """Resolve and type-coerce a field value."""

@staticmethod
def _now_timestamp() -> str:
    """Generate current UTC timestamp."""

@staticmethod
def _is_valid_timestamp(value: Any) -> bool:
    """Validate ISO 8601 timestamp."""
```

### Algorithm

1. Extract `data` from payload (or empty dict if missing/invalid)
2. For each field in schema:
   - If field exists in data: resolve value (coerce, encode)
   - If field missing: use default (encode if applicable)
3. Validate `created` timestamp (preserve or normalize)
4. Set `last_modified` to current time
5. Return reconstructed payload

---

## Usage Context

The migrator is called by `JSONBackend._ensure_safe_state()` when:

1. File version differs from expected version
2. `development=True` (forces migration)
3. Payload structure is invalid (recovery path)

The migrator itself has no knowledge of files or locking. It operates purely on dictionaries.

---

## Testing Notes

### Test Independence

Each test creates its own fixtures and validates specific behavior. Tests do not depend on each other.

### Timestamp Testing

Tests that check timestamps use `datetime.now()` boundaries to validate correctness without exact matching.

### Encoder Testing

Tests use simple encoders (e.g., `str.upper`, `",".join`) to verify application rules.
