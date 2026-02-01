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

During migration, each declared ``Data`` field is resolved atomically.

Resolution rules are applied **per field**, not recursively to internal
structures:

- **Field present in payload**:
    - If the field defines a decoder and the decoder successfully validates the
      stored value (returning an instance of ``data_type``), the stored raw value
      is preserved exactly.
    - If validation fails, the field is reset to its default value.

- **Field missing in payload**:
    - The default value defined in the schema is used.

- **Extra top-level fields in payload**:
    - Fields not declared in the schema are dropped.

The migrator does **not** normalize or reconstruct internal structures.
Preservation of nested fields depends entirely on decoder tolerance.

Encoders and decoders during migration
--------------------------------------

During migration, encoders and decoders are used with **distinct roles**:

- **Encoders** are used only to serialize default values when a field must be
  reset.

- **Decoders** are used during migration **only for semantic validation**:
    - If a decoder successfully validates the stored value (returning an instance
      of ``data_type``), the raw serialized value is preserved.
    - If the decoder fails or returns an incompatible type, the field is reset
      to its default.

Decoders do not reconstruct or transform values during migration.
They are not applied to produce migrated payloads, only to decide whether a
stored value can be preserved safely.

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

Atomicity of migration
----------------------

Migration operates at the level of declared ``Data`` fields.

Each field is treated as an atomic unit:

- Internal structures are not migrated or patched incrementally.
- If a field value cannot be validated safely, it is reset entirely to its
  default value.
- Partial preservation of invalid structures is never attempted.

This design avoids heuristic recovery and ensures deterministic outcomes.

---

Summary
-------

Schema migration in ``staticconfiguration`` follows these principles:

- Explicit, schema-driven evolution.
- Deterministic reconstruction over incremental patches.
- Deterministic, conservative migration semantics.
- Explicit schema-driven field resolution.
- Validation-based preservation of existing data.
- Safe defaults over partial preservation.

This approach favors long-term maintainability and predictable behavior.
