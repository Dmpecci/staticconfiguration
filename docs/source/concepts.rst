Core concepts
=============

This document describes the core ideas, guarantees, and trade-offs behind
``staticconfiguration``.

Understanding these concepts is essential to using the library correctly and
avoiding unintended architectural issues.

---

The problem this library solves
-------------------------------

In most applications, dependency injection (DI) is the correct way to manage
dependencies and configuration.

However, there exists a narrow category of values that do not fit cleanly into
a DI graph:

- They are **data**, not services or strategies.
- They are accessed by **unrelated subsystems**.
- They must be available **very early** in the application lifecycle.
- They must **persist across restarts**.
- Forcing them through DI adds noise and artificial dependencies.

Examples include application preferences, UI state, feature flags, timeouts,
and global display options.

``staticconfiguration`` exists to handle this specific case.

It does **not** attempt to generalize configuration management beyond it.

---

Static access semantics
-----------------------

Configuration values are accessed statically, without creating instances.

This is intentional.

Static access makes the *global nature* of these values explicit, instead of
hiding it behind containers, singletons, or long dependency chains.

.. code-block:: python

	timeout = AppSettings.get(AppSettings.timeout)

This design accepts that some state is inherently global and focuses on making
that state explicit, constrained, and safe.

Declarative configuration schema
---------------------------------
All configuration fields are declared explicitly in code using Data
descriptors.

Each field defines:

- A stable name in the persisted payload.
- A concrete Python type.
- A default value.
- Optional encoder and decoder functions.

There is no dynamic discovery of fields and no implicit schema.

If a value is not declared in the schema, it does not exist.

Persistence model
-----------------
Configuration is persisted in a local JSON file with a fixed structure:

- A schema version string.
- Creation and modification timestamps.
- A data object containing all declared fields.

Every read or write operation interacts directly with the file on disk.

The library intentionally avoids in-memory caching.

This ensures that:

- All processes observe the same state.
- There is no cache invalidation problem.
- Global state remains explicit and inspectable.
- Correctness is prioritized over access latency.

Type safety and normalization
------------------------------
All values are validated against their declared type.

During schema migration or recovery:

- Missing fields are filled with their default values.
- Extra fields in the JSON file are discarded.
- Type mismatches attempt a cast to the declared type.
- If casting fails, the default value is used.

This normalization process is deterministic and schema-driven.

Schema versioning
-----------------
Each configuration class declares a __version__ string.

When the version stored in the JSON file differs from the class version,
the payload is migrated automatically.

Migration is not incremental.

Instead, the payload is reconstructed deterministically from the declared
schema, ensuring long-term consistency and eliminating ad-hoc migration logic.

During active development, __development__ = True can be used to force
migration on every access.

This should never be enabled in production.

Concurrency model
-----------------
All read and write operations acquire an exclusive file lock.

This includes read operations.

The lock ensures that:

- Reads never observe partial writes.
- Writes are serialized across processes.
- The configuration file remains structurally consistent.

The locking mechanism is designed for single-machine, multiprocess
environments and prioritizes data integrity over throughput.

A detailed description is provided in the concurrency documentation.

Failure and recovery philosophy
--------------------------------
staticconfiguration is designed to keep the application running whenever
possible.

If the configuration file becomes unreadable or corrupted:

- The corrupted file is backed up.
- The configuration is reset to schema defaults.
- A warning is emitted.
- Normal operation continues.

The library does not attempt partial repair of corrupted data.

Recovery is explicit and observable, never silent.

What this library does not provide
----------------------------------
To avoid accidental misuse, it is important to state what is deliberately
out of scope:

- No distributed locking or multi-machine coordination.
- No transactional updates across multiple fields.
- No real-time synchronization or change notifications.
- No in-memory caching or performance optimizations.
- No secret management.

If these features are required, this library is not the correct tool.

Design trade-offs
-----------------
The design of staticconfiguration follows a small set of explicit trade-offs:

- Correctness over performance.
- Explicit global state over hidden indirection.
- Deterministic behavior over convenience features.
- Narrow scope over general-purpose flexibility.

These trade-offs are intentional and define the library's domain.
