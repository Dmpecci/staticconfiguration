# Stress and Chaos Testing

## Purpose

This document describes the stress testing approach for `staticconfiguration`. Stress tests are exploratory tools designed to observe system behavior under extreme conditions, not to enforce performance SLAs.

---

## Philosophy

### Stress Tests Are Observational

**Key Principle**: These tests exist to LEARN, not to "pass".

- Metrics are informative, not assertive
- Failures indicate genuine problems only
- Timing variations are expected and acceptable

### What We Measure

- Total execution time
- Average operation time
- Operations completed
- Error counts by type
- Final JSON integrity

### What We Assert

Stress tests fail ONLY on:

| Condition | Reason |
|-----------|--------|
| Final JSON corrupted | Lock mechanism broken |
| Process deadlock | TTL mechanism broken |
| RuntimeError from locks | Ownership violation |

Stress tests DO NOT fail on:

| Condition | Reason |
|-----------|--------|
| Slow operations | Expected under load |
| `ConfigurationResetWarning` | Corruption detected and handled |
| Transient `JSONDecodeError` | Reader saw partial state |
| Incomplete operations | Acceptable under extreme load |

---

## Test Suite

### Location

`tests/test_concurrency_stress.py` - 16 tests

### Execution

```bash
# Run all stress tests
pytest -m stress -v -s

# Run specific stress test
pytest tests/test_concurrency_stress.py::test_stress_concurrent_writers_10_fields -m stress -v -s

# Exclude stress tests (default behavior)
pytest tests/  # Only runs 104 deterministic tests
```

### Test Categories

#### Concurrent Readers (3 tests)

| Test | Configuration | Purpose |
|------|---------------|---------|
| `test_stress_concurrent_readers_10_fields` | 8 readers × 50 iterations | Baseline read performance |
| `test_stress_concurrent_readers_100_fields` | 8 readers × 30 iterations | Medium payload reads |
| `test_stress_concurrent_readers_1000_fields` | 8 readers × 20 iterations | Large payload reads |

**Expected behavior**: Readers complete quickly, no errors, no blocking.

#### Concurrent Writers (3 tests)

| Test | Configuration | Purpose |
|------|---------------|---------|
| `test_stress_concurrent_writers_10_fields` | 4 writers × 25 iterations | Baseline write serialization |
| `test_stress_concurrent_writers_100_fields` | 4 writers × 20 iterations | Medium payload writes |
| `test_stress_concurrent_writers_1000_fields` | 4 writers × 15 iterations | Large payload writes |

**Expected behavior**: Writers serialize correctly, lock contention visible in timing.

#### Mixed Read/Write (3 tests)

| Test | Configuration | Purpose |
|------|---------------|---------|
| `test_stress_mixed_readwrite_10_fields` | 8 readers + 4 writers | Realistic workload simulation |
| `test_stress_mixed_readwrite_100_fields` | 8 readers + 4 writers | Medium payload mixed |
| `test_stress_mixed_readwrite_1000_fields` | 8 readers + 4 writers | Large payload mixed |

**Expected behavior**: Readers wait during writes, no corruption.

#### Extreme Scenarios (5 tests)

| Test | Configuration | Purpose |
|------|---------------|---------|
| `test_stress_extreme_payload_10000_fields` | 10K fields, light load | Payload size limits |
| `test_stress_alternating_operations_100_fields` | Rapid lock cycling | Lock acquisition/release stress |
| `test_stress_high_contention_single_field` | All writers → one field | Maximum contention |
| `test_stress_writer_storm_random_chaos` | 12 writers, random fields | Chaos engineering |
| `test_stress_reader_writer_thunderdome` | 12 readers + 8 writers | Maximum concurrency |

#### Smoke Test (1 test)

| Test | Purpose |
|------|---------|
| `test_stress_smoke_test_all_sizes` | Quick validation across all payload sizes |

---

## Performance Baselines

### Observed Metrics (Reference Only)

| Payload Size | File Size | Read Time | Write Time |
|--------------|-----------|-----------|------------|
| 10 fields | 0.3 KB | ~0.02 ms | ~1.6 ms |
| 100 fields | 2.4 KB | ~0.5 ms | ~2.5 ms |
| 1,000 fields | 23.4 KB | ~1-2 ms | ~4-6 ms |
| 10,000 fields | 243.2 KB | ~7 ms | ~20-25 ms |

**Note**: These are informative benchmarks, not SLAs. Actual performance varies by hardware and system load.

---

## Chaos Engineering Features

### Random Field Access

Workers access random fields in each iteration:
```python
random_field = random.choice(schema)
random_value = random.randint(-2**31, 2**31 - 1)
```

### Benefits

- Unpredictable access patterns
- Higher probability of detecting race conditions
- Tests entire schema, not just one field

### Diagnostics Captured

Each error captures:
```python
{
    "process_id": os.getpid(),
    "worker_type": "reader/writer/mixed",
    "operation_type": "read/write",
    "field_accessed": "field_00042",
    "value_written": -1234567890,
    "iteration_number": 15,
    "exception_type": "JSONDecodeError",
    "exception_message": "Extra data: line 17...",
    "stacktrace": [...],
}
```

---

## Artifact Preservation

### When Corruption Is Detected

Diagnostic artifacts are saved to:
```
docs/test_reports/internal/artifacts/<test_name>/<timestamp>/
├── corrupted.json       # The corrupted file
├── lock_file.lock       # Lock if present (orphaned)
├── temp_file.tmp        # Temp file if present (interrupted write)
└── DIAGNOSTIC_REPORT.md # Error breakdown
```

### Usage

Review artifacts to diagnose:
- What caused the corruption
- Which process was involved
- What operation was in progress

---

## Interpreting Results

### Scenario: Clean Run

```
STRESS TEST: 8 Concurrent Readers - 10 Fields
Processes: 8
Total time: 0.026s
Errors: 0
JSON integrity: ✓ VALID
```

**Interpretation**: System behaving correctly. No issues.

### Scenario: Transient Errors, Valid Final State

```
STRESS TEST: 8 Readers + 4 Writers - 1000 Fields
Errors: 3 (JSONDecodeError)
JSON integrity: ✓ VALID
```

**Interpretation**: Readers occasionally saw partial writes during active writing. This is expected and handled. Final state is correct.

### Scenario: Final Corruption (FAILURE)

```
STRESS TEST: ...
Errors: 15
JSON integrity: ✗ CORRUPTED

Artifacts saved to: docs/test_reports/internal/artifacts/...
```

**Interpretation**: This is a REAL bug. The lock mechanism failed to protect the file. Investigate artifacts.

---

## When Stress Failures Are Critical

### Always Critical

| Symptom | Meaning |
|---------|---------|
| JSON corrupted after test | Lock serialization broken |
| Deadlock (test timeout) | TTL mechanism broken |
| `RuntimeError: lock not held` | Lock protocol violated |

### Never Critical

| Symptom | Meaning |
|---------|---------|
| `ConfigurationResetWarning` | Corruption detected and recovered |
| Slow operations | Normal under load |
| Some operations incomplete | Acceptable for large payloads |
| Transient `JSONDecodeError` | Reader/writer race (handled) |

---

## Running Stress Tests

### Quick Smoke Test

```bash
pytest tests/test_concurrency_stress.py::test_stress_smoke_test_all_sizes -m stress -v -s
```

### Full Suite

```bash
pytest -m stress -v -s
```

### With Output Capture

```bash
pytest -m stress -v -s 2>&1 | tee stress_results.txt
```

### Repeated Runs (Detect Flakiness)

```bash
for i in {1..10}; do pytest -m stress -q; done
```

---

## Design Decisions

### Why Manual Execution

Stress tests are excluded from CI because:
- Timing is non-deterministic
- Results vary by hardware
- False positives on slow CI runners
- They observe, don't enforce

### Why No Timing Assertions

```python
# BAD: Brittle, hardware-dependent
assert elapsed < 1.0, "Too slow"

# GOOD: Only assert on correctness
assert verify_json_integrity(config_path)
```

### Why Report Errors But Don't Fail

Transient errors during stress are expected. The system is designed to recover. What matters is final state.

---

## Adding New Stress Tests

1. Add function to `tests/test_concurrency_stress.py`
2. Apply `@pytest.mark.stress` decorator
3. Follow pattern: setup → execute → collect metrics → verify integrity
4. Print informative output (total time, operations, errors)
5. Assert ONLY on corruption/deadlocks
6. Update this document

---

## Maintenance

### If Tests Start Failing Consistently

1. Check for real deadlocks first
2. Review implementation changes
3. Increase timeouts only as last resort
4. Document any new expected behaviors

### Test Execution Time Budget

- Individual test: < 1 second typical
- Full suite: 2-3 seconds total
- If much slower: check for contention issues
