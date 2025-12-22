Concurrency model
=================

This document describes how ``staticconfiguration`` handles concurrent access
and what guarantees it provides in multiprocess environments.

Understanding this model is critical to using the library safely.

---

Scope and assumptions
---------------------

The concurrency model of ``staticconfiguration`` is explicitly designed for:

- Single-machine environments.
- Multiple independent processes accessing the same configuration.
- Local filesystems with reliable atomic file operations.

It does **not** support:

- Distributed systems.
- Network filesystems (NFS, SMB, cloud-synced folders).
- Cross-machine coordination.

If your deployment violates these assumptions, this library is not appropriate.

---

Exclusive locking on every operation
------------------------------------

All configuration operations acquire an **exclusive file lock**.

This includes both read and write operations.

.. code-block:: text

	get()  → acquire lock → read → release lock
	set()  → acquire lock → write → release lock

This design ensures that:

- Reads never observe partial or corrupted writes.
- Writes are fully serialized across processes.
- The configuration file remains structurally consistent at all times.

Allowing lock-free reads would introduce race conditions and require complex
cache invalidation or versioning mechanisms.

Correctness is prioritized over access latency.

Lock file mechanism
-------------------
Locking is implemented using a companion .lock file next to the JSON
configuration file.

The lock file contains:

- The process ID (PID) that acquired the lock.
- A monotonic timestamp representing acquisition time.

Only one process can hold the lock at a time.

Other processes block until the lock becomes available or is considered stale.

Stale lock detection and recovery
-----------------------------------
To prevent permanent deadlocks, locks are subject to expiration.

A lock is considered stale if:

- Its lifetime exceeds a fixed TTL.
- The process that created the lock no longer exists.
- The lock file is malformed or corrupted.

When a stale lock is detected, it is removed and acquisition is retried.

This mechanism favors forward progress over strict ownership guarantees.

Process existence checks
-------------------------
Process existence is verified using the operating system's process table.

If process detection fails unexpectedly, the system behaves conservatively and
assumes the process may still exist.

This reduces the risk of breaking a valid lock under uncertain conditions.

No fairness or ordering guarantees
----------------------------------
The locking mechanism does not enforce fairness.

Under contention:

- There is no guarantee of acquisition order.
- A process might experience starvation.

No fairness; starvation is possible. 
TTL prevents permanent deadlocks due to crashed owners, not starvation due to scheduling.

This trade-off is acceptable given the intended usage patterns and simplicity
requirements of the library.

The concurrency_unsafe escape hatch
-----------------------------------
Both get and set accept an optional concurrency_unsafe parameter.

.. code-block:: python

	value = AppSettings.get(AppSettings.timeout, concurrency_unsafe=True)
	AppSettings.set(AppSettings.timeout, 60, concurrency_unsafe=True)

When enabled:

- No file lock is acquired.
- No concurrency guarantees are provided.
- Concurrent access can corrupt the configuration file.
- Data loss is possible and expected under contention.

This flag exists as a deliberate escape hatch for strictly single-process
contexts.

Every use of concurrency_unsafe=True emits a warning to reduce the risk of
accidental misuse.

Issues caused while this flag is enabled are not supported.

Failure behavior
----------------
If a violation of mutual exclusion is detected, such as attempting to release a
lock not owned by the current process, the library fails immediately with a
runtime error.

This is considered a programming error and is not recoverable.

Silent recovery in this case would hide serious concurrency bugs.

Summary
-------
The concurrency model of staticconfiguration follows a small set of
principles:

- Serialize all access to prevent corruption.
- Prefer correctness over performance.
- Detect and recover from stale locks.
- Fail fast on clear contract violations.
- Provide an explicit escape hatch for expert use.

Using the library safely requires respecting these constraints.
