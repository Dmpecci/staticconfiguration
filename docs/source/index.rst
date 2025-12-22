staticconfiguration
===================

Persistent, typed, static configuration for Python applications.

``staticconfiguration`` is a small library designed to manage the narrow subset
of configuration values that are genuinely global, must persist across
application restarts, and do not fit cleanly into dependency injection
architectures.

It provides **static access**, **strong typing**, **automatic persistence**,
**schema evolution**, and **safe multiprocess concurrency** for single-machine
applications.

This documentation describes the concepts, guarantees, and trade-offs of the
library, as well as the public API.

---

What this library is
--------------------

- A declarative configuration system with static access semantics.
- A solution for *global-by-nature* configuration values.
- A complement to dependency injection, not a replacement.
- Designed for correctness, explicitness, and long-term maintainability.

Typical use cases include application preferences, feature flags, UI settings,
timeouts, and other values that must be shared across unrelated subsystems.

---

What this library is not
------------------------

- Not a general-purpose key-value store.
- Not a distributed configuration system.
- Not a secrets manager.
- Not a replacement for dependency injection.
- Not suitable for network filesystems or multi-machine deployments.

If dependency injection works well for a given value, it should be preferred.

---

Getting started
---------------

If you are new to the library, start here:

- **Quickstart**: how to define a configuration class and read/write values.
- **Core concepts**: the design philosophy and guarantees.
- **Concurrency model**: how locking, safety, and failure modes work.
- **Schema migration**: how configuration evolves over time.

The full API reference is generated automatically from the source code and
documents all public classes and methods.

---

Contents
--------

.. toctree::
   :maxdepth: 2

   quickstart
   concepts
   concurrency
   schema_migration
   corruption_recovery
   performance_and_validation
   faq
   api
