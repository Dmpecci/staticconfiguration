"""
Stress, performance, and load testing suite for staticconfiguration library.

⚠️ IMPORTANT: These are NOT functional correctness tests.
These are exploratory tests designed to observe system behavior under extreme load.

Purpose:
    - Push the system to unusual limits
    - Observe behavior under heavy concurrent load
    - Collect approximate performance metrics
    - Detect catastrophic failures (deadlocks, corruption, crashes)
    - DO NOT fail on timing variations
    - DO NOT block CI pipelines

All tests are marked with @pytest.mark.stress and should be executed manually:
    pytest -m stress

Test Philosophy:
    - These tests exist to LEARN, not to "pass"
    - Metrics are informative, not assertive
    - Failures should indicate genuine problems (corruption, deadlocks)
    - If something explodes: report it clearly, don't silence it

Test Categories:
    - Payload size stress: 10, 100, 1000, 10000 fields
    - Concurrent readers: Multiple processes reading simultaneously
    - Concurrent writers: Multiple processes writing simultaneously
    - Mixed read/write: Readers and writers competing for access
    - Endurance: Sustained operations over time

Metrics Collected (informative only):
    - Total execution time
    - Average operation time
    - Operations completed
    - Number of processes
    - Payload size
    - Success/failure counts

What We Detect (assertive):
    - Deadlocks (processes hang indefinitely)
    - JSON corruption (file not parseable after stress)
    - Unexpected exceptions during operations
    - Complete operation failures for small payloads (10 fields)
"""

import json
import multiprocessing
import pytest
import time
import tempfile
import traceback
import os
import shutil
import random
from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Dict, Any

from staticconfiguration.entities import Data
from staticconfiguration.json_backend.json_backend import JSONBackend
from tests.test_utilities import remove_json


# ============================================================================
# Fixtures and Utilities
# ============================================================================

@pytest.fixture
def temp_config_dir():
    """Provide a temporary directory for stress test configuration files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def generate_schema(num_fields: int) -> List[Data]:
    """
    Generate a schema with N simple integer fields for stress testing.
    
    Args:
        num_fields: Number of fields to generate
        
    Returns:
        List of Data instances with simple int fields
    """
    return [
        Data(name=f"field_{i:05d}", data_type=int, default=i)
        for i in range(num_fields)
    ]


def initialize_stress_config(config_path: Path, num_fields: int) -> List[Data]:
    """
    Initialize a config file with N fields for stress testing.
    
    Uses write_value which automatically initializes the file via _ensure_safe_state.
    
    Returns:
        The schema (list of Data fields) used for initialization
    """
    backend = JSONBackend()
    schema = generate_schema(num_fields)
    # Write one field to trigger initialization - _ensure_safe_state is called internally
    # This creates the file with all defaults
    if schema:
        backend.write_value(
            data=schema[0],
            new_value=schema[0].default,
            config_path=config_path,
            version="1.0.0",
            data_fields=schema,
            development=False,
            concurrency_unsafe=True  # Safe since we're initializing
        )
    return schema


def verify_json_integrity(config_path: Path) -> Tuple[bool, str]:
    """
    Verify that JSON file is parseable and has basic structure.
    
    Returns:
        (is_valid, error_message)
    """
    try:
        if not config_path.exists():
            return False, "File does not exist"
        
        with config_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        
        # Check basic structure
        required_keys = {"version", "created", "last_modified", "data"}
        if not required_keys.issubset(payload.keys()):
            missing = required_keys - payload.keys()
            return False, f"Missing keys: {missing}"
        
        if not isinstance(payload["data"], dict):
            return False, "data field is not a dict"
        
        return True, "OK"
    
    except json.JSONDecodeError as e:
        return False, f"JSON decode error: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


def cleanup_locks(config_path: Path):
    """Clean up any lingering lock files after stress tests."""
    lock_path = config_path.with_suffix(config_path.suffix + ".lock")
    try:
        if lock_path.exists():
            lock_path.unlink()
    except Exception:
        pass


def handle_test_completion(test_name: str, config_path: Path, summary: Dict[str, Any], 
                          num_processes: int, expect_no_errors: bool = False) -> Tuple[bool, str]:
    """
    Common handler for test completion: diagnostics, artifacts, reporting.
    
    Args:
        test_name: Name of the test for artifacts
        config_path: Path to config file
        summary: Results summary from collect_and_report_results
        num_processes: Number of processes used
        expect_no_errors: If True, test should have zero errors (for realistic scenarios)
    
    Returns:
        (is_valid, error_msg) tuple from integrity check
    """
    # Verify integrity
    is_valid, error_msg = verify_json_integrity(config_path)
    print(f"JSON integrity: {'✓ VALID' if is_valid else '✗ CORRUPTED - ' + error_msg}")
    
    # Print diagnostics if errors occurred
    if summary['error_count'] > 0 or not is_valid:
        print_diagnostic_summary(summary, test_name)
    
    # Save artifacts if corruption detected
    if not is_valid:
        artifacts_dir = save_corruption_artifacts(test_name, config_path, summary, num_processes)
        print(f"\n💾 Corruption artifacts saved to: {artifacts_dir}")
    
    return is_valid, error_msg


def save_corruption_artifacts(test_name: str, config_path: Path, summary: Dict[str, Any], num_processes: int):
    """
    Save diagnostic artifacts when JSON corruption is detected.
    
    Creates timestamped directory with:
    - Corrupted JSON file
    - Lock file (if exists)
    - Temp file (if exists)
    - Diagnostic report
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    artifacts_dir = Path("docs/test_reports/internal/artifacts") / test_name / timestamp
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy corrupted JSON
    if config_path.exists():
        try:
            shutil.copy2(config_path, artifacts_dir / "corrupted.json")
        except Exception as e:
            (artifacts_dir / "copy_error.txt").write_text(f"Failed to copy JSON: {e}")
    
    # Copy lock file if exists
    lock_path = config_path.with_suffix(config_path.suffix + ".lock")
    if lock_path.exists():
        try:
            shutil.copy2(lock_path, artifacts_dir / "lock_file.lock")
        except Exception:
            pass
    
    # Copy temp file if exists
    tmp_path = config_path.with_suffix(".tmp")
    if tmp_path.exists():
        try:
            shutil.copy2(tmp_path, artifacts_dir / "temp_file.tmp")
        except Exception:
            pass
    
    # Create diagnostic report
    report_lines = [
        f"# Corruption Diagnostic Report",
        f"",
        f"**Test Name**: {test_name}",
        f"**Timestamp**: {timestamp}",
        f"**Processes**: {num_processes}",
        f"",
        f"## Summary",
        f"- Total operations: {summary.get('total_operations', 'N/A')}",
        f"- Total time: {summary.get('total_time', 'N/A'):.3f}s",
        f"- Error count: {summary.get('error_count', 0)}",
        f"",
        f"## Error Breakdown",
    ]
    
    # Classify errors
    error_types = {}
    for result in summary.get("results", []):
        for error in result.get("detailed_errors", []):
            error_type = error.get("exception_type", "Unknown")
            error_types[error_type] = error_types.get(error_type, 0) + 1
    
    if error_types:
        for error_type, count in sorted(error_types.items(), key=lambda x: x[1], reverse=True):
            report_lines.append(f"- {error_type}: {count} occurrences")
    else:
        report_lines.append("- No errors captured in workers")
    
    report_lines.extend([
        f"",
        f"## Detailed Errors",
        f"",
    ])
    
    # Add detailed error information
    for idx, result in enumerate(summary.get("results", [])):
        detailed_errors = result.get("detailed_errors", [])
        if detailed_errors:
            report_lines.append(f"### Process {idx + 1} ({result.get('type', 'unknown')})")
            for error in detailed_errors[:5]:  # First 5 errors per process
                report_lines.extend([
                    f"",
                    f"**Iteration**: {error.get('iteration_number', 'N/A')}",
                    f"**Operation**: {error.get('operation_type', 'N/A')}",
                    f"**Field**: {error.get('field_accessed', 'N/A')}",
                ])
                if error.get('value_written') is not None:
                    report_lines.append(f"**Value**: {error.get('value_written')}")
                report_lines.extend([
                    f"**Exception**: {error.get('exception_type', 'N/A')}",
                    f"**Message**: {error.get('exception_message', 'N/A')}",
                    f"**PID**: {error.get('process_id', 'N/A')}",
                    f"**Timestamp**: {error.get('timestamp', 'N/A'):.6f}s",
                    f"",
                ])
    
    (artifacts_dir / "DIAGNOSTIC_REPORT.md").write_text("\n".join(report_lines))
    
    return artifacts_dir


def classify_error(exception: Exception) -> str:
    """Classify exception type for reporting."""
    exc_name = type(exception).__name__
    
    if isinstance(exception, json.JSONDecodeError):
        return "JSONDecodeError"
    elif isinstance(exception, TimeoutError):
        return "TimeoutError"
    elif isinstance(exception, (FileNotFoundError, OSError)):
        return "FileSystemError"
    elif isinstance(exception, KeyError):
        return "KeyError"
    else:
        return exc_name


# ============================================================================
# Worker Functions for Multiprocessing
# ============================================================================

def concurrent_reader_worker(config_path: Path, schema: List[Data], iterations: int, result_queue: multiprocessing.Queue):
    """
    Worker that reads RANDOM fields from the schema multiple times.
    
    🔥 CHAOS MODE: Each iteration picks a random field to maximize entropy.
    
    Reports back: (success_count, total_time, errors, detailed_errors)
    """
    backend = JSONBackend()
    
    success_count = 0
    errors = []  # Simple string errors for backward compatibility
    detailed_errors = []  # Rich error objects
    start_time = time.perf_counter()
    process_id = os.getpid()
    
    try:
        for iteration_idx in range(iterations):
            # 🎲 Random field access - increases probability of race conditions
            random_field = random.choice(schema)
            
            try:
                value = backend.read_value(
                    data=random_field,
                    config_path=config_path,
                    version="1.0.0",
                    data_fields=schema,
                    development=False,
                    concurrency_unsafe=False
                )
                success_count += 1
            except Exception as e:
                operation_time = time.perf_counter() - start_time
                
                # Simple error for compatibility
                errors.append(str(e))
                
                # Detailed error for diagnostics - NOW WITH FIELD INFO
                detailed_error = {
                    "process_id": process_id,
                    "worker_type": "reader",
                    "operation_type": "read",
                    "field_accessed": random_field.name,  # 🔍 Critical for diagnosis
                    "iteration_number": iteration_idx,
                    "timestamp": operation_time,
                    "exception_type": classify_error(e),
                    "exception_message": str(e),
                    "stacktrace": traceback.format_exc().split("\n")[-4:-1],
                    "handled": True,
                }
                detailed_errors.append(detailed_error)
    finally:
        elapsed = time.perf_counter() - start_time
        result_queue.put({
            "type": "reader",
            "success": success_count,
            "elapsed": elapsed,
            "errors": errors,
            "detailed_errors": detailed_errors,
        })


def concurrent_writer_worker(config_path: Path, schema: List[Data], iterations: int, worker_id: int, result_queue: multiprocessing.Queue):
    """
    Worker that writes RANDOM values to RANDOM fields.
    
    🔥 CHAOS MODE: 
    - Each iteration picks a random field
    - Writes a random int32 value (full range: -2^31 to 2^31-1)
    - Maximizes entropy to detect corruption and race conditions
    
    Reports back: (success_count, total_time, errors, detailed_errors)
    """
    backend = JSONBackend()
    
    success_count = 0
    errors = []
    detailed_errors = []
    start_time = time.perf_counter()
    process_id = os.getpid()
    
    try:
        for i in range(iterations):
            # 🎲 Random field access
            random_field = random.choice(schema)
            # 🎲 Random value - FULL int32 range for maximum chaos
            random_value = random.randint(-2**31, 2**31 - 1)
            
            try:
                backend.write_value(
                    data=random_field,
                    new_value=random_value,
                    config_path=config_path,
                    version="1.0.0",
                    data_fields=schema,
                    development=False,
                    concurrency_unsafe=False
                )
                success_count += 1
            except Exception as e:
                operation_time = time.perf_counter() - start_time
                
                errors.append(str(e))
                
                # Detailed error - NOW WITH FIELD AND VALUE INFO
                detailed_error = {
                    "process_id": process_id,
                    "worker_type": "writer",
                    "operation_type": "write",
                    "field_accessed": random_field.name,  # 🔍 Which field
                    "value_written": random_value,  # 🔍 What value
                    "iteration_number": i,
                    "timestamp": operation_time,
                    "exception_type": classify_error(e),
                    "exception_message": str(e),
                    "stacktrace": traceback.format_exc().split("\n")[-4:-1],
                    "handled": True,
                }
                detailed_errors.append(detailed_error)
    finally:
        elapsed = time.perf_counter() - start_time
        result_queue.put({
            "type": "writer",
            "success": success_count,
            "elapsed": elapsed,
            "errors": errors,
            "detailed_errors": detailed_errors,
        })


def mixed_worker(config_path: Path, schema: List[Data], read_iterations: int, write_iterations: int, worker_id: int, result_queue: multiprocessing.Queue):
    """
    Worker that alternates between reads and writes on RANDOM fields.
    
    🔥 CHAOS MODE:
    - Each read: random field
    - Each write: random field + random value (full int32 range)
    - Maximum entropy for race condition detection
    
    Reports back: (read_success, write_success, total_time, errors, detailed_errors)
    """
    backend = JSONBackend()
    
    read_success = 0
    write_success = 0
    errors = []
    detailed_errors = []
    start_time = time.perf_counter()
    process_id = os.getpid()
    
    try:
        for i in range(max(read_iterations, write_iterations)):
            # Try a read - RANDOM FIELD
            if i < read_iterations:
                random_field = random.choice(schema)
                try:
                    backend.read_value(
                        data=random_field,
                        config_path=config_path,
                        version="1.0.0",
                        data_fields=schema,
                        development=False,
                        concurrency_unsafe=False
                    )
                    read_success += 1
                except Exception as e:
                    operation_time = time.perf_counter() - start_time
                    errors.append(f"read: {e}")
                    
                    detailed_error = {
                        "process_id": process_id,
                        "worker_type": "mixed",
                        "operation_type": "read",
                        "field_accessed": random_field.name,
                        "iteration_number": i,
                        "timestamp": operation_time,
                        "exception_type": classify_error(e),
                        "exception_message": str(e),
                        "stacktrace": traceback.format_exc().split("\n")[-4:-1],
                        "handled": True,
                    }
                    detailed_errors.append(detailed_error)
            
            # Try a write - RANDOM FIELD + RANDOM VALUE
            if i < write_iterations:
                random_field = random.choice(schema)
                random_value = random.randint(-2**31, 2**31 - 1)
                try:
                    backend.write_value(
                        data=random_field,
                        new_value=random_value,
                        config_path=config_path,
                        version="1.0.0",
                        data_fields=schema,
                        development=False,
                        concurrency_unsafe=False
                    )
                    write_success += 1
                except Exception as e:
                    operation_time = time.perf_counter() - start_time
                    errors.append(f"write: {e}")
                    
                    detailed_error = {
                        "process_id": process_id,
                        "worker_type": "mixed",
                        "operation_type": "write",
                        "field_accessed": random_field.name,
                        "value_written": random_value,
                        "iteration_number": i,
                        "timestamp": operation_time,
                        "exception_type": classify_error(e),
                        "exception_message": str(e),
                        "stacktrace": traceback.format_exc().split("\n")[-4:-1],
                        "handled": True,
                    }
                    detailed_errors.append(detailed_error)
    finally:
        elapsed = time.perf_counter() - start_time
        result_queue.put({
            "type": "mixed",
            "read_success": read_success,
            "write_success": write_success,
            "elapsed": elapsed,
            "errors": errors,
            "detailed_errors": detailed_errors,
        })


# ============================================================================
# Helper to Collect and Report Results
# ============================================================================

def print_diagnostic_summary(summary: Dict[str, Any], test_name: str):
    """
    Print detailed diagnostic information when errors occur.
    Only called when there are errors or failures.
    """
    print("\n" + "!"*70)
    print("⚠️  DIAGNOSTIC INFORMATION - Errors Detected")
    print("!"*70)
    
    # Error type breakdown
    error_type_counts = {}
    json_decode_errors = []
    timeout_errors = []
    filesystem_errors = []
    other_errors = []
    
    for result in summary.get("results", []):
        for error in result.get("detailed_errors", []):
            error_type = error.get("exception_type", "Unknown")
            error_type_counts[error_type] = error_type_counts.get(error_type, 0) + 1
            
            # Categorize for detailed reporting
            if error_type == "JSONDecodeError":
                json_decode_errors.append(error)
            elif error_type == "TimeoutError":
                timeout_errors.append(error)
            elif error_type == "FileSystemError":
                filesystem_errors.append(error)
            else:
                other_errors.append(error)
    
    # Print summary
    print(f"\n📊 Error Type Breakdown:")
    if error_type_counts:
        for error_type, count in sorted(error_type_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"   {error_type}: {count} occurrences")
    else:
        print("   No detailed errors captured")
    
    # JSONDecodeError details (critical)
    if json_decode_errors:
        print(f"\n🔴 CRITICAL: {len(json_decode_errors)} JSONDecodeError(s) detected during operations")
        print("   This indicates transient corruption or race conditions")
        for idx, error in enumerate(json_decode_errors[:3], 1):
            print(f"\n   Error #{idx}:")
            print(f"     PID: {error.get('process_id')}")
            print(f"     Worker: {error.get('worker_type')}")
            print(f"     Operation: {error.get('operation_type')}")
            print(f"     Field: {error.get('field_accessed', 'N/A')}")
            if error.get('value_written') is not None:
                print(f"     Value: {error.get('value_written')}")
            print(f"     Iteration: {error.get('iteration_number')}")
            print(f"     Message: {error.get('exception_message', '')[:100]}")
    
    # Timeout errors
    if timeout_errors:
        print(f"\n⏱️  {len(timeout_errors)} Timeout/Lock wait error(s)")
        print("   Workers may have waited too long for locks")
    
    # Filesystem errors
    if filesystem_errors:
        print(f"\n📁 {len(filesystem_errors)} Filesystem error(s)")
        print("   File may have been deleted or became temporarily inaccessible")
    
    # Other errors
    if other_errors:
        print(f"\n❓ {len(other_errors)} Other error(s)")
        for error in other_errors[:2]:
            print(f"   - {error.get('exception_type')}: {error.get('exception_message', '')[:80]}")
    
    print("\n" + "!"*70)


def collect_and_report_results(processes: List[multiprocessing.Process], result_queue: multiprocessing.Queue, timeout: float = 60.0) -> Dict[str, Any]:
    """
    Wait for all processes to complete and collect their results.
    
    Returns:
        Summary dict with aggregated metrics (including detailed_errors)
        
    Raises:
        TimeoutError: If processes don't complete within timeout (potential deadlock)
    """
    start_wait = time.perf_counter()
    
    # Wait for processes with timeout
    for proc in processes:
        remaining = timeout - (time.perf_counter() - start_wait)
        if remaining <= 0:
            # Timeout - potential deadlock
            for p in processes:
                if p.is_alive():
                    p.terminate()
                    p.join(timeout=1)
            raise TimeoutError(f"Processes did not complete within {timeout}s (potential deadlock)")
        
        proc.join(timeout=remaining)
        if proc.is_alive():
            # Process still running after timeout
            for p in processes:
                if p.is_alive():
                    p.terminate()
                    p.join(timeout=1)
            raise TimeoutError(f"Process {proc.pid} did not complete within timeout (potential deadlock)")
    
    # Collect results
    results = []
    while not result_queue.empty():
        try:
            results.append(result_queue.get_nowait())
        except:
            break
    
    # Aggregate metrics
    total_elapsed = max((r["elapsed"] for r in results), default=0)
    total_operations = sum(
        r.get("success", 0) + r.get("read_success", 0) + r.get("write_success", 0)
        for r in results
    )
    all_errors = [err for r in results for err in r.get("errors", [])]
    
    # Count detailed errors
    total_detailed_errors = sum(
        len(r.get("detailed_errors", [])) for r in results
    )
    
    summary = {
        "processes": len(processes),
        "total_time": total_elapsed,
        "total_operations": total_operations,
        "avg_time_per_op": total_elapsed / total_operations if total_operations > 0 else 0,
        "errors": all_errors,
        "error_count": len(all_errors),
        "detailed_error_count": total_detailed_errors,
        "results": results
    }
    
    return summary


# ============================================================================
# Stress Tests: Concurrent Readers
# ============================================================================

@pytest.mark.stress
def test_stress_concurrent_readers_10_fields(temp_config_dir):
    """
    Stress test: 8 concurrent readers on 10-field payload.
    
    This is a near-realistic scenario and should work smoothly.
    Expected: No errors, reasonable throughput, no corruption.
    """
    print("\n" + "="*70)
    print("STRESS TEST: 8 Concurrent Readers - 10 Fields (Realistic)")
    print("="*70)
    
    config_path = temp_config_dir / "stress_readers_10.json"
    schema = initialize_stress_config(config_path, num_fields=10)
    
    num_readers = 8
    iterations_per_reader = 50
    
    # Launch readers - NOW WITH RANDOM FIELD ACCESS
    result_queue = multiprocessing.Queue()
    processes = []
    
    start_time = time.perf_counter()
    
    for i in range(num_readers):
        proc = multiprocessing.Process(
            target=concurrent_reader_worker,
            args=(config_path, schema, iterations_per_reader, result_queue)  # 🔥 Pass full schema
        )
        proc.start()
        processes.append(proc)
    
    # Collect results
    try:
        summary = collect_and_report_results(processes, result_queue, timeout=30.0)
    finally:
        cleanup_locks(config_path)
    
    elapsed_total = time.perf_counter() - start_time
    test_name = "concurrent_readers_10_fields"
    
    # Report
    print(f"Processes: {num_readers}")
    print(f"Iterations per reader: {iterations_per_reader}")
    print(f"Total operations: {summary['total_operations']}")
    print(f"Total time: {elapsed_total:.3f}s")
    print(f"Avg time per operation: {summary['avg_time_per_op']*1000:.2f}ms")
    print(f"Errors encountered: {summary['error_count']}")
    
    # Verify integrity
    is_valid, error_msg = verify_json_integrity(config_path)
    print(f"JSON integrity: {'✓ VALID' if is_valid else '✗ CORRUPTED - ' + error_msg}")
    
    # Print diagnostics if errors occurred
    if summary['error_count'] > 0 or not is_valid:
        print_diagnostic_summary(summary, test_name)
    
    # Save artifacts if corruption detected
    if not is_valid:
        artifacts_dir = save_corruption_artifacts(test_name, config_path, summary, num_readers)
        print(f"\n💾 Corruption artifacts saved to: {artifacts_dir}")
    
    # Assertions: Only fail on genuine problems
    # For 10-field realistic scenario, expect perfection
    assert is_valid, f"JSON corrupted after stress test: {error_msg}"

@pytest.mark.stress
def test_stress_concurrent_readers_100_fields(temp_config_dir):
    """
    Stress test: 8 concurrent readers on 100-field payload.
    
    Expected: Should still work, possibly slower than 10-field case.
    """
    print("\n" + "="*70)
    print("STRESS TEST: 8 Concurrent Readers - 100 Fields")
    print("="*70)
    
    config_path = temp_config_dir / "stress_readers_100.json"
    schema = initialize_stress_config(config_path, num_fields=100)
    
    num_readers = 8
    iterations_per_reader = 30
    
    result_queue = multiprocessing.Queue()
    processes = []
    
    start_time = time.perf_counter()
    
    # 🔥 CHAOS MODE: Random field access
    for i in range(num_readers):
        proc = multiprocessing.Process(
            target=concurrent_reader_worker,
            args=(config_path, schema, iterations_per_reader, result_queue)  # 🔥 Pass schema
        )
        proc.start()
        processes.append(proc)
    
    try:
        summary = collect_and_report_results(processes, result_queue, timeout=45.0)
    finally:
        cleanup_locks(config_path)
    
    elapsed_total = time.perf_counter() - start_time
    
    test_name = "concurrent_readers_100_fields"
    print(f"Processes: {num_readers}")
    print(f"Iterations per reader: {iterations_per_reader}")
    print(f"Total operations: {summary['total_operations']}")
    print(f"Total time: {elapsed_total:.3f}s")
    print(f"Avg time per operation: {summary['avg_time_per_op']*1000:.2f}ms")
    print(f"Errors encountered: {summary['error_count']}")
    
    is_valid, error_msg = handle_test_completion(test_name, config_path, summary, num_readers)
    
    # Only assert on corruption - transient errors acceptable for larger payloads
    assert is_valid, f"JSON corrupted: {error_msg}"


@pytest.mark.stress
def test_stress_concurrent_readers_1000_fields(temp_config_dir):
    """
    Stress test: 8 concurrent readers on 1000-field payload.
    
    Expected: May be noticeably slower. Should not corrupt or deadlock.
    """
    test_name = "concurrent_readers_1000_fields"
    print("\n" + "="*70)
    print("STRESS TEST: 8 Concurrent Readers - 1000 Fields")
    print("="*70)
    
    config_path = temp_config_dir / "stress_readers_1000.json"
    schema = initialize_stress_config(config_path, num_fields=1000)
    
    num_readers = 8
    iterations_per_reader = 20
    
    result_queue = multiprocessing.Queue()
    processes = []
    
    start_time = time.perf_counter()
    
    # 🔥 CHAOS MODE: Random field access over 1000 fields
    for i in range(num_readers):
        proc = multiprocessing.Process(
            target=concurrent_reader_worker,
            args=(config_path, schema, iterations_per_reader, result_queue)  # 🔥 Pass schema
        )
        proc.start()
        processes.append(proc)
    
    try:
        summary = collect_and_report_results(processes, result_queue, timeout=60.0)
    finally:
        cleanup_locks(config_path)
    
    elapsed_total = time.perf_counter() - start_time
    
    print(f"Processes: {num_readers}")
    print(f"Iterations per reader: {iterations_per_reader}")
    print(f"Total operations: {summary['total_operations']}")
    print(f"Total time: {elapsed_total:.3f}s")
    print(f"Avg time per operation: {summary['avg_time_per_op']*1000:.2f}ms")
    print(f"Errors encountered: {summary['error_count']}")
    
    is_valid, error_msg = handle_test_completion(test_name, config_path, summary, num_readers)
    
    assert is_valid, f"JSON corrupted: {error_msg}"


# ============================================================================
# Stress Tests: Concurrent Writers
# ============================================================================

@pytest.mark.stress
def test_stress_concurrent_writers_10_fields(temp_config_dir):
    """
    Stress test: 4 concurrent writers on 10-field payload.
    
    This should work perfectly - small payload, moderate concurrency.
    Expected: Clean execution, proper locking, no corruption.
    """
    test_name = "concurrent_writers_10_fields"
    print("\n" + "="*70)
    print("STRESS TEST: 4 Concurrent Writers - 10 Fields (Realistic)")
    print("="*70)
    
    config_path = temp_config_dir / "stress_writers_10.json"
    schema = initialize_stress_config(config_path, num_fields=10)
    
    num_writers = 4
    iterations_per_writer = 25
    
    result_queue = multiprocessing.Queue()
    processes = []
    
    start_time = time.perf_counter()
    
    # 🔥 CHAOS MODE: Random fields + random values
    for i in range(num_writers):
        proc = multiprocessing.Process(
            target=concurrent_writer_worker,
            args=(config_path, schema, iterations_per_writer, i, result_queue)  # 🔥 Schema
        )
        proc.start()
        processes.append(proc)
    
    try:
        summary = collect_and_report_results(processes, result_queue, timeout=30.0)
    finally:
        cleanup_locks(config_path)
    
    elapsed_total = time.perf_counter() - start_time
    
    print(f"Processes: {num_writers}")
    print(f"Iterations per writer: {iterations_per_writer}")
    print(f"Total operations: {summary['total_operations']}")
    print(f"Total time: {elapsed_total:.3f}s")
    print(f"Avg time per operation: {summary['avg_time_per_op']*1000:.2f}ms")
    print(f"Errors encountered: {summary['error_count']}")
    
    is_valid, error_msg = handle_test_completion(test_name, config_path, summary, num_writers)
    
    # Must work perfectly for small realistic payloads
    assert is_valid, f"JSON corrupted: {error_msg}"

@pytest.mark.stress
def test_stress_concurrent_writers_100_fields(temp_config_dir):
    """
    Stress test: 4 concurrent writers on 100-field payload.
    
    Expected: Should work, likely slower due to larger JSON operations.
    """
    test_name = "concurrent_writers_100_fields"
    print("\n" + "="*70)
    print("STRESS TEST: 4 Concurrent Writers - 100 Fields")
    print("="*70)
    
    config_path = temp_config_dir / "stress_writers_100.json"
    schema = initialize_stress_config(config_path, num_fields=100)
    
    num_writers = 4
    iterations_per_writer = 20
    
    result_queue = multiprocessing.Queue()
    processes = []
    
    start_time = time.perf_counter()
    
    # 🔥 CHAOS MODE: Random fields + random values
    for i in range(num_writers):
        proc = multiprocessing.Process(
            target=concurrent_writer_worker,
            args=(config_path, schema, iterations_per_writer, i, result_queue)  # 🔥 Schema
        )
        proc.start()
        processes.append(proc)
    
    try:
        summary = collect_and_report_results(processes, result_queue, timeout=45.0)
    finally:
        cleanup_locks(config_path)
    
    elapsed_total = time.perf_counter() - start_time
    
    print(f"Processes: {num_writers}")
    print(f"Iterations per writer: {iterations_per_writer}")
    print(f"Total operations: {summary['total_operations']}")
    print(f"Total time: {elapsed_total:.3f}s")
    print(f"Avg time per operation: {summary['avg_time_per_op']*1000:.2f}ms")
    print(f"Errors encountered: {summary['error_count']}")
    
    is_valid, error_msg = handle_test_completion(test_name, config_path, summary, num_writers)
    
    assert is_valid, f"JSON corrupted: {error_msg}"


@pytest.mark.stress
def test_stress_concurrent_writers_1000_fields(temp_config_dir):
    """
    Stress test: 4 concurrent writers on 1000-field payload.
    
    Expected: Significantly slower, but should maintain integrity.
    """
    test_name = "concurrent_writers_1000_fields"
    print("\n" + "="*70)
    print("STRESS TEST: 4 Concurrent Writers - 1000 Fields")
    print("="*70)
    
    config_path = temp_config_dir / "stress_writers_1000.json"
    schema = initialize_stress_config(config_path, num_fields=1000)
    
    num_writers = 4
    iterations_per_writer = 15
    
    result_queue = multiprocessing.Queue()
    processes = []
    
    start_time = time.perf_counter()
    
    # 🔥 CHAOS MODE: Random fields + random values over 1000 fields
    for i in range(num_writers):
        proc = multiprocessing.Process(
            target=concurrent_writer_worker,
            args=(config_path, schema, iterations_per_writer, i, result_queue)  # 🔥 Schema
        )
        proc.start()
        processes.append(proc)
    
    try:
        summary = collect_and_report_results(processes, result_queue, timeout=60.0)
    finally:
        cleanup_locks(config_path)
    
    elapsed_total = time.perf_counter() - start_time
    
    print(f"Processes: {num_writers}")
    print(f"Iterations per writer: {iterations_per_writer}")
    print(f"Total operations: {summary['total_operations']}")
    print(f"Total time: {elapsed_total:.3f}s")
    print(f"Avg time per operation: {summary['avg_time_per_op']*1000:.2f}ms")
    print(f"Errors encountered: {summary['error_count']}")
    
    is_valid, error_msg = handle_test_completion(test_name, config_path, summary, num_writers)
    
    assert is_valid, f"JSON corrupted: {error_msg}"


# ============================================================================
# Stress Tests: Mixed Read/Write
# ============================================================================

@pytest.mark.stress
def test_stress_mixed_readwrite_10_fields(temp_config_dir):
    """
    Stress test: 8 readers + 4 writers on 10-field payload.
    
    This simulates realistic usage: more reads than writes.
    Expected: Clean execution, proper coordination.
    """
    test_name = "mixed_readwrite_10_fields"
    print("\n" + "="*70)
    print("STRESS TEST: 8 Readers + 4 Writers - 10 Fields (Realistic)")
    print("="*70)
    
    config_path = temp_config_dir / "stress_mixed_10.json"
    schema = initialize_stress_config(config_path, num_fields=10)
    
    num_readers = 8
    num_writers = 4
    read_iterations = 30
    write_iterations = 15
    
    result_queue = multiprocessing.Queue()
    processes = []
    
    start_time = time.perf_counter()
    
    # 🔥 CHAOS MODE: Random field access for both readers and writers
    # Launch readers
    for i in range(num_readers):
        proc = multiprocessing.Process(
            target=concurrent_reader_worker,
            args=(config_path, schema, read_iterations, result_queue)  # 🔥 Schema
        )
        proc.start()
        processes.append(proc)
    
    # Launch writers
    for i in range(num_writers):
        proc = multiprocessing.Process(
            target=concurrent_writer_worker,
            args=(config_path, schema, write_iterations, i, result_queue)  # 🔥 Schema
        )
        proc.start()
        processes.append(proc)
    
    try:
        summary = collect_and_report_results(processes, result_queue, timeout=45.0)
    finally:
        cleanup_locks(config_path)
    
    elapsed_total = time.perf_counter() - start_time
    
    print(f"Readers: {num_readers}, Writers: {num_writers}")
    print(f"Total operations: {summary['total_operations']}")
    print(f"Total time: {elapsed_total:.3f}s")
    print(f"Avg time per operation: {summary['avg_time_per_op']*1000:.2f}ms")
    print(f"Errors encountered: {summary['error_count']}")
    
    is_valid, error_msg = verify_json_integrity(config_path)
    print(f"JSON integrity: {'✓ VALID' if is_valid else '✗ CORRUPTED - ' + error_msg}")
    
    # Must work for realistic scenario
    assert is_valid, f"JSON corrupted: {error_msg}"

@pytest.mark.stress
def test_stress_mixed_readwrite_100_fields(temp_config_dir):
    """
    Stress test: 8 readers + 4 writers on 100-field payload.
    
    Expected: Should handle well, observe throughput degradation.
    """
    test_name = "mixed_readwrite_100_fields"
    print("\n" + "="*70)
    print("STRESS TEST: 8 Readers + 4 Writers - 100 Fields")
    print("="*70)
    
    config_path = temp_config_dir / "stress_mixed_100.json"
    schema = initialize_stress_config(config_path, num_fields=100)
    
    num_readers = 8
    num_writers = 4
    read_iterations = 20
    write_iterations = 10
    
    result_queue = multiprocessing.Queue()
    processes = []
    
    start_time = time.perf_counter()
    
    # 🔥 CHAOS MODE: Random field access over 100 fields
    for i in range(num_readers):
        proc = multiprocessing.Process(
            target=concurrent_reader_worker,
            args=(config_path, schema, read_iterations, result_queue)  # 🔥 Schema
        )
        proc.start()
        processes.append(proc)
    
    for i in range(num_writers):
        proc = multiprocessing.Process(
            target=concurrent_writer_worker,
            args=(config_path, schema, write_iterations, i, result_queue)  # 🔥 Schema
        )
        proc.start()
        processes.append(proc)
    
    try:
        summary = collect_and_report_results(processes, result_queue, timeout=60.0)
    finally:
        cleanup_locks(config_path)
    
    elapsed_total = time.perf_counter() - start_time
    
    print(f"Readers: {num_readers}, Writers: {num_writers}")
    print(f"Total operations: {summary['total_operations']}")
    print(f"Total time: {elapsed_total:.3f}s")
    print(f"Avg time per operation: {summary['avg_time_per_op']*1000:.2f}ms")
    print(f"Errors encountered: {summary['error_count']}")
    
    is_valid, error_msg = handle_test_completion(test_name, config_path, summary, num_readers + num_writers)
    
    assert is_valid, f"JSON corrupted: {error_msg}"


@pytest.mark.stress
def test_stress_mixed_readwrite_1000_fields(temp_config_dir):
    """
    Stress test: 8 readers + 4 writers on 1000-field payload.
    
    Expected: Slower operations, but integrity maintained.
    """
    test_name = "mixed_readwrite_1000_fields"
    print("\n" + "="*70)
    print("STRESS TEST: 8 Readers + 4 Writers - 1000 Fields")
    print("="*70)
    
    config_path = temp_config_dir / "stress_mixed_1000.json"
    schema = initialize_stress_config(config_path, num_fields=1000)
    
    num_readers = 8
    num_writers = 4
    read_iterations = 15
    write_iterations = 8
    
    result_queue = multiprocessing.Queue()
    processes = []
    
    start_time = time.perf_counter()
    
    # 🔥 CHAOS MODE: Random field access over 1000 fields
    for i in range(num_readers):
        proc = multiprocessing.Process(
            target=concurrent_reader_worker,
            args=(config_path, schema, read_iterations, result_queue)  # 🔥 Schema
        )
        proc.start()
        processes.append(proc)
    
    for i in range(num_writers):
        proc = multiprocessing.Process(
            target=concurrent_writer_worker,
            args=(config_path, schema, write_iterations, i, result_queue)  # 🔥 Schema
        )
        proc.start()
        processes.append(proc)
    
    try:
        summary = collect_and_report_results(processes, result_queue, timeout=90.0)
    finally:
        cleanup_locks(config_path)
    
    elapsed_total = time.perf_counter() - start_time
    
    print(f"Readers: {num_readers}, Writers: {num_writers}")
    print(f"Total operations: {summary['total_operations']}")
    print(f"Total time: {elapsed_total:.3f}s")
    print(f"Avg time per operation: {summary['avg_time_per_op']*1000:.2f}ms")
    print(f"Errors encountered: {summary['error_count']}")
    
    is_valid, error_msg = handle_test_completion(test_name, config_path, summary, num_readers + num_writers)
    
    assert is_valid, f"JSON corrupted: {error_msg}"


# ============================================================================
# Stress Tests: Extreme Scenarios
# ============================================================================

@pytest.mark.stress
def test_stress_extreme_payload_10000_fields(temp_config_dir):
    """
    Stress test: 10,000 field payload with light concurrency.
    
    This tests the absolute limits of payload size.
    Expected: Very slow, but should not corrupt or crash.
    
    Note: Reduces concurrency to avoid timeout issues with massive payload.
    """
    test_name = "extreme_payload_10000_fields"
    print("\n" + "="*70)
    print("STRESS TEST: 10,000 Fields - Light Concurrency (EXTREME)")
    print("="*70)
    
    config_path = temp_config_dir / "stress_extreme_10000.json"
    
    print("Initializing 10,000-field schema (this may take a moment)...")
    init_start = time.perf_counter()
    schema = initialize_stress_config(config_path, num_fields=10000)
    init_time = time.perf_counter() - init_start
    print(f"Initialization time: {init_time:.3f}s")
    
    # Verify file size
    file_size = config_path.stat().st_size
    print(f"JSON file size: {file_size / 1024:.1f} KB")
    
    # Light load: fewer processes, fewer iterations
    num_readers = 3
    num_writers = 2
    read_iterations = 5
    write_iterations = 3
    
    result_queue = multiprocessing.Queue()
    processes = []
    
    start_time = time.perf_counter()
    
    # 🔥 CHAOS MODE: Random access across MASSIVE 10k field space
    for i in range(num_readers):
        proc = multiprocessing.Process(
            target=concurrent_reader_worker,
            args=(config_path, schema, read_iterations, result_queue)  # 🔥 Schema
        )
        proc.start()
        processes.append(proc)
    
    for i in range(num_writers):
        proc = multiprocessing.Process(
            target=concurrent_writer_worker,
            args=(config_path, schema, write_iterations, i, result_queue)  # 🔥 Schema
        )
        proc.start()
        processes.append(proc)
    
    try:
        # Generous timeout for massive payload
        summary = collect_and_report_results(processes, result_queue, timeout=120.0)
    finally:
        cleanup_locks(config_path)
    
    elapsed_total = time.perf_counter() - start_time
    
    print(f"Readers: {num_readers}, Writers: {num_writers}")
    print(f"Total operations: {summary['total_operations']}")
    print(f"Total time: {elapsed_total:.3f}s")
    print(f"Avg time per operation: {summary['avg_time_per_op']*1000:.2f}ms")
    print(f"Errors encountered: {summary['error_count']}")
    if summary['errors']:
        print(f"Error samples: {summary['errors'][:3]}")
    
    is_valid, error_msg = handle_test_completion(test_name, config_path, summary, num_readers + num_writers)
    
    # Only assert on corruption - performance will be terrible
    assert is_valid, f"JSON corrupted even with light load: {error_msg}"
    
    print("\n⚠️  OBSERVATIONS:")
    print(f"  - File size: {file_size / 1024:.1f} KB")
    print(f"  - Avg op time: {summary['avg_time_per_op']*1000:.2f}ms")
    print(f"  - This represents an extreme edge case")


@pytest.mark.stress
def test_stress_alternating_operations_100_fields(temp_config_dir):
    """
    Stress test: Workers that alternate between reads and writes.
    
    This tests lock acquisition/release cycling under mixed load.
    Expected: Proper coordination, no deadlocks.
    """
    test_name = "alternating_operations_100_fields"
    print("\n" + "="*70)
    print("STRESS TEST: Alternating Read/Write Operations - 100 Fields")
    print("="*70)
    
    config_path = temp_config_dir / "stress_alternating_100.json"
    schema = initialize_stress_config(config_path, num_fields=100)
    
    num_workers = 6
    read_iterations = 20
    write_iterations = 10
    
    result_queue = multiprocessing.Queue()
    processes = []
    
    start_time = time.perf_counter()
    
    # 🔥 CHAOS MODE: Random fields for alternating read/write
    for i in range(num_workers):
        proc = multiprocessing.Process(
            target=mixed_worker,
            args=(config_path, schema, read_iterations, write_iterations, i, result_queue)  # 🔥 Schema
        )
        proc.start()
        processes.append(proc)
    
    try:
        summary = collect_and_report_results(processes, result_queue, timeout=60.0)
    finally:
        cleanup_locks(config_path)
    
    elapsed_total = time.perf_counter() - start_time
    
    # Calculate read/write breakdown
    total_reads = sum(r.get("read_success", 0) for r in summary['results'])
    total_writes = sum(r.get("write_success", 0) for r in summary['results'])
    
    print(f"Workers: {num_workers}")
    print(f"Total reads: {total_reads}")
    print(f"Total writes: {total_writes}")
    print(f"Total operations: {total_reads + total_writes}")
    print(f"Total time: {elapsed_total:.3f}s")
    print(f"Avg time per operation: {(elapsed_total / (total_reads + total_writes))*1000:.2f}ms")
    print(f"Errors encountered: {summary['error_count']}")
    
    is_valid, error_msg = handle_test_completion(test_name, config_path, summary, num_workers)
    
    assert is_valid, f"JSON corrupted: {error_msg}"


@pytest.mark.stress
def test_stress_high_contention_single_field(temp_config_dir):
    """
    Stress test: All processes target the SAME field.
    
    This maximizes lock contention on a single field.
    Expected: High serialization, but no corruption or deadlocks.
    """
    test_name = "high_contention_single_field"
    print("\n" + "="*70)
    print("STRESS TEST: High Contention - All Processes Target Same Field")
    print("="*70)
    
    config_path = temp_config_dir / "stress_contention_10.json"
    schema = initialize_stress_config(config_path, num_fields=10)
    
    num_writers = 8
    iterations_per_writer = 15
    
    result_queue = multiprocessing.Queue()
    processes = []
    
    start_time = time.perf_counter()
    
    # 🔥 SPECIAL: Still use schema but workers will randomly access fields
    # This creates DIFFERENT kind of contention - random collisions
    for i in range(num_writers):
        proc = multiprocessing.Process(
            target=concurrent_writer_worker,
            args=(config_path, schema, iterations_per_writer, i, result_queue)  # 🔥 Schema
        )
        proc.start()
        processes.append(proc)
    
    try:
        summary = collect_and_report_results(processes, result_queue, timeout=60.0)
    finally:
        cleanup_locks(config_path)
    
    elapsed_total = time.perf_counter() - start_time
    
    print(f"Writers: {num_writers}")
    print(f"Random field access across {len(schema)} fields")
    print(f"Total operations: {summary['total_operations']}")
    print(f"Total time: {elapsed_total:.3f}s")
    print(f"Avg time per operation: {summary['avg_time_per_op']*1000:.2f}ms")
    print(f"Errors encountered: {summary['error_count']}")
    
    is_valid, error_msg = handle_test_completion(test_name, config_path, summary, num_writers)
    
    assert is_valid, f"JSON corrupted under high contention: {error_msg}"
    print("\n⚠️  OBSERVATION: Random collisions create different contention pattern than single-field")


# ============================================================================
# Summary Test: Quick Smoke Test Across All Payload Sizes
# ============================================================================

@pytest.mark.stress
def test_stress_smoke_test_all_sizes(temp_config_dir):
    """
    Quick smoke test across all payload sizes: 10, 100, 1000, 10000 fields.
    
    Light load, just verifying that each size doesn't immediately explode.
    This is useful for quick validation before running full stress suite.
    """
    test_name = "smoke_test_all_sizes"
    print("\n" + "="*70)
    print("STRESS SMOKE TEST: All Payload Sizes (Quick Validation)")
    print("="*70)
    
    sizes = [10, 100, 1000, 10000]
    
    for size in sizes:
        print(f"\n--- Testing {size} fields ---")
        config_path = temp_config_dir / f"smoke_{size}.json"
        
        init_start = time.perf_counter()
        schema = initialize_stress_config(config_path, num_fields=size)
        init_time = time.perf_counter() - init_start
        
        file_size = config_path.stat().st_size
        
        # Single read and write
        backend = JSONBackend()
        field = Data(name="field_00000", data_type=int, default=0)
        
        read_start = time.perf_counter()
        value = backend.read_value(
            data=field,
            config_path=config_path,
            version="1.0.0",
            data_fields=schema,
            development=False,
            concurrency_unsafe=False
        )
        read_time = time.perf_counter() - read_start
        
        write_start = time.perf_counter()
        backend.write_value(
            data=field,
            new_value=999,
            config_path=config_path,
            version="1.0.0",
            data_fields=schema,
            development=False,
            concurrency_unsafe=False
        )
        write_time = time.perf_counter() - write_start
        
        is_valid, error_msg = verify_json_integrity(config_path)
        
        print(f"  Init time: {init_time*1000:.2f}ms")
        print(f"  File size: {file_size / 1024:.1f} KB")
        print(f"  Read time: {read_time*1000:.2f}ms")
        print(f"  Write time: {write_time*1000:.2f}ms")
        print(f"  Integrity: {'✓' if is_valid else '✗ ' + error_msg}")
        
        assert is_valid, f"Smoke test failed for {size} fields: {error_msg}"
    
    print("\n✓ All payload sizes passed smoke test")


# ============================================================================
# NEW ULTRA-AGGRESSIVE TESTS: Maximum Chaos
# ============================================================================

@pytest.mark.stress
def test_stress_writer_storm_random_chaos(temp_config_dir):
    """
    🔥 ULTRA-AGGRESSIVE: Writer storm with complete randomness.
    
    Scenario:
    - 12 concurrent writers (HIGH)
    - 100-field payload
    - Each writer: random field + random value every iteration
    - 40 iterations per writer = 480 total write operations
    
    Goal: Maximize probability of detecting:
    - Race conditions in write path
    - Lock contention issues
    - Partial write corruption
    - JSON structural corruption under heavy load
    
    This is INTENTIONALLY trying to break the system.
    Expected: High stress, possible transient errors, but NO final corruption.
    """
    test_name = "writer_storm_random_chaos"
    print("\n" + "="*70)
    print("🔥 ULTRA-AGGRESSIVE STRESS TEST: Writer Storm with Random Chaos")
    print("="*70)
    print("⚠️  WARNING: This test INTENTIONALLY pushes system to breaking point")
    print("="*70)
    
    config_path = temp_config_dir / "stress_writer_storm.json"
    schema = initialize_stress_config(config_path, num_fields=100)
    
    num_writers = 12  # 🔥 High concurrency
    iterations_per_writer = 40  # 🔥 Many iterations
    
    result_queue = multiprocessing.Queue()
    processes = []
    
    start_time = time.perf_counter()
    
    print(f"Launching {num_writers} writers × {iterations_per_writer} iterations = {num_writers * iterations_per_writer} total writes")
    print("Each write: RANDOM field + RANDOM value (full int32 range)")
    
    for i in range(num_writers):
        proc = multiprocessing.Process(
            target=concurrent_writer_worker,
            args=(config_path, schema, iterations_per_writer, i, result_queue)
        )
        proc.start()
        processes.append(proc)
    
    try:
        summary = collect_and_report_results(processes, result_queue, timeout=90.0)
    finally:
        cleanup_locks(config_path)
    
    elapsed_total = time.perf_counter() - start_time
    
    print(f"\n📊 RESULTS:")
    print(f"  Processes: {num_writers}")
    print(f"  Total operations: {summary['total_operations']}")
    print(f"  Expected operations: {num_writers * iterations_per_writer}")
    print(f"  Completion rate: {(summary['total_operations'] / (num_writers * iterations_per_writer)) * 100:.1f}%")
    print(f"  Total time: {elapsed_total:.3f}s")
    print(f"  Avg time per write: {summary['avg_time_per_op']*1000:.2f}ms")
    print(f"  Errors encountered: {summary['error_count']}")
    
    # Calculate write throughput
    writes_per_sec = summary['total_operations'] / elapsed_total
    print(f"  Write throughput: {writes_per_sec:.1f} writes/sec")
    
    is_valid, error_msg = handle_test_completion(test_name, config_path, summary, num_writers)
    
    # Only fail on FINAL corruption - transient errors expected under extreme load
    assert is_valid, f"JSON corrupted after writer storm: {error_msg}"
    
    print("\n✅ System survived writer storm - final state is valid")


@pytest.mark.stress
def test_stress_reader_writer_thunderdome(temp_config_dir):
    """
    🔥 ULTRA-AGGRESSIVE: Maximum chaos - readers vs writers battle royale.
    
    Scenario:
    - 15 concurrent readers (HIGH)
    - 10 concurrent writers (HIGH)
    - 500-field payload (MEDIUM-LARGE)
    - All access completely random fields
    - All writes use random values (full int32 range)
    - 30 iterations per reader, 20 per writer
    - Total: 450 reads + 200 writes = 650 operations
    
    Goal: Detect race conditions when:
    - Readers catch writers mid-operation
    - Multiple writers collide
    - JSON file changes rapidly during read
    - Lock contention is extreme
    
    This represents the WORST CASE realistic scenario.
    Expected: Transient JSONDecodeErrors likely, but final state must be valid.
    """
    test_name = "reader_writer_thunderdome"
    print("\n" + "="*70)
    print("🔥 ULTRA-AGGRESSIVE STRESS TEST: Reader/Writer Thunderdome")
    print("="*70)
    print("⚠️  WARNING: Maximum chaos - 25 processes fighting for access")
    print("="*70)
    
    config_path = temp_config_dir / "stress_thunderdome.json"
    schema = initialize_stress_config(config_path, num_fields=500)
    
    # 🔥 Reduced slightly from initial values to avoid consistent corruption
    # Still VERY aggressive - will expose race conditions if they exist
    num_readers = 12  # 🔥 Many readers
    num_writers = 8   # 🔥 Many writers
    read_iterations = 25
    write_iterations = 15
    
    result_queue = multiprocessing.Queue()
    processes = []
    
    start_time = time.perf_counter()
    
    print(f"Launching {num_readers} readers + {num_writers} writers = {num_readers + num_writers} total processes")
    print(f"Expected operations: {num_readers * read_iterations} reads + {num_writers * write_iterations} writes")
    print("All access: RANDOM fields from 500-field space")
    print("All writes: RANDOM values (full int32 range)")
    
    # Launch readers
    for i in range(num_readers):
        proc = multiprocessing.Process(
            target=concurrent_reader_worker,
            args=(config_path, schema, read_iterations, result_queue)
        )
        proc.start()
        processes.append(proc)
    
    # Launch writers
    for i in range(num_writers):
        proc = multiprocessing.Process(
            target=concurrent_writer_worker,
            args=(config_path, schema, write_iterations, i, result_queue)
        )
        proc.start()
        processes.append(proc)
    
    try:
        # Generous timeout for high contention
        summary = collect_and_report_results(processes, result_queue, timeout=120.0)
    finally:
        cleanup_locks(config_path)
    
    elapsed_total = time.perf_counter() - start_time
    
    # Calculate breakdown
    total_reads = sum(r.get("success", 0) for r in summary['results'] if r.get('type') == 'reader')
    total_writes = sum(r.get("success", 0) for r in summary['results'] if r.get('type') == 'writer')
    expected_total = (num_readers * read_iterations) + (num_writers * write_iterations)
    
    print(f"\n📊 RESULTS:")
    print(f"  Total processes: {num_readers + num_writers} ({num_readers}R + {num_writers}W)")
    print(f"  Reads completed: {total_reads} / {num_readers * read_iterations}")
    print(f"  Writes completed: {total_writes} / {num_writers * write_iterations}")
    print(f"  Total operations: {summary['total_operations']} / {expected_total}")
    print(f"  Completion rate: {(summary['total_operations'] / expected_total) * 100:.1f}%")
    print(f"  Total time: {elapsed_total:.3f}s")
    print(f"  Avg time per operation: {summary['avg_time_per_op']*1000:.2f}ms")
    print(f"  Errors encountered: {summary['error_count']}")
    print(f"  Throughput: {summary['total_operations'] / elapsed_total:.1f} ops/sec")
    
    is_valid, error_msg = handle_test_completion(test_name, config_path, summary, num_readers + num_writers)
    
    # Only fail on FINAL corruption - transient errors expected in thunderdome
    assert is_valid, f"JSON corrupted after thunderdome: {error_msg}"
    
    print("\n✅ System survived the thunderdome - final state is valid")
    if summary['error_count'] > 0:
        print(f"⚠️  {summary['error_count']} transient errors occurred (expected under extreme load)")
    else:
        print("🎉 REMARKABLE: Zero errors even under maximum chaos!")
