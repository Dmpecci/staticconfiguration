# Testing Overview and System Contracts

## Purpose

This document defines the testing strategy, system invariants, and behavioral contracts for the `staticconfiguration` library. It serves as the authoritative reference for understanding what the test suite validates and what guarantees the system provides.

---

## Test Suite Structure

### Current Status

- **Total Tests**: 120
- **Deterministic Tests**: 104 (run by default)
- **Stress Tests**: 16 (marked `@pytest.mark.stress`, manual execution)
- **Pass Rate**: 100%

### Test Files

| File | Tests | Purpose |
|------|-------|---------|
| `test_persistence.py` | 36 | JSONBackend and StaticConfigBase operations |
| `test_migrator.py` | 26 | ConfigPayloadMigrator pure function behavior |
| `test_concurrency.py` | 29 | Lock lifecycle, TTL, concurrent access |
| `test_concurrent_integrity.py` | 4 | Data integrity under concurrent writes |
| `test_class_declaration.py` | 4 | Decorator validation and class transformation |
| `test_utilities.py` | 5 | Helper functions |
| `test_concurrency_stress.py` | 16 | Exploratory stress testing (manual) |

---

## System Invariants

### Invariant 1: Atomic Writes

**Guarantee**: A write operation either completes fully or has no effect.

**Implementation**:
- Writes go to `.tmp` file first
- Atomic `Path.replace()` swaps `.tmp` to target
- No partial writes visible to readers

**Tested by**:
- `test_uses_temporary_file`
- `test_no_tmp_file_leftover_after_writes`

### Invariant 2: Mutual Exclusion

**Guarantee**: At most one process holds the write lock at any time.

**Implementation**:
- Lock file created with `O_CREAT | O_EXCL` (atomic creation)
- Lock contains `pid:monotonic_time` for ownership and TTL
- Second writer blocks until first releases

**Tested by**:
- `test_second_writer_waits_for_first`
- `test_multiple_sequential_writes`
- `test_release_lock_validates_ownership`

### Invariant 3: No Permanent Deadlocks

**Guarantee**: A crashed process cannot permanently block the system.

**Implementation**:
- Lock TTL: 10 seconds (`_LOCK_TTL_SECONDS = 10.0`)
- Stale detection: `time.monotonic() - created > TTL`
- Dead process detection: `psutil.pid_exists()`
- Stale locks are automatically broken

**Tested by**:
- `test_stale_lock_recovery_on_write`
- `test_reader_recovers_from_zombie_lock`
- `test_concurrent_writes_with_stale_locks`

### Invariant 4: Corruption Recovery

**Guarantee**: Corrupted JSON is automatically recovered with explicit warning.

**Implementation**:
- `_ensure_safe_state()` catches `JSONDecodeError`
- Corrupted file backed up with timestamped `.json.corruptedbackup` suffix
- File recreated with default values
- `ConfigurationResetWarning` emitted

**Tested by**:
- `test_recovers_from_malformed_json`
- `test_recovery_restores_all_defaults`
- `test_explicit_corruption_recovery_with_write`

**Critical**: Recovery is NEVER silent. Warning is always emitted, and corrupted content is preserved for inspection.

### Invariant 5: Schema Migration

**Guarantee**: Configuration files migrate correctly when schema changes.

**Implementation**:
- `ConfigPayloadMigrator.migrate_payload()` handles all transformations
- New fields get defaults
- Removed fields are dropped
- Type coercion with fallback to defaults

**Tested by**:
- `test_adds_new_fields_with_defaults`
- `test_removes_fields_not_in_schema`
- `test_type_change_with_coercion`

---

## Behavioral Contracts

### StaticConfigBase.get()

**Preconditions**:
- `data_field` is a `Data` instance defined in the class

**Postconditions**:
- Returns stored value or default if not set
- File created if it doesn't exist
- Decoder applied if defined
- Lock acquired unless `concurrency_unsafe=True`

**Errors**:
- `KeyError`: Field not defined in class
- `TypeError`: `data_field` is not a `Data` instance

### StaticConfigBase.set()

**Preconditions**:
- `data_field` is a `Data` instance defined in the class
- `new_value` matches `data_field.data_type`

**Postconditions**:
- Value persisted to JSON
- `last_modified` timestamp updated
- Encoder applied if defined
- Other fields preserved
- Lock acquired unless `concurrency_unsafe=True`

**Errors**:
- `KeyError`: Field not defined in class
- `TypeError`: Value type mismatch or invalid field

### JSONBackend._acquire_lock()

**Preconditions**:
- Config file path is valid

**Postconditions**:
- Lock file exists with `pid:monotonic_time`
- Caller has exclusive access
- Stale locks broken if encountered

**Behavior**:
- Busy-wait with 50ms sleep interval
- Breaks locks older than 10 seconds
- Breaks locks from dead processes

### JSONBackend._release_lock()

**Preconditions**:
- Lock was acquired by this process

**Postconditions**:
- Lock file deleted
- Other processes can acquire

**Errors**:
- `RuntimeError`: Lock not owned by this process
- `RuntimeError`: Lock file disappeared unexpectedly

---

## What Is NOT Guaranteed

### No Fairness

The locking mechanism does not guarantee FIFO ordering. Under contention, any waiting process may acquire next.

### No Real-Time Guarantees

Operations may be delayed by:
- OS scheduling
- Lock contention
- Large file I/O

### No Cross-Machine Safety

File locks work within a single machine. Network filesystems (NFS, SMB) are not supported for concurrent access.

### No Transaction Semantics

Multiple field updates are not atomic. Each `set()` is independent.

---

## Test Categories

### Deterministic Tests

**Purpose**: Validate correctness of system contracts.

**Characteristics**:
- Reproducible results
- Clear pass/fail criteria
- Run in CI pipeline

**Assertion Strategy**:
- Assert on invariant violations
- Assert on contract breaches
- Assert on data corruption

### Stress Tests

**Purpose**: Observe system behavior under extreme load.

**Characteristics**:
- Exploratory, not prescriptive
- Metrics are informative only
- Manual execution (`pytest -m stress`)

**Assertion Strategy**:
- Assert ONLY on final JSON corruption
- Assert ONLY on deadlocks (timeout)
- DO NOT assert on timing or throughput

---

## Acceptable Failures

### Transient Errors During Stress

The following are expected and acceptable during stress tests:

- `ConfigurationResetWarning` (corruption detected and recovered)
- `FileNotFoundError` during unsafe concurrent writes
- `JSONDecodeError` during active write (reader sees partial state)

These indicate the system working as designed, not bugs.

### Lock Contention Delays

Under high contention, operations may take longer than expected. This is not a failure.

---

## Running Tests

```bash
# Run deterministic tests (default)
pytest tests/ -v

# Run stress tests (manual)
pytest tests/ -m stress -v -s

# Run specific test file
pytest tests/test_concurrency.py -v

# Run with coverage
pytest tests/ --cov=staticconfiguration
```

---

## Maintenance Notes

### Adding New Tests

1. Deterministic tests: Add to appropriate file, no special markers
2. Stress tests: Add `@pytest.mark.stress`, document in STRESS_AND_CHAOS_TESTING.md

### Test Philosophy

- Tests validate ACTUAL behavior, not assumptions
- Tests fail on invariant violations, not implementation details
- Stress tests observe and report, they don't enforce SLAs
