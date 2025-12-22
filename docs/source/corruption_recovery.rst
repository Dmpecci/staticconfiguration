Corruption recovery
===================

This document describes how ``staticconfiguration`` behaves when the persisted
configuration file becomes unreadable or corrupted.

The recovery strategy is intentional and prioritizes application availability
over strict data preservation.

---

What is considered corruption
------------------------------

A configuration file is considered corrupted when it cannot be read safely.

This includes, but is not limited to:

- Invalid or malformed JSON.
- Truncated files caused by crashes or power loss.
- I/O errors while reading the file.
- Files that do not conform to the minimal structural invariants required by
  the backend.

Structural validation is intentionally minimal.

The library does not attempt to validate field-level correctness at this stage.

---

Recovery strategy
-----------------

When corruption is detected, the following steps are executed:

1. The corrupted file is backed up with a timestamped filename.
2. A new configuration file is created using schema defaults.
3. A warning is emitted to notify the application.
4. Normal operation continues.

The application is never left without a valid configuration file.

---

Backup behavior
---------------

Before resetting the configuration, the corrupted file is preserved using a
best-effort backup strategy.

The backup file name follows this pattern:

	<original_name>_<YYYY-MM-DD_HH-MM-SS>.json.corruptedbackup

Backups are created in the same directory as the original configuration file.

If backup creation fails for any reason, recovery continues without blocking.

The presence of a backup allows manual inspection or recovery if needed.

---

Why the file is not repaired
----------------------------

``staticconfiguration`` does **not** attempt to repair corrupted configuration
files.

Partial repair is intentionally avoided because:

- It requires heuristics that are difficult to reason about.
- It can silently preserve invalid or inconsistent state.
- It increases complexity without providing strong guarantees.

Instead, the library favors deterministic recovery to a known-good state.

---

Warning emission
----------------

Recovery is **never silent**.

When corruption is detected, a ``ConfigurationResetWarning`` is emitted.

This allows applications to:

- Log the event.
- Notify the user.
- Escalate the condition if desired.

Example:

.. code-block:: python

	import warnings
	from staticconfiguration.exceptions import ConfigurationResetWarning

	warnings.simplefilter("always", ConfigurationResetWarning)

	value = AppSettings.get(AppSettings.timeout)

Fail-fast alternatives
----------------------
Some applications may prefer to fail immediately instead of recovering
automatically.

This behavior can be implemented by converting the warning into an exception:

.. code-block:: python

	import warnings
	from staticconfiguration.exceptions import ConfigurationResetWarning

	def fail_fast_on_reset(message, category, filename, lineno, file=None, line=None):
		raise RuntimeError(message)

	warnings.showwarning = fail_fast_on_reset

This allows full control over failure policy without changing the library's
default behavior.

Interaction with migration
---------------------------
Corruption recovery occurs before schema migration.

If the file is unreadable:

- Recovery is applied first.
- Migration is then applied to the freshly created payload if necessary.

Migration logic never operates on corrupted data.

---

Design philosophy
-----------------
The recovery model follows a small set of principles:

- Prefer availability over configuration preservation.
- Make recovery explicit and observable.
- Preserve evidence for manual inspection.
- Avoid silent or partial repairs.
- Keep the recovery path simple and deterministic.

This philosophy reflects the assumption that an unusable application is worse
than a reset configuration in most end-user scenarios.

---

Summary
-------
When configuration corruption occurs:

- The file is backed up.
- Defaults are restored.
- A warning is emitted.
- The application continues running.

This behavior is intentional and designed to minimize user-facing failures
while preserving diagnostic information.
