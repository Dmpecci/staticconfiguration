# staticconfiguration

A Python library for declarative, persistent, and type-safe configuration with static access semantics. Designed for the small subset of application state that is genuinely global, needs early availability, and must persist across sessions.

---

## Motivation

In applications that extensively use dependency injection (DI), there exists a narrow category of values that sit uncomfortably within the DI graph:

- They are data, not behavioral dependencies.
- They are accessed by components that do not depend on each other.
- They must be available during early initialization, before DI containers are fully wired.
- They must persist across application restarts.
- They represent configuration in the true sense: values that define how the application behaves, not what it operates on.

Common examples: theme preferences, log levels, network timeouts, feature flags, application-wide display settings.

Forcing these values through DI often leads to awkward patterns:
- Passing configuration objects through long constructor chains.
- Injecting a "configuration service" into unrelated components solely to access one or two values.
- Creating artificial dependencies between modules that should remain independent.

This library exists for that specific case: a small, explicitly defined set of global configuration values that are accessed statically, validated declaratively, and persisted automatically.

---

### Relationship to Dependency Injection

**This library is not an alternative to DI. It is a complement.**

Dependency Injection should be your default architecture. The vast majority (≈99%) of your application should rely on DI to manage dependencies, enforce contracts, and enable testing.

`staticconfiguration` addresses the remaining ≈1%: values that are:
- **Global by nature** (e.g., application theme, default timeout).
- **Shared across unrelated subsystems** (no natural dependency relationship).
- **Configuration data**, not services or strategies.

If you can comfortably pass a value through DI without contorting your design, do so. If forcing a value into DI makes your architecture worse, consider this library.

---

### Explicit Configuration State (not accidental globals)

In real-world projects, when certain values do not fit cleanly into Dependency Injection,
they often end up being handled in ad-hoc ways:

- mutable module-level variables
- a `settings.py` file living entirely in memory
- loosely structured JSON files accessed concurrently
- hand-written migration logic
- implicit assumptions about initialization order

These approaches introduce *accidental global state*: mutable, implicit, and fragile,
with no clear contracts or long-term guarantees.

This library exists to replace those patterns.

`staticconfiguration` introduces a small, explicit configuration state that is:

- declared upfront in a typed schema
- validated on every access
- persisted in a controlled way
- governed by clear, documented invariants

The goal is not to encourage global state, but to **eliminate accidental global state**
by making configuration explicit, constrained, and intentional.

In short: configuration state by design, not globals by accident.

---

## What This Library Is

A declarative configuration system with:

- **Static access**: Configuration is accessed via class methods, not instances.
- **Persistent storage**: Values are stored in a JSON file and survive application restarts.
- **Schema versioning**: Configuration evolves with your application through explicit versioning and automatic migration.
- **Type validation**: Values are checked against their declared types.
- **Concurrency safety**: Reads and writes are serialized to prevent corruption.
- **Corruption recovery**: Unreadable files are reset to defaults with explicit warning.

---

## What This Library Is Not

This is not:
- A general-purpose key-value store.
- A distributed configuration system.
- A secrets management tool.
- A settings UI framework.
- A configuration server or service.

This library does not support:
- Distributed locking (multi-machine environments).
- Transactional updates (multiple fields atomically).
- Real-time synchronization across processes.
- Network filesystems for concurrent access (NFS, SMB).

---

## Installation

```bash
pip install staticconfiguration
```

Requires Python ≥ 3.10 and `psutil` for process detection.

---

## Basic Usage

### Defining a Configuration Class

```python
from staticconfiguration import staticconfig, Data

@staticconfig
class AppSettings:
    __config_path__ = "~/.config/myapp"
    __config_file__ = "settings.json"
    __version__ = "1.0.0"
    __development__ = False

    api_url = Data(name="api_url", data_type=str, default="https://api.example.com")
    timeout = Data(name="timeout", data_type=int, default=30)
    debug_mode = Data(name="debug_mode", data_type=bool, default=False)
```

**Required attributes:**
- `__config_path__`: Directory where the configuration file will be stored.
- `__config_file__`: Name of the JSON file.
- `__version__`: Schema version string. Change this when fields are added, removed, or types change.
- `__development__`: Boolean. When `True`, schema is aggressively migrated even if version matches (useful during active development).

**Configuration fields:**
- Declared using `Data(name, data_type, default)`.
- `name`: Field name in the JSON file.
- `data_type`: Python type (e.g., `str`, `int`, `bool`, `list`, `dict`).
- `default`: Fallback value used when the field is not yet set or after corruption recovery.

### Reading Values

```python
# Read a value
current_timeout = AppSettings.get(AppSettings.timeout)
print(current_timeout)  # 30 (or current persisted value)
```

### Writing Values

```python
# Update a value
AppSettings.set(AppSettings.timeout, 60)

# Value is immediately persisted to disk
```

### Type Safety

```python
# This will raise TypeError
AppSettings.set(AppSettings.timeout, "not_an_int")  # TypeError: Value must be of type int
```

---

## Encoders and Decoders

For types that are not directly JSON-serializable, you can provide custom encoders and decoders:

```python
from datetime import datetime

def encode_datetime(dt: datetime) -> str:
    return dt.isoformat()

def decode_datetime(s: str) -> datetime:
    return datetime.fromisoformat(s)

@staticconfig
class AppSettings:
    __config_path__ = "~/.config/myapp"
    __config_file__ = "settings.json"
    __version__ = "1.0.0"
    __development__ = False

    last_sync = Data(
        name="last_sync",
        data_type=datetime,
        default=datetime(2024, 1, 1),
        encoder=encode_datetime,
        decoder=decode_datetime
    )
```

**Encoder** is called when writing a value to JSON.  
**Decoder** is called when reading a value from JSON.

---

## Persistence and Concurrency

### File Structure

Configuration is stored as JSON:

```json
{
  "version": "1.0.0",
  "created": "2024-12-20T10:30:00Z",
  "last_modified": "2024-12-20T11:45:00Z",
  "data": {
    "api_url": "https://api.example.com",
    "timeout": 60,
    "debug_mode": false
  }
}
```

### Concurrency Model

By default, all reads and writes acquire an exclusive file lock:

- **Write operations** are serialized. Multiple processes attempting to write will queue.
- **Read operations** acquire the lock to ensure they do not read partial writes.
- **Lock mechanism**: File-based lock with PID and timestamp. Lock files are automatically broken after 10 seconds (TTL) or when the owning process no longer exists.

This design prioritizes correctness over latency. Under high contention, operations may be delayed but will not corrupt data.

### Atomic Writes

Writes use a temporary file and atomic rename:

```
1. Write to config.json.tmp
2. Atomically replace config.json with config.json.tmp
```

This ensures readers never see partial writes, even if the writing process crashes mid-write.

---

## Corruption Recovery

If the JSON file becomes unreadable (due to external tampering, disk failure, or power loss), the library will:

1. Create a timestamped backup of the corrupted file: `settings_2024-12-20_10-30-00.json.corruptedbackup`
2. Reset the file to defaults based on the declared schema.
3. Emit `ConfigurationResetWarning`.
4. Continue operation normally.

**This recovery is never silent.** You will always see a warning if corruption is detected.

**Example:**

```python
import warnings
from staticconfiguration.exceptions import ConfigurationResetWarning

warnings.simplefilter("always", ConfigurationResetWarning)

# If corruption occurs:
# UserWarning: Configuration file was corrupted and has been reset to defaults.
value = AppSettings.get(AppSettings.timeout)  # Returns default value
```

The library does not attempt to repair corrupted JSON. It assumes the file is unrecoverable and resets to a known good state.

---

## Schema Evolution and Migration

When you change your configuration schema (add fields, remove fields, change types), increment `__version__`:

```python
@staticconfig
class AppSettings:
    __config_path__ = "~/.config/myapp"
    __config_file__ = "settings.json"
    __version__ = "2.0.0"  # Incremented from 1.0.0
    __development__ = False

    # New field
    max_retries = Data(name="max_retries", data_type=int, default=3)
    
    # Existing fields
    api_url = Data(name="api_url", data_type=str, default="https://api.example.com")
    timeout = Data(name="timeout", data_type=int, default=30)
```

``__version__`` is a free string, format is whatever the developer wants, it justs checks if the strings are equal, doesn't have an increasing or decreasing version detection, if the class version differs from the .json file version, it automatically migrates. 
It is recommended that you update it when you actually change the schema, then, the final user's app, when detecting a different version, will automatically update their configurations, making distribution of new updates easier, without complex logic, or ad-hoc rules.

You are free to update version every update you do, but if you don't actually change the schema, it's like doing nothing, just an unnecessary migration for the final user.

On the next read or write, the library will automatically:

- **Add new fields** with their default values.
- **Remove fields** that no longer exist in the schema.
- **Update timestamps** (`last_modified` is set to the current time).
- **Type changes** are made if a variable is detected with same name, and different type. It will try a type cast, if positive, will update the value, if negative, just uses default value from the schema. If encoders or decoders are given, it will use them, but its correct functioning is developer's responsability.

### Development Mode

During active development, set `__development__ = True`:

```python
@staticconfig
class AppSettings:
    __config_path__ = "~/.config/myapp"
    __config_file__ = "settings.json"
    __version__ = "1.0.0"
    __development__ = True  # Forces migration even if version matches
```
This is really useful during active development, when you try different schemas, change data types, you won't have to change every run the `__version__` string.
This forces migration on every access, even if `__version__` has not changed.

**Do not use `__development__ = True` in production.** If you push to production branch with this flag active, your final users will be migrating the schema every operation get/set is done.

---

## The `concurrency_unsafe` Parameter

**WARNING: This is a serious footgun.**  
Use **only** if you fully understand the consequences. Issues caused while this
parameter is enabled will **NOT** be supported.

Both `get()` and `set()` accept an optional `concurrency_unsafe` parameter:

```python
# Bypass locking (NOT SAFE for concurrent access)
value = AppSettings.get(AppSettings.timeout, concurrency_unsafe=True)
AppSettings.set(AppSettings.timeout, 60, concurrency_unsafe=True)
```

When `concurrency_unsafe=True`:

- No file lock is acquired.
- Operations bypass all concurrency protection.
- Data integrity is NOT guaranteed.
- Concurrent access can lead to data loss and JSON corruption.
- The performance benefit is negligible in normal usage.
    Disabling locking only removes an extremely rare worst-case delay (e.g. recovery
    from a stale lock), at the cost of making corruption under concurrent access
    almost certain.

Use only when:

- You are absolutely certain that only one process will ever access the configuration.
- You are operating in a strictly single-threaded, single-process context.
- You fully accept the risk of data loss.

Do NOT use when:

- Multiple processes may access the configuration.
- Multiple instances of the application can run simultaneously.
- You are not 100% sure.

This flag exists deliberately.  
You are given the power — but with great power comes great responsibility.

---

## When to Use This Library

Use `staticconfiguration` when:

- You have a small set of values that are genuinely global in scope.
- Values must persist across application restarts.
- Values are accessed by multiple, unrelated components.
- You need type safety and schema validation.
- Your application runs on a single machine (desktop apps, CLI tools, local services).

**Good use cases:**
- Desktop application preferences (theme, window size, last opened file).
- CLI tool defaults (output format, verbosity level).
- Single-instance services with persistent configuration.
- Development tools and utilities with user preferences.

---

## When Not to Use This Library

Do not use `staticconfiguration` when:

- You can comfortably use dependency injection.
- Configuration is instance-specific, not global.
- You need distributed configuration (microservices, multi-machine deployments).
- Configuration comes from environment variables or command-line arguments.
- You require transactional updates (multiple fields atomically).
- You need real-time synchronization across processes.
- Configuration contains secrets (use a secrets manager instead).

This library is optimized for correctness and simplicity on single-machine environments. If you need distributed coordination or low-latency updates under extreme contention, look elsewhere.

---

## Design Philosophy

### Explicit Over Implicit

Configuration is declared explicitly in code. There are no hidden fields, no runtime discovery, no magic.

### Correctness Over Performance

The locking mechanism prioritizes data integrity. Under contention, operations may be slower, but data will not be corrupted.

### Recovery Over Failure

If the configuration file is unreadable, the system recovers automatically rather than crashing. Recovery is explicit (warning emitted) and preserves evidence (backup file).

### Single Machine, Not Distributed

This library is designed for local file access. It does not support distributed locking or multi-machine coordination.

### Narrow Scope

This library solves one problem well: persistent, typed, global configuration with static access. It does not attempt to be a general-purpose configuration framework.

---

## Requirements

- Python ≥ 3.10
- `psutil` (for process existence detection during lock cleanup)

---

## Summary

`staticconfiguration` is a small library for a specific problem: managing the narrow set of values that are genuinely global, need persistence, and sit uncomfortably within dependency injection architectures.

It provides static access, type safety, automatic persistence, schema evolution, and corruption recovery. It is designed for single-machine applications and prioritizes correctness over performance.

If you need global configuration and dependency injection makes your design worse, this library may help. If DI works fine, keep using it.

---

## License

Copyright (c) 2025 David Muñoz Pecci
This project is licensed under the Mozilla Public License 2.0 (MPL-2.0).
