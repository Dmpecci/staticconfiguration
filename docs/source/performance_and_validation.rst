Performance and validation
==========================

This document describes how ``staticconfiguration`` behaves under real-world
load, how it is tested, and what guarantees can be inferred from those tests.

The goal is not to present idealized benchmarks, but to document **observed
behavior under stress** and the limits of the design.

---

Testing philosophy
------------------

``staticconfiguration`` is tested using a layered approach:

- Deterministic unit tests for schema declaration, persistence, and migration.
- Multiprocess concurrency tests using real OS processes.
- Stress and chaos tests that deliberately push the system into extreme
	contention scenarios.
- Explicit validation of file integrity after every test.

Tests are designed to answer a single question:

**Does the system preserve correctness under adverse conditions?**

Performance is observed, but never prioritized over integrity.

---

What is measured (and what is not)
----------------------------------

The stress tests measure:

- End-to-end operation latency (read and write).
- Throughput under contention.
- Integrity of the persisted JSON file after concurrent access.
- System behavior when the filesystem is temporarily unstable.

They do **not** measure:

- Micro-benchmarks of internal functions.
- Cache-level performance (no cache exists by design).
- Network or distributed performance.

All measurements are taken on a local filesystem.

---

Observed performance characteristics
------------------------------------

Under realistic multiprocess workloads (8-20 processes):

- Typical read operations complete in ~0.5-2 ms.
- Typical write operations complete in ~1-4 ms.
- Throughput ranges from several hundred to ~800 operations per second,
	depending on contention and payload size.

As payload size increases:

- Read and write times scale roughly linearly with file size.
- Extremely large payloads (10,000 fields, ~240 KB JSON) remain usable,
	but are explicitly considered edge cases.

The library makes no attempt to optimize for large payloads.

---

Stress test summary
-------------------

The following scenarios are covered by the stress test suite:

- Concurrent readers (8 processes).
- Concurrent writers (4-12 processes).
- Mixed reader/writer workloads.
- High-contention access to the same field.
- Random access across hundreds or thousands of fields.
- Extreme payload sizes.
- Writer storms with randomized values.
- Maximum chaos scenarios with 20+ competing processes.

In all cases, the final configuration state is validated for structural
integrity.

The system consistently survives these scenarios without producing corrupted
configuration files.

---

Interpreting reported errors
----------------------------

In stress/chaos mode, transient exceptions may occur 
(e.g., JSON decode during active write, temporary missing file during atomic replace). 
These are captured for diagnostics and are not considered integrity failures unless the final payload is corrupted.

These are **not integrity failures**, they are captured for debugging.

They represent transient conditions such as:

- A process observing a file being replaced atomically.
- Temporary unavailability during file replacement.
- OS-level race conditions during extreme contention.

These errors are:

- Captured and classified during runtime.
- Expected under aggressive stress scenarios.
- Explicitly tolerated by the test suite.
- Verified to not result in corrupted or partially written files.

A test only fails if integrity is violated.

---

Integrity guarantees
--------------------

Based on the test suite, the following guarantees are provided:

- The configuration file is never left in a partially written state.
- Concurrent access does not produce corrupted JSON.
- Stale locks are detected and recovered from.
- Writer crashes do not permanently block access.
- Final persisted state is always structurally valid.

These guarantees hold **only** when concurrency protection is enabled.

---

Behavior under extreme conditions
---------------------------------

Under intentionally abusive scenarios:

- Some operations may fail transiently.
- Completion rate may drop slightly below 100%.
- Throughput may degrade due to lock contention.

This behavior is expected and acceptable.

The defining property is that the system **fails safely** and preserves
persistent integrity.

---

Interaction with ``concurrency_unsafe``
---------------------------------------

When ``concurrency_unsafe=True`` is used:

- None of the guarantees described in this document apply.
- Corruption is possible and expected under contention.
- Stress tests intentionally demonstrate this behavior.

This mode exists exclusively as an expert escape hatch.

---

Security considerations
-----------------------

``staticconfiguration`` does not provide security features such as encryption,
access control, or secret management.

Its security model is limited to:

- Preventing accidental corruption under concurrent access.
- Preserving file integrity.
- Making failure modes explicit and observable.

If stronger security guarantees are required, this library should not be used.

---

Summary
-------

The test suite demonstrates that ``staticconfiguration``:

- Preserves correctness under high contention.
- Scales predictably with payload size.
- Fails safely under extreme conditions.
- Prioritizes integrity over throughput.

These properties are the result of deliberate design trade-offs and define the
practical operating envelope of the library.
