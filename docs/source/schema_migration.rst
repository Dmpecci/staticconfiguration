Schema migration
================

This document describes how ``staticconfiguration`` evolves configuration
schemas over time and how persisted data is migrated safely and deterministically.

---

When migration occurs
---------------------

A configuration payload is migrated automatically when **either** of the
following conditions is met:

- The schema version stored in the JSON file differs from the class
	``__version__``.
- ``__development__`` is set to ``True``.

Migration is triggered lazily on the next read or write operation.

There is no separate migration step and no manual action required.

---

Version strings
---------------

The ``__version__`` attribute is treated as an **opaque string**.

The library does not interpret version semantics (no ordering, no comparison,
no semantic version parsing).

Only string equality is checked.

Developers are responsible for updating the version string when the schema
changes.

---

Deterministic reconstruction
----------------------------

Migration is **not incremental**.

Instead of applying patches, the configuration payload is rebuilt
deterministically from the declared schema.

The process is:

1. Read the existing payload (if any).
2. Reconstruct the ``data`` section from the declared ``Data`` fields.
3. Resolve each field according to migration rules.
4. Normalize timestamps.
5. Persist the resulting payload.

This approach avoids complex migration graphs and guarantees consistent results
regardless of the number of intermediate versions.

---

Field resolution rules
----------------------

During migration, each declared field is resolved independently.

The rules are applied in the following order:

- **Field present in payload**:
	- If the value matches the declared type, it is preserved.
	- If the type differs, an explicit cast is attempted.
	- If casting fails, the default value is used.

- **Field missing in payload**:
	- The default value defined in the schema is used.

- **Extra fields in payload**:
	- Fields not declared in the schema are dropped.

This guarantees that the resulting payload always matches the declared schema
exactly.

---

Encoders and decoders during migration
--------------------------------------

If a field defines an encoder or decoder, they are applied as follows:

- Migration applies type coercion and (optionally) encoders to produce a JSON-safe payload. 
- Decoders are applied at read time (domain projection), not during migration.

Correct encoder and decoder behavior is the responsibility of the developer.

Encoders/decoders are expected to be total functions. If they raise, the exception propagates (programming error).

---

Timestamp handling
------------------

The payload contains two timestamps:

- ``created``: when the configuration was first created.
- ``last_modified``: when the configuration was last written.

Migration applies the following rules:

- ``created`` is preserved **only if** it is present and valid.
- If ``created`` is missing or invalid, it is set to the current time.
- ``last_modified`` is always set to the current time during migration.

All timestamps are normalized to ISO 8601 format with UTC timezone.

---

Development mode
----------------

Setting ``__development__ = True`` forces migration on **every** access,
even if the version string matches.

This is intended for active development, where schemas may change frequently
without updating the version string.

Development mode should never be enabled in production environments, as it
introduces unnecessary overhead.

---

Failure behavior
----------------

Migration is designed to be robust.

If migration encounters malformed or partially invalid data:

- The migration continues using default values where necessary.
- The resulting payload is always structurally valid.
- Migration never leaves the configuration in a partially written state.

If the configuration file itself is unreadable, corruption recovery is applied
instead of migration.

---

Summary
-------

Schema migration in ``staticconfiguration`` follows these principles:

- Explicit, schema-driven evolution.
- Deterministic reconstruction over incremental patches.
- Strong normalization guarantees.
- Minimal developer intervention.
- Safe defaults over partial preservation.

This approach favors long-term maintainability and predictable behavior.
