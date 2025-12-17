"""
Responsibility:
    - Provide a simple JSON file-based backend to store and retrieve structured
      configuration values. Group the necessary operations to initialize, read,
      and write the configuration file.

Contracts:
    - The backend assumes the filesystem is available and the process has
      read/write permissions on the target path.
    - Methods expect to receive valid pathlib.Path objects and Data objects
      defined in staticconfiguration.entities.
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import time
from staticconfiguration.entities import Data

class JSONBackend:
    """
    Backend for JSON persistence of static configurations.

    Responsibility:
        - Handle initial creation of the JSON configuration file, as well as
          reading and writing individual values.

    Contracts:
        Invariants:
            - No method modifies paths other than those provided by its arguments.
        Preconditions:
            - ``config_path`` must be a pathlib.Path pointing to the file or the
              location where it will be created.
            - ``data_fields`` is a valid list of Data describing fields and
              possible associated encoder/decoder.
        Postconditions:
            - After ``ensure_initialized``, a JSON file exists with keys
              ``version``, ``created``, ``last_modified``, and ``data`` when
              preconditions are met.
    """
    _sleep_interval: float = 0.05
    _lock_ttl_seconds: float = 10.0

    def ensure_initialized(self, config_path: Path, version: str, data_fields: list[Data]) -> None:
        """
        Ensure initial existence of the JSON configuration file.

        Responsibility:
            - Create the JSON configuration file with a minimal structure
              (metadata and default values) if it does not exist.

        Contracts:
            Preconditions:
                - ``config_path`` is a valid pathlib.Path pointing to the target
                  file (not to a nonexistent directory without permissions).
                - ``version`` is a non-empty string representing the configuration
                  schema version.
                - ``data_fields`` is a list of Data instances where each element
                  has ``name``, ``data_type``, and ``default`` attributes.
            Postconditions:
                - If the file did not exist, it is created with keys ``version``,
                  ``created``, ``last_modified``, and ``data`` containing default
                  values (possibly encoded via ``Data.encoder`` if present).

        Args:
            config_path (Path): Path to the JSON configuration file.
            version (str): Configuration schema version to store.
            data_fields (list[Data]): List of Data descriptors for fields to
                initialize with their default values.

        Returns:
            None: Does not return a value; ensures file creation if it did not
                exist.

        Raises:
            OSError: If it is not possible to create the directory or write the
                file due to filesystem issues.
        """
        if config_path.exists():
            return

        config_path.parent.mkdir(parents=True, exist_ok=True)

        timestamp = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

        data_content = {}
        for data in data_fields:
            value = data.encoder(data.default) if data.encoder else data.default
            data_content[data.name] = value

        payload = {
            "version": version,
            "created": timestamp,
            "last_modified": timestamp,
            "data": data_content,
        }

        with config_path.open("w", encoding="utf-8") as json_file:
            json.dump(payload, json_file, indent=2)

    def read_value(self, data: Data, config_path: Path):
        """
        Read and decode the value of a configuration key from JSON.

        Responsibility:
            - Retrieve the stored value for the provided Data from the JSON file
              and return it in its domain representation (applying ``Data.decoder``
              if available or performing conversion via ``data.data_type``).

        Contracts:
            Preconditions:
                - ``config_path`` exists and is readable.
                - ``data`` is a valid Data instance with a defined ``name``
                  attribute.
            Postconditions:
                - Returns ``None`` only if the value is ``null`` in JSON or if
                  ``data.default`` is ``None`` and the key does not exist.
                - If ``data.decoder`` is present, the output is the result of
                  ``data.decoder(raw_value)``.

        Args:
            data (Data): Descriptor of the field whose key is to be read.
            config_path (Path): Path to the JSON configuration file.

        Returns:
            object | None: Decoded value corresponding to the ``data`` field,
                or ``None`` if there is no value and ``default`` is ``None``.

        Raises:
            FileNotFoundError: If ``config_path`` does not exist.
            json.JSONDecodeError: If the JSON content is malformed.
        """
        self._wait_until_unlocked(config_path)
        with config_path.open("r", encoding="utf-8") as json_file:
            payload = json.load(json_file)

        raw_value = payload["data"].get(data.name, data.default)

        if data.decoder:
            return data.decoder(raw_value)

        if raw_value is None:
            return None
        
        return data.data_type(raw_value)

    def write_value(self, data: Data, new_value, config_path: Path):
        """
        Write or update the value of a field in the JSON configuration file.

        Responsibility:
            - Persist ``new_value`` for the key described by ``data``, updating
              the ``last_modified`` timestamp and applying ``Data.encoder`` if
              present.

        Contracts:
            Preconditions:
                - ``config_path`` exists and is writable (or the process can
                  create/rewrite the file temporarily in the same location).
                - ``data`` is a valid Data instance and ``new_value`` is a value
                  that can be serialized directly or via ``Data.encoder``.
            Postconditions:
                - The JSON file will contain the updated value in
                  ``payload['data'][data.name]`` (possibly encoded).
                - ``payload['last_modified']`` will reflect the write time in
                  ISO 8601 UTC format without microseconds.

        Args:
            data (Data): Descriptor of the field to update.
            new_value (Any): New value to store for the field.
            config_path (Path): Path to the JSON configuration file.

        Returns:
            None: Does not return a value; persists the new state to disk.

        Raises:
            FileNotFoundError: If ``config_path`` does not exist when attempting
                to read it.
            json.JSONDecodeError: If the JSON file is corrupted.
            OSError: If the write/rename of the temporary file fails.
        """
        self.acquire_lock(config_path)
        try:
            with config_path.open("r", encoding="utf-8") as json_file:
                payload = json.load(json_file)

            encoded_value = data.encoder(new_value) if data.encoder else new_value
            payload["data"][data.name] = encoded_value

            timestamp = (
                datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
            payload["last_modified"] = timestamp

            tmp_path = config_path.with_suffix(".tmp")

            with tmp_path.open("w", encoding="utf-8") as json_file:
                json.dump(payload, json_file, indent=2)

            tmp_path.replace(config_path)
        finally:
            self.release_lock(config_path)

    def _wait_until_unlocked(self, config_file: Path) -> None:
        """
        Block execution until the lock file for ``config_file`` disappears,
        cleaning stale locks when necessary.

        Responsibility:
            - Poll the lock associated with ``config_file`` until no writer holds
              it, allowing safe read access.

        Contracts:
            Preconditions:
                - ``config_file`` is the JSON configuration path to observe.
            Postconditions:
                - Returns only when the corresponding ``.lock`` file is absent.

        Args:
            config_file (Path): JSON configuration file to monitor.
        """
        while self._is_locked(config_file):
            now_ms = int(time.time() * 1000)
            if self._is_lock_stale(config_file, now_ms):
                try:
                    self._lock_path(config_file).unlink()
                except FileNotFoundError:
                    pass
                continue

            time.sleep(self._sleep_interval)

    def acquire_lock(self, config_file: Path) -> None:
        """
        Acquire exclusive access to ``config_file`` by creating its lock.

        Responsibility:
            - Block until the ``.lock`` companion file can be created, marking
              the caller as the active writer.

        Contracts:
            Preconditions:
                - ``config_file`` points to the JSON configuration file to lock.
            Postconditions:
                - The ``.lock`` file exists after successful acquisition.

        Args:
            config_file (Path): JSON configuration file to lock for writing.
        """
        config_file.parent.mkdir(parents=True, exist_ok=True)
        self._wait_until_unlocked(config_file)

        while True:
            if self._try_create_lock(config_file):
                return
            time.sleep(self._sleep_interval)

    def release_lock(self, config_file: Path) -> None:
        """
        Release the lock associated with ``config_file``.

        Responsibility:
            - Delete the ``.lock`` file created during acquisition to free the
              resource.

        Contracts:
            Preconditions:
                - The caller previously acquired the lock for ``config_file``.
            Postconditions:
                - The ``.lock`` file is removed.

        Args:
            config_file (Path): JSON configuration file whose lock is released.
        """
        self._lock_path(config_file).unlink()

    def _lock_path(self, config_file: Path) -> Path:
        """
        Compute the lock file path for ``config_file``.

        Responsibility:
            - Derive the companion ``.lock`` file path colocated with the JSON
              configuration file.

        Contracts:
            Preconditions:
                - ``config_file`` is a Path pointing to the JSON file.
            Postconditions:
                - Returns a Path ending with ``.lock`` in the same directory.

        Args:
            config_file (Path): JSON configuration file to lock.

        Returns:
            Path: Path to the lock file.
        """
        return config_file.with_suffix(config_file.suffix + ".lock")

    def _try_create_lock(self, config_file: Path) -> bool:
        """
        Attempt to create the lock file atomically.

        Responsibility:
            - Create the ``.lock`` file when it does not exist, signaling
              exclusive ownership.

        Contracts:
            Preconditions:
                - ``config_file`` is a Path to the JSON configuration file.
            Postconditions:
                - Returns True if the lock file is created, False if it already
                  exists.

        Args:
            config_file (Path): JSON configuration file whose lock is acquired.

        Returns:
            bool: True when the lock file is created; False otherwise.
        """
        lock_path = self._lock_path(config_file)
        try:
            with lock_path.open("x", encoding="utf-8") as lock_file:
                lock_file.write(str(int(time.time() * 1000)))
            return True
        except FileExistsError:
            return False

    def _read_lock_timestamp_ms(self, config_file: Path) -> int | None:
        """
        Read the timestamp stored inside the lock file.

        Responsibility:
            - Extract the millisecond epoch written when the lock was created.

        Contracts:
            Preconditions:
                - ``config_file`` refers to the JSON configuration file whose
                  lock may exist.
            Postconditions:
                - Returns the parsed integer timestamp in milliseconds when
                  readable; otherwise returns None.

        Args:
            config_file (Path): JSON configuration file whose lock timestamp is
                read.

        Returns:
            int | None: Timestamp in milliseconds if available; otherwise None.
        """
        try:
            lock_path = self._lock_path(config_file)
            with lock_path.open("r", encoding="utf-8") as lock_file:
                content = lock_file.read().strip()
        except OSError:
            return None

        if not content:
            return None

        try:
            return int(content)
        except ValueError:
            return None

    def _is_lock_stale(self, config_file: Path, now_ms: int) -> bool:
        """
        Determine whether the lock for ``config_file`` is stale.

        Responsibility:
            - Decide if the existing lock should be considered expired based on
              its stored timestamp and the configured TTL.

        Contracts:
            Preconditions:
                - ``config_file`` is a Path to the JSON configuration file.
                - ``now_ms`` is the current epoch time in milliseconds.
            Postconditions:
                - Returns True when the lock is expired or invalid; False when
                  it is still valid or absent.

        Args:
            config_file (Path): JSON configuration file whose lock is evaluated.
            now_ms (int): Current epoch time in milliseconds.

        Returns:
            bool: True if the lock is stale; False otherwise.
        """
        if not self._is_locked(config_file):
            return False

        timestamp_ms = self._read_lock_timestamp_ms(config_file)
        if timestamp_ms is None:
            return True

        ttl_ms = int(self._lock_ttl_seconds * 1000)
        if timestamp_ms > now_ms: # future timestamp, consider invalid
            return True
        return now_ms - timestamp_ms >= ttl_ms

    def _is_locked(self, config_file: Path) -> bool:
        """
        Determine whether ``config_file`` is currently locked.

        Responsibility:
            - Check for the presence of the companion ``.lock`` file.

        Contracts:
            Preconditions:
                - ``config_file`` is a Path to the JSON configuration file.
            Postconditions:
                - Returns True if the ``.lock`` file exists; False otherwise.

        Args:
            config_file (Path): JSON configuration file to check.

        Returns:
            bool: True when the resource is locked; False otherwise.
        """
        return self._lock_path(config_file).exists()
