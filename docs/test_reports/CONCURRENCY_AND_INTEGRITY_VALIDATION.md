# Concurrency and Integrity Validation

## Purpose

This document describes the concurrency mechanisms, integrity guarantees, and corruption recovery behavior of the `staticconfiguration` library. It explains what constitutes correct behavior and when failures indicate real bugs.

---

## Concurrency Architecture

### Lock Mechanism Overview

```
┌─────────────────────────────────────────────────┐
│              _acquire_lock()                     │
│  ┌─────────────────────────────────────────────┐│
│  │  1. Try atomic create: O_CREAT | O_EXCL     ││
│  │  2. Write "pid:monotonic_time" to lock      ││
│  │  3. If FileExistsError:                     ││
│  │     a. Check if lock is stale (TTL > 10s)   ││
│  │     b. Check if owner process is dead       ││
│  │     c. If stale: break lock, retry          ││
│  │     d. If fresh: sleep 50ms, retry          ││
│  └─────────────────────────────────────────────┘│
└─────────────────────────────────────────────────┘
```

### Lock File Format

```
{pid}:{monotonic_time}
Example: 12345:1234567.89
```

- `pid`: Process ID of lock holder
- `monotonic_time`: `time.monotonic()` at acquisition

### TTL Mechanism

- **TTL**: 10 seconds (`_LOCK_TTL_SECONDS = 10.0`)
- **Stale condition**: `time.monotonic() - created > 10.0`
- **Dead process**: `not psutil.pid_exists(pid)`
- **Invalid format**: Treated as stale (fallback to mtime check)

---

## Concurrency Guarantees

### Mutual Exclusion

**Guarantee**: Only one process can hold the lock at any time.

**Mechanism**:
- Atomic lock creation with `os.open(O_CREAT | O_EXCL)`
- `FileExistsError` means another process holds lock
- Busy-wait until lock available

**Validated by**:
- `test_second_writer_waits_for_first`
- `test_write_blocks_other_writers`

### No Deadlocks

**Guarantee**: System cannot permanently deadlock.

**Mechanism**:
- TTL breaks orphaned locks after 10 seconds
- Dead process detection via `psutil.pid_exists()`
- Both readers and writers clean stale locks

**Validated by**:
- `test_stale_lock_recovery_on_write`
- `test_reader_recovers_from_zombie_lock`
- `test_no_deadlock_with_process_termination`

### Lock Ownership

**Guarantee**: Only the lock owner can release a lock.

**Mechanism**:
- `_release_lock()` reads lock file, extracts owner PID
- Raises `RuntimeError` if caller PID ≠ owner PID
- Prevents accidental lock theft

**Validated by**:
- `test_release_lock_validates_ownership`

---

## Integrity Guarantees

### Atomic Writes

**Guarantee**: Writes are atomic (all-or-nothing).

**Mechanism**:
```python
tmp_path = config_path.with_suffix(".tmp")
with tmp_path.open("w") as f:
    json.dump(payload, f, indent=2)
tmp_path.replace(config_path)  # Atomic on POSIX
```

**Validated by**:
- `test_uses_temporary_file`
- `test_no_tmp_file_leftover_after_writes`

### No Silent Data Loss

**Guarantee**: Concurrent writes with proper locking never lose data silently.

**Mechanism**:
- Lock serializes all writes
- Atomic write prevents partial state
- Last writer wins (deterministic)

**Validated by**:
- `test_all_fields_written_4_processes`
- `test_all_fields_written_6_processes`

**Test Contract**:
```
Given: N processes writing to N fields concurrently (with locks)
When: All processes complete successfully
Then: ALL fields contain non-default values (proves writes persisted)
And: JSON is valid and parseable
```

---

## Corruption Recovery

### Recovery Mechanism

**Trigger**: `_ensure_safe_state()` encounters unparseable JSON

**Behavior**:
1. Catch `JSONDecodeError` or `OSError`
2. Create timestamped backup of corrupted file (`.json.corruptedbackup`)
3. Recreate file with default values for all fields
4. Emit `ConfigurationResetWarning`
5. Continue operation normally

**Critical**: Recovery is NEVER silent. Warning is always emitted, and corrupted content is preserved for inspection.

### Recovery Validation

**Validated by**:
- `test_recovers_from_malformed_json`
- `test_recovery_restores_all_defaults`
- `test_recovery_after_unsafe_concurrent_writes`
- `test_explicit_corruption_recovery_with_write`

**Test Contract**:
```
Given: JSON file is corrupted (any cause)
When: Next safe operation (read/write) is performed
Then: ConfigurationResetWarning MUST be emitted
And: JSON MUST be restored to valid state with defaults
And: Corrupted file MUST be backed up with .json.corruptedbackup suffix
```

### Corruption Causes

| Cause | Detection | Recovery |
|-------|-----------|----------|
| External tampering | JSONDecodeError | Automatic |
| `concurrency_unsafe=True` races | JSONDecodeError | Automatic |
| Disk failure | OSError | Automatic |
| Partial write (power loss) | JSONDecodeError | Automatic |

---

## What Constitutes a Real Bug

### Fatal Conditions (Test Failures)

These indicate bugs in the implementation:

| Condition | Meaning |
|-----------|---------|
| JSON corrupted after normal operations | Lock mechanism broken |
| RuntimeError during lock release | Ownership validation failed |
| Process deadlock (timeout exceeded) | TTL mechanism broken |
| Silent data loss (field at default unexpectedly) | Write not persisted |
| Silent corruption recovery (no warning) | Warning not emitted |

### Acceptable Conditions (Not Bugs)

These are expected behavior:

| Condition | Explanation |
|-----------|-------------|
| `ConfigurationResetWarning` emitted | Corruption detected and handled |
| Lock wait under contention | Mutex working correctly |
| Slow operations under load | Expected performance degradation |
| `JSONDecodeError` in stress tests | Reader saw partial state |

---

## TTL Edge Cases

### Boundary Behavior

- Lock at exactly 10.0 seconds: Treated as stale (`>=` comparison)
- Lock with future timestamp: Treated as stale
- Lock with invalid format: Fallback to mtime check

### Race Conditions Handled

| Scenario | Behavior |
|----------|----------|
| Lock disappears during stale check | `FileNotFoundError` caught, retry |
| Two processes break same stale lock | Only one succeeds, other retries |
| Lock recreated during break | New lock respected |

**Validated by**:
- `test_ttl_boundary_conditions`
- `test_concurrent_stale_lock_cleanup_race`
- `test_lock_disappears_during_timestamp_read`

---

## Process Detection

### Mechanism

```python
def _process_exists(pid: int) -> bool:
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return True  # Conservative: assume alive
```

### Behavior

- Uses `psutil.pid_exists()` for reliable cross-platform detection
- On failure: assumes process exists (conservative)
- Combined with TTL for robust stale detection

**Validated by**:
- `test_process_exists_detection`

---

## Concurrency Tests Summary

### Test Categories

| Category | Tests | Purpose |
|----------|-------|---------|
| Lock Lifecycle | 3 | Basic lock creation/release |
| Concurrent Writes | 2 | Writer serialization |
| Concurrent Reads | 1 | Reader non-blocking |
| Read-Write Interaction | 2 | Reader waits for writer |
| Stress Scenarios | 2 | Mixed concurrent operations |
| Edge Cases | 7 | Race conditions, termination |
| TTL Mechanism | 6 | Stale lock detection |
| TTL Edge Cases | 6 | Boundary conditions |
| Concurrent Integrity | 4 | No silent data loss |

### Total: 33 concurrency-related tests

---

## Usage Recommendations

### Safe Concurrent Access

```python
# Default: concurrency_unsafe=False (locks enabled)
MyConfig.set(MyConfig.field, value)  # Safe
value = MyConfig.get(MyConfig.field)  # Safe
```

### Unsafe Mode (Performance)

```python
# Explicit bypass for single-process scenarios
MyConfig.set(MyConfig.field, value, concurrency_unsafe=True)
```

**Warning**: `concurrency_unsafe=True` disables all locking. Use only when:
- Single process access is guaranteed
- Corruption risk is acceptable
- Performance is critical

---

## Known Limitations

### Not Supported

- Network filesystems (NFS, SMB) for concurrent access
- Windows file locking (uses same mechanism, different semantics)
- Distributed locking across machines

### Platform Notes

- Lock mechanism uses POSIX-style file operations
- `psutil.pid_exists()` handles cross-platform PID checking
- Atomic `Path.replace()` behavior varies by filesystem
