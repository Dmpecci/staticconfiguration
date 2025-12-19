"""
Responsibility:
    - Provide a simple JSON file-based backend to store and retrieve structured
      configuration values. Group the necessary operations to ensure safe state, read,
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
from staticconfiguration.json_backend.config_payload_migrator import ConfigPayloadMigrator

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
            - After ``ensure_safe_state``, a JSON file exists with keys
              ``version``, ``created``, ``last_modified``, and ``data`` and 
               is structurally operable when preconditions are met.
    """
    _sleep_interval: float = 0.05
    _lock_ttl_seconds: float = 10.0

    def ensure_safe_state(self, config_path: Path, version: str, data_fields: list[Data], development: bool) -> None:
        """
        Ensure existence and integrity of the JSON configuration file, and check for
        required migrations if needed.

        Responsibility:
            - Read the JSON configuration optimistically without acquiring a lock.
            - Create a new JSON configuration file from defaults when the file
              is missing or its content is not parseable as JSON.
            - Detect schema version changes and run a deterministic migration
              under exclusive lock when required.
            - If the file exists but is corrupted (not parseable as JSON), it is
                recreated with default values for all fields defined in
                ``data_fields``.
            - If the file exists and parses but its version differs from the
                requested ``version``, or development mode is enabled, it is migrated under exclusive lock.

        Contracts:
            Preconditions:
                - ``config_path`` is a valid pathlib.Path, may or may not exist.
                - ``version`` is not an empty string.
                - ``data_fields`` is a list of all the Data instances obtained from the
                  configuration schema.
            Postconditions:
                - On success, leaves the file in a valid JSON state with the expected keys.

        Args:
            config_path (Path): Path to the JSON configuration file.
            version (str): Configuration schema version to store.
            data_fields (list[Data]): List of Data descriptors for fields to
                initialize with their default values.
            development (bool): If True, forces migration even if versions match.

        """
        def now_timestamp() -> str:
            """
            Acquire current timestamp in ISO 8601 UTC format without microseconds.

            Returns:
                str: timestamp string in ISO 8601 UTC format.
            """
            return (
                datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )

        def build_default_payload() -> dict:
            """
            Create a default payload dictionary with all fields set to their default values.

            Returns:
                dict: Default payload dictionary.
            """
            timestamp = now_timestamp()

            data_content: dict[str, object] = {}
            for data in data_fields:
                value = data.encoder(data.default) if (data.encoder and data.default is not None) else data.default
                data_content[data.name] = value

            return {
                "version": version,
                "created": timestamp,
                "last_modified": timestamp,
                "data": data_content,
            }

        def write_payload(payload: dict) -> None: 
            """
            Writes the given payload dictionary to the JSON configuration file atomically.
            Doesn't acquire any lock, the caller must ensure exclusive access.
            Args:
                payload (dict): Payload dictionary to write to the JSON file.
            """
            config_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = config_path.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as json_file:
                json.dump(payload, json_file, indent=2)
            tmp_path.replace(config_path)

        def read_payload() -> dict:
            """
            Reads and returns the payload dictionary from the JSON configuration file.

            Returns:
                dict: Payload dictionary read from the JSON configuration file.
            """
            with config_path.open("r", encoding="utf-8") as json_file:
                return json.load(json_file)
        
        def migrate_payload(force_migration: bool = False) -> None:
            """
            Checks the version and development flag of the existing payload and migrates it if necessary.
            Always acquires the lock before performing operations.
            """
            self.acquire_lock(config_path)
            try:
                payload = read_payload()
                if (not development and payload.get("version") == version) and not force_migration: 
                    # Yeah, I know, another check.
                    # Same condition, different context: re-evaluated under lock,
                    # with an explicit escape hatch for rare edge cases.
                    # The common fast-path stays untouched.
                    return
                migrated_payload = ConfigPayloadMigrator.migrate_payload(payload, version, data_fields)
                write_payload(migrated_payload)
            except (json.JSONDecodeError, OSError): # json definitely corrupted, rewrite defaults
                #raise Exception("Configuration file is corrupted; rewriting with default values.")
                write_payload(build_default_payload())
            finally:
                self.release_lock(config_path)       

        def is_operable_payload(payload: object) -> bool:
            """
            Check whether a parsed JSON payload is structurally operable
            for staticconfiguration.

            This does NOT validate schema fields or types; it only enforces
            the minimal structural invariants required by the backend.

            Created and last_modified must be present, they are not exactly required for correct functioning,
            but in migration, it should be added without modifying other fields.
            """
            if not isinstance(payload, dict):
                return False

            if "version" not in payload:
                return False
            if "created" not in payload:
                return False
            if "last_modified" not in payload:
                return False

            data = payload.get("data")
            if not isinstance(data, dict):
                return False

            return True

        # Check .json existence
        if not config_path.exists(): 
            self.acquire_lock(config_path)
            try:
                if not config_path.exists(): # recheck after acquiring lock
                    write_payload(build_default_payload())
                return
            finally:
                self.release_lock(config_path)

        if development: # Development mode always migrates
            migrate_payload()
            return
        # Check version and migrate if needed
        try: # optimistic read without lock
            payload = read_payload()
            if not is_operable_payload(payload): 
                migrate_payload(force_migration=True)
                return
            file_version = payload.get("version")
            if file_version != version:
                migrate_payload()
            return
        except (json.JSONDecodeError, OSError): # .json may be corrupted, try with lock. Shouldn't be corrupted, 
            migrate_payload()                   # we write atomically with replace(), but better safe than sorry
            return
        
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
                - Returns ``None`` if ``data`` is ``None``.
                - If ``data.decoder`` is present, the output is the result of
                  ``data.decoder(raw_value)``.

        Args:
            data (Data): Descriptor of the field whose key is to be read.
            config_path (Path): Path to the JSON configuration file.

        Returns:
            object | None: Decoded value corresponding to the ``data`` field,
                or ``None`` if the stored value is JSON null.
        Raises:
            KeyError: If the key described by ``data.name`` does not exist in the JSON file.
        """
        self._wait_until_unlocked(config_path)
        with config_path.open("r", encoding="utf-8") as json_file:
            payload = json.load(json_file)

        if data.name in payload["data"]:
            raw_value = payload["data"][data.name]
        else:
            raise KeyError(f"Key '{data.name}' not found in configuration file.")

        if raw_value is None:
            return None
        if data.decoder:
            return data.decoder(raw_value)

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
            KeyError: If the key described by ``data.name`` does not exist in the JSON file.
        """
        self.acquire_lock(config_path)
        try:
            with config_path.open("r", encoding="utf-8") as json_file:
                payload = json.load(json_file)
            
            if data.name not in payload["data"]:
                raise KeyError(f"Key '{data.name}' not found in configuration file.")

            encoded_value = data.encoder(new_value) if (data.encoder and new_value is not None) else new_value
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
                    # To me from the future (or anyone else):
                    # This is intentionally not release_lock().
                    # Stale-lock cleanup is racy by nature; release_lock enforces writer invariants,
                    # and this path will look like a catastrophic failure; we don't want unnecessary heart attacks.
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
        try:
            self._lock_path(config_file).unlink()
        except FileNotFoundError:
            # If this fails, fail completely and loudly.
            # It means some process has written without holding the lock,
            # or the lock was deleted externally.
            raise RuntimeError("Attempted to release a lock that is not held. Concurrency may be unstable.")

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
