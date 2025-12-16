# staticconfiguration

`staticconfiguration` is a Python library that provides a **declarative, static, and persistent configuration system** based on class definitions and a decorator-driven schema.

It allows applications to define configuration **once** (as a class), persist it to disk, and access it **safely and consistently** from anywhere in the codebase—without dependency injection, global mutable singletons, or scattered configuration loaders.

---

## Motivation

In medium and large Python applications, configuration tends to become a problem:

- Global settings are accessed from many unrelated modules.
- Configuration objects are passed around everywhere via dependency injection.
- Different components need access to the same configuration values, but do not (and should not) depend on each other.
- Persistence (JSON, files, versioning) is often handled inconsistently or duplicated.

This project was born from a real need in a larger application, where multiple independent subsystems need access to **shared, persistent configuration values**.

The key idea is simple:

> Configuration should be **declared once**, in one place, and accessed **statically and consistently** from anywhere.

---

## Core Idea

`staticconfiguration` lets you define configuration as a **class**, not as an object instance.

You declare:

- **What configuration values exist**
- **Their types**
- **Their default values**

And the library takes care of:

- Validating the configuration class declaration (required attributes and schema integrity)
- Creating and maintaining a persistent on-disk configuration file (JSON)
- Providing a static access API (`get` / `set`) backed by that file
- Supporting schema evolution through versioning and automatic migration

---

## Example

```python
from staticconfiguration import staticconfig, Data

@staticconfig
class AppSettings:
    __config_path__ = "~/.config"
    __config_file__ = "settings.json"
    __version__ = "1.0.0"
    __development__ = True

    api_url = Data(name="api_url", data_type=str, default="https://api.example.com")
    max_retries = Data(name="max_retries", data_type=int, default=3)
    enable_feature_x = Data(name="enable_feature_x", data_type=bool, default=False)
```

This class:

- Is not instantiated
- Acts as a static configuration definition
- Declares configuration schema, not runtime values


Access to values (get, set) and persistence is implemented incrementally on top of this structure.

## Configuration Metadata

Each configuration class includes a small set of required metadata attributes:

- `__config_path__`: Base directory where the configuration file is stored (e.g. `~/.config`).
- `__config_file__`: File name of the configuration (e.g. `settings.json`).
- `__version__`: Schema version of the configuration class. Used to detect changes in the declared configuration fields and trigger migrations.
- `__development__`: Development mode flag. When enabled, schema changes are treated as non-stable and migrations are applied aggressively to keep the on-disk file aligned with the current class declaration during active development.

### Versioning and Migration

When `__version__` changes (or when `__development__` is enabled), the library reconciles the on-disk JSON with the declared schema:

- Removes fields that no longer exist in the class.
- Adds newly declared fields initialized to their default values.
- Resets fields to defaults if their declaration changes in an incompatible way (e.g. type/default changes).

This ensures the configuration file remains consistent with the code-defined schema across releases and development cycles.

## Design Principles

- **Declarative**: Configuration is declared explicitly, not assembled dynamically.
- **Static access**: Configuration is accessed via the class itself, not through instances.
- **Single source of truth**: One configuration definition per application.
- **No dependency injection required**: Components do not need to know about a shared configuration object.
- **Separation of concerns**: Declaration, validation, persistence, and access are cleanly separated.

## How It Works

`staticconfiguration` is built around a declarative schema and a class decorator:

1. The developer declares configuration fields using `Data(...)` at class definition time.
2. The `@staticconfig` decorator validates the class and builds an internal schema registry.
3. Configuration values are persisted in a JSON file located at `__config_path__ / __config_file__`.
4. The application reads and updates values through a static API (`get` / `set`) without instantiating the class.

The `Data` objects declared in the class represent **schema metadata** (name, type, default). Runtime values are stored on disk and retrieved on demand.

## Concurrency and Race Conditions

`staticconfiguration` is designed to be safe under concurrent access:

- Writes are performed under mutual exclusion to prevent multiple writers from corrupting the JSON file.
- Reads are coordinated with writes to avoid read-after-write hazards and partially written states.
- The implementation is designed so that configuration remains a reliable source of truth even when multiple threads or processes attempt to update values.

This focus on correctness prevents common issues such as lost updates, torn writes, and inconsistent reads.

## Why This Exists (Compared to Existing Solutions)

This project is not a replacement for:

- environment variables
- CLI configuration
- DI containers

It is intended for applications where:

- Configuration is global by nature
- Multiple independent subsystems need access
- Configuration should be persistent and structured

- Configuration changes must be persisted reliably and safely
- Concurrent access must not corrupt configuration or produce inconsistent reads (See <attachments> above for file contents. You may not need to search or read the file again.)

Simplicity and explicitness matter more than flexibility.

staticconfiguration does not introduce global state; it introduces an environment-scoped, declarative, versioned configuration context outside the dependency graph.