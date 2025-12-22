Frequently asked questions
==========================

This section addresses common questions and misconceptions about
``staticconfiguration``.

If a question appears frequently in issue trackers, it belongs here.

---

Why does the library use static access instead of instances?
------------------------------------------------------------

Because the values managed by ``staticconfiguration`` are global by nature.

Using instances, singletons, or injected services would hide that global
nature behind indirection and make the architecture harder to reason about.

Static access makes the scope explicit and avoids accidental dependency graphs.

---

Why not use dependency injection for everything?
-------------------------------------------------

Dependency injection should be the default architecture for most application
state.

However, forcing genuinely global configuration values into a DI graph often
introduces unnecessary plumbing, artificial dependencies, and noisy
constructors.

``staticconfiguration`` exists for the small subset of values where DI makes
the design worse, not better.

---

Why is there no in-memory cache?
--------------------------------

Caching introduces implicit global state and cache invalidation problems.

A cache would require:

- Cross-process invalidation.
- Versioning or change notifications.
- Complex failure handling.

This would reintroduce the same problems the library is designed to avoid.

By reading directly from disk on every access, the configuration remains
explicit, consistent, and observable across processes.

---

Why does ``get()`` acquire a lock?
-----------------------------------

Because reading without a lock can observe partial or corrupted writes.

Allowing lock-free reads would require additional mechanisms to guarantee
consistency, such as copy-on-write or versioned snapshots.

The library prioritizes correctness and simplicity over raw read performance.

---

Can I update multiple fields atomically?
----------------------------------------

No.

Each ``set()`` operation updates a single field.

The library does not support transactional updates across multiple fields.

If atomic multi-field updates are required, this library is not a good fit.

---

Is this library thread-safe or process-safe?
--------------------------------------------

The library is designed for **process-level safety**.

Concurrency guarantees apply across multiple processes accessing the same
configuration file.

Thread-level safety within a single process depends on Python's execution
model and is not explicitly enforced.

---

Can I use this on network filesystems (NFS, SMB, cloud sync)?
--------------------------------------------------------------

No.

The locking and atomic write mechanisms assume local filesystem semantics.

Using this library on network filesystems can lead to undefined behavior,
including data corruption.

---

Why does the library reset the configuration instead of failing?
------------------------------------------------------------------

Because an unusable application is often worse than a reset configuration.

When corruption is detected, the library favors:

- Restoring a known-good state.
- Preserving evidence via backups.
- Allowing the application to continue running.

Applications that prefer fail-fast behavior can convert the warning into an
exception.

---

What happens if two processes write at the same time?
-----------------------------------------------------

Writes are serialized using an exclusive file lock.

Only one process can write at a time.

Other processes block until the lock is released or considered stale.

---

What is ``concurrency_unsafe`` and why does it exist?
-----------------------------------------------------

``concurrency_unsafe`` disables all locking and concurrency protections.

It exists as a deliberate escape hatch for strictly single-process contexts.

Using it in concurrent environments can corrupt the configuration file.

Every use emits a warning to reduce the risk of accidental misuse.

Issues caused while this flag is enabled are not supported.

---

Can I store secrets in the configuration file?
-----------------------------------------------

You "can", but you should not.

Configuration files managed by ``staticconfiguration`` are plain JSON files.

Secrets should be managed using dedicated secret management solutions.

---

Is this library suitable for distributed systems?
-------------------------------------------------

No.

``staticconfiguration`` is explicitly designed for single-machine deployments.

It does not provide distributed locking, coordination, or replication.

---

Why not use environment variables instead?
------------------------------------------

Environment variables are ephemeral and process-scoped.

They do not persist across restarts and are not suitable for user-modifiable
configuration in many applications.

Environment variables and ``staticconfiguration`` serve different purposes.

---

Does this library guarantee backward compatibility?
----------------------------------------------------

The library provides schema migration, not backward compatibility guarantees.

Developers are responsible for maintaining compatibility at the schema level
and updating version strings appropriately.

It works in updates, and downgrades, It is the same logic, add new fields, delete old ones,
but developer must know how migration works, to avoid surprises.

---

Summary
-------

``staticconfiguration`` is intentionally narrow in scope.

If its constraints align with your use case, it provides a robust and explicit
solution for persistent global configuration.

If not, it is better to choose a different tool.
