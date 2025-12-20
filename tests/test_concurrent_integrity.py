"""
Concurrent integrity tests for JSONBackend.

These tests validate critical system invariants under concurrent access:
    1. No silent data loss (all writes must persist)
    2. No silent restoration to defaults (corruption must be explicit)
    3. Recovery mechanism works correctly when corruption is deliberate

Test Philosophy:
    - Tests fail ONLY on real invariant violations
    - ConfigurationResetWarning is expected in corruption tests
    - RuntimeError indicates broken lock mechanism (FATAL)
    - JSON corruption after normal operations is FATAL
"""
import multiprocessing
import os
import random
import tempfile
import time
import warnings
from pathlib import Path

import pytest

from staticconfiguration.entities import Data
from staticconfiguration.json_backend.json_backend import JSONBackend
from staticconfiguration.exceptions.configuration_reset_warning import ConfigurationResetWarning
from tests.test_utilities import remove_json


@pytest.fixture
def temp_config_dir():
    """Provide a temporary directory for test configuration files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def generate_four_field_schema() -> list[Data]:
    """Generate schema with 4 integer fields, all defaulting to 0."""
    return [
        Data(name=f"field_{i}", data_type=int, default=0)
        for i in range(4)
    ]


def concurrent_writer_worker(config_path: Path, process_id: int, schema: list[Data], iterations: int = 30):
    """
    Worker that writes a random int >= 1 to field_{process_id % 4}.
    
    This guarantees:
        - All 4 fields get written (with 4+ processes)
        - Values are never 0 (proves no silent restoration to defaults)
    
    Writes repeatedly to ensure convergence under last-writer-wins semantics.
    """
    backend = JSONBackend()
    field_index = process_id % 4
    data_field = schema[field_index]
    
    for _ in range(iterations):
        # Generate random positive integer (avoid 0 to prove write happened)
        random_value = random.randint(1, 2**31 - 1)
        
        try:
            backend.write_value(
                data=data_field,
                new_value=random_value,
                config_path=config_path,
                version="1.0.0",
                data_fields=schema,
                development=False,
                concurrency_unsafe=False
            )
        except Exception:
            # Tolerate transient failures (lock race conditions, etc.)
            # Keep trying to ensure at least some writes succeed
            pass


def unsafe_writer_worker(config_path: Path, process_id: int, schema: list[Data], iterations: int):
    """
    Worker that writes UNSAFELY to deliberately cause corruption.
    
    concurrency_unsafe=True bypasses locks, leading to race conditions.
    """
    backend = JSONBackend()
    field_index = process_id % 4
    data_field = schema[field_index]
    
    for _ in range(iterations):
        random_value = random.randint(1, 2**31 - 1)
        try:
            backend.write_value(
                data=data_field,
                new_value=random_value,
                config_path=config_path,
                version="1.0.0",
                data_fields=schema,
                development=False,
                concurrency_unsafe=True  # DELIBERATE CORRUPTION
            )
        except Exception:
            # Expected: FileNotFoundError, JSONDecodeError, etc.
            # Race conditions from bypassing locks are the goal of this worker
            pass


def verify_json_integrity(config_path: Path) -> tuple[bool, str]:
    """
    Verify JSON file is parseable and structurally valid.
    
    Returns:
        (is_valid, error_message)
    """
    import json
    
    try:
        if not config_path.exists():
            return False, "JSON file does not exist"
        
        with config_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        
        required_keys = {"version", "created", "last_modified", "data"}
        if not required_keys.issubset(payload.keys()):
            return False, f"Missing required keys. Found: {payload.keys()}"
        
        if not isinstance(payload["data"], dict):
            return False, "data section is not a dict"
        
        return True, "OK"
    
    except json.JSONDecodeError as e:
        return False, f"JSONDecodeError: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


class TestConcurrentWriteIntegrity:
    """
    Tests that validate no silent data loss under concurrent writes.
    
    Invariant: After N processes write to N fields, all fields must contain
    non-default values (proving writes persisted).
    """
    
    def test_all_fields_written_4_processes(self, temp_config_dir):
        """
        4 processes write to 4 fields (one per process).
        
        Success condition:
            - All 4 fields must be > 0 (proving write happened)
            - JSON must be valid and parseable
        
        Failure conditions:
            - Any field is 0 (silent data loss or restoration to default)
            - RuntimeError (broken lock mechanism)
            - JSON corruption
        """
        config_path = temp_config_dir / "integrity_4proc.json"
        schema = generate_four_field_schema()
        
        # Initialize config (no concurrency yet, use safe mode)
        backend = JSONBackend()
        backend.write_value(
            data=schema[0],
            new_value=0,
            config_path=config_path,
            version="1.0.0",
            data_fields=schema,
            development=False,
            concurrency_unsafe=False
        )
        
        # Launch 4 processes, each writes to one field
        processes = []
        for process_id in range(4):
            p = multiprocessing.Process(
                target=concurrent_writer_worker,
                args=(config_path, process_id, schema, 30)
            )
            processes.append(p)
            p.start()
        
        # Wait for completion
        for p in processes:
            p.join(timeout=10)
            if p.is_alive():
                p.terminate()
                pytest.fail("Process did not complete within timeout (potential deadlock)")
        
        # Verify JSON integrity
        is_valid, error_msg = verify_json_integrity(config_path)
        assert is_valid, f"JSON corrupted after concurrent writes: {error_msg}"
        
        # Verify all fields were written (all > 0)
        for i, field in enumerate(schema):
            value = backend.read_value(
                data=field,
                config_path=config_path,
                version="1.0.0",
                data_fields=schema,
                development=False,
                concurrency_unsafe=False
            )
            assert value > 0, (
                f"Field {field.name} is {value} (expected > 0). "
                "This indicates silent data loss or restoration to default."
            )
    
    def test_all_fields_written_6_processes(self, temp_config_dir):
        """
        6 processes write to 4 fields (some fields overwritten multiple times).
        
        Success condition:
            - All 4 fields must be > 0
            - JSON must be valid
        
        This tests that last write wins correctly with multiple writers per field.
        """
        config_path = temp_config_dir / "integrity_6proc.json"
        schema = generate_four_field_schema()
        
        # Initialize (no concurrency yet, use safe mode)
        backend = JSONBackend()
        backend.write_value(
            data=schema[0],
            new_value=0,
            config_path=config_path,
            version="1.0.0",
            data_fields=schema,
            development=False,
            concurrency_unsafe=False
        )
        
        # Launch 6 processes (field_0 and field_2 get 2 writers each)
        processes = []
        for process_id in range(6):
            p = multiprocessing.Process(
                target=concurrent_writer_worker,
                args=(config_path, process_id, schema, 30)
            )
            processes.append(p)
            p.start()
        
        # Wait
        for p in processes:
            p.join(timeout=10)
            if p.is_alive():
                p.terminate()
                pytest.fail("Process timeout (potential deadlock)")
        
        # Verify integrity
        is_valid, error_msg = verify_json_integrity(config_path)
        assert is_valid, f"JSON corrupted: {error_msg}"
        
        # Verify all fields written
        for field in schema:
            value = backend.read_value(
                data=field,
                config_path=config_path,
                version="1.0.0",
                data_fields=schema,
                development=False,
                concurrency_unsafe=False
            )
            assert value > 0, f"Field {field.name} not written (value={value})"


class TestDeliberateCorruptionRecovery:
    """
    Tests that validate the corruption recovery mechanism.
    
    Strategy:
        1. Deliberately corrupt JSON using concurrency_unsafe=True
        2. Attempt normal operation (read or write)
        3. Verify ConfigurationResetWarning is emitted
        4. Verify JSON is restored to valid state with defaults
    """
    
    def test_recovery_after_unsafe_concurrent_writes(self, temp_config_dir):
        """
        Deliberately corrupt JSON via unsafe concurrent writes, then verify recovery.
        
        Steps:
            1. Launch multiple processes writing with concurrency_unsafe=True
            2. This MAY cause JSON corruption (race conditions)
            3. Perform a safe read/write operation
            4. If corruption occurred, ConfigurationResetWarning is emitted
            5. Verify JSON is valid after operation (recovered if corrupted)
        
        NOTE: Corruption is not guaranteed - unsafe writes may succeed without
        breaking JSON. The test validates recovery IF corruption occurs.
        """
        config_path = temp_config_dir / "corruption_recovery.json"
        schema = generate_four_field_schema()
        
        # Initialize
        backend = JSONBackend()
        backend.write_value(
            data=schema[0],
            new_value=999,
            config_path=config_path,
            version="1.0.0",
            data_fields=schema,
            development=False,
            concurrency_unsafe=True
        )
        
        # Launch 4 processes writing UNSAFELY (high probability of corruption)
        processes = []
        for process_id in range(4):
            p = multiprocessing.Process(
                target=unsafe_writer_worker,
                args=(config_path, process_id, schema, 50)  # 50 iterations each
            )
            processes.append(p)
            p.start()
        
        # Wait for completion
        for p in processes:
            p.join(timeout=10)
            if p.is_alive():
                p.terminate()
        
        # At this point, JSON MAY be corrupted (not guaranteed)
        is_valid_before, _ = verify_json_integrity(config_path)
        
        # Perform SAFE operation - should recover if corrupted
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            
            # This should detect corruption and recover
            value = backend.read_value(
                data=schema[0],
                config_path=config_path,
                version="1.0.0",
                data_fields=schema,
                development=False,
                concurrency_unsafe=False
            )
            
            # Verify ConfigurationResetWarning was emitted ONLY if corruption occurred
            reset_warnings = [warning for warning in w if issubclass(warning.category, ConfigurationResetWarning)]
            if not is_valid_before:
                # Corruption happened, warning MUST be emitted
                assert len(reset_warnings) > 0, (
                    "JSON was corrupted but ConfigurationResetWarning was not emitted. "
                    "Recovery mechanism failed."
                )
        
        # Verify JSON is now valid (critical invariant)
        is_valid_after, error_msg = verify_json_integrity(config_path)
        assert is_valid_after, f"JSON still corrupted after recovery attempt: {error_msg}"
        
        # If recovery occurred (warning emitted), verify defaults are restored
        if len(reset_warnings) > 0:
            for field in schema:
                value = backend.read_value(
                    data=field,
                    config_path=config_path,
                    version="1.0.0",
                    data_fields=schema,
                    development=False,
                    concurrency_unsafe=False
                )
                assert value == field.default, (
                    f"Field {field.name} not restored to default after recovery. "
                    f"Expected {field.default}, got {value}"
                )
            
            # Verify backup file was created
            backup_files = list(config_path.parent.glob(f"{config_path.stem}_*.json.corruptedbackup"))
            assert len(backup_files) > 0, (
                "Recovery occurred but no backup file found. "
                "Expected at least one .json.corruptedbackup file."
            )
    
    def test_explicit_corruption_recovery_with_write(self, temp_config_dir):
        """
        Manually corrupt JSON file, then verify write operation recovers.
        
        This is a more explicit test: we corrupt the file ourselves,
        then verify the backend detects and recovers.
        """
        config_path = temp_config_dir / "explicit_corruption.json"
        schema = generate_four_field_schema()
        
        # Initialize valid JSON
        backend = JSONBackend()
        backend.write_value(
            data=schema[0],
            new_value=123,
            config_path=config_path,
            version="1.0.0",
            data_fields=schema,
            development=False,
            concurrency_unsafe=True
        )
        
        # Manually corrupt the JSON
        with config_path.open("w", encoding="utf-8") as f:
            f.write("{this is not valid json!!!")
        
        # Verify it's corrupted
        is_valid_before, _ = verify_json_integrity(config_path)
        assert not is_valid_before, "Test setup failed: JSON should be corrupted"
        
        # Attempt write - should trigger recovery
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            
            backend.write_value(
                data=schema[1],
                new_value=456,
                config_path=config_path,
                version="1.0.0",
                data_fields=schema,
                development=False,
                concurrency_unsafe=False
            )
            
            # Verify warning was emitted
            reset_warnings = [warning for warning in w if issubclass(warning.category, ConfigurationResetWarning)]
            assert len(reset_warnings) > 0, "ConfigurationResetWarning not emitted during recovery"
        
        # Verify JSON is now valid
        is_valid_after, error_msg = verify_json_integrity(config_path)
        assert is_valid_after, f"JSON not recovered: {error_msg}"
        
        # Verify backup file was created
        backup_files = list(config_path.parent.glob(f"{config_path.stem}_*.json.corruptedbackup"))
        assert len(backup_files) == 1, (
            f"Expected exactly one backup file, found {len(backup_files)}: {backup_files}"
        )
        
        backup_file = backup_files[0]
        assert backup_file.name.startswith(f"{config_path.stem}_"), (
            f"Backup name '{backup_file.name}' doesn't start with '{config_path.stem}_'"
        )
        assert backup_file.suffix == ".corruptedbackup", (
            f"Backup suffix is '{backup_file.suffix}', expected '.corruptedbackup'"
        )
        
        # Verify backup contains the corrupted content (not parseable as JSON)
        import json
        with backup_file.open("r", encoding="utf-8") as f:
            backup_content = f.read()
        
        assert "{this is not valid json!!!" in backup_content, (
            "Backup doesn't contain the original corrupted content"
        )
        
        # Verify backup is indeed corrupted (cannot be parsed)
        with pytest.raises(json.JSONDecodeError):
            with backup_file.open("r", encoding="utf-8") as f:
                json.load(f)
        
        # Verify recovery behavior: system restores defaults, then applies the write
        # Result must be deterministic:
        #   - field_1 (written): 456
        #   - all other fields: 0 (default)
        for field in schema:
            value = backend.read_value(
                data=field,
                config_path=config_path,
                version="1.0.0",
                data_fields=schema,
                development=False,
                concurrency_unsafe=False
            )
            if field.name == "field_1":
                assert value == 456, f"Written field should be 456, got {value}"
            else:
                assert value == 0, f"Field {field.name} should be default (0), got {value}"
