# Copyright (c) 2025 David Muñoz Pecci
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

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
import warnings
import os
from staticconfiguration.exceptions.configuration_reset_warning import ConfigurationResetWarning
from psutil import pid_exists
import shutil

class JSONBackend:
    """
    Backend for JSON persistence of static configurations.

    .. code-block:: text

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
    _SLEEP_INTERVAL: float = 0.05
    _LOCK_TTL_SECONDS: float = 10.0

    @staticmethod
    def read_value(data: Data, config_path: Path, version: str, data_fields: list[Data], development: bool, concurrency_unsafe: bool = False) -> object | None:
        """
        Read and decode the value of a configuration key from JSON.

        Responsibility:
            - Retrieve the stored value for the provided Data from the JSON file
              and return it in its domain representation (applying ``Data.decoder``
              if available or performing conversion via ``data.data_type``).
              Always under lock to ensure exclusive access.

        Contracts:
            Preconditions:
                - ``data`` is a valid Data instance with a defined ``name``
                  attribute.
            Postconditions:
                - Returns ``None`` if ``data`` is ``None``.
                - If ``data.decoder`` is present, the output is the result of
                  ``data.decoder(raw_value)``.

        Args:
            data (Data): Descriptor of the field whose key is to be read.
            config_path (Path): Path to the JSON configuration file.
            version (str): Configuration schema version to store.
            data_fields (list[Data]): List of Data descriptors for fields to
                initialize with their default values.
            development (bool): If True, forces migration even if versions match.
            concurrency_unsafe (bool): If True, operations will be unsafe for
                concurrent access. Default is False. Data integrity may be compromised. Use with caution.

        Returns:
            object | None: Decoded value corresponding to the ``data`` field,
                or ``None`` if the stored value is JSON null.
        Raises:
            KeyError: If the key described by ``data.name`` does not exist in the JSON file.
        """
        if not concurrency_unsafe:
            JSONBackend._acquire_lock(config_path)
        try:
            payload = JSONBackend._ensure_safe_state(config_path, version, data_fields, development)
            if data.name in payload["data"]:
                raw_value = payload["data"][data.name]
            else:
                raise KeyError(f"Key '{data.name}' not found in configuration file.")

            if raw_value is None:
                return None
            if data.decoder:
                return data.decoder(raw_value)
        finally:
            if not concurrency_unsafe:
                JSONBackend._release_lock(config_path)
        return data.data_type(raw_value)

    @staticmethod
    def write_value(data: Data, new_value, config_path: Path, version: str, data_fields: list[Data], development: bool, concurrency_unsafe: bool = False) -> None:
        """
        Write or update the value of a field in the JSON configuration file.

        Responsibility:
            - Persist ``new_value`` for the key described by ``data``, updating
              the ``last_modified`` timestamp and applying ``Data.encoder`` if
              present. Always under lock to ensure exclusive access.

        Contracts:
            Preconditions:
                - ``data`` is a valid Data instance and ``new_value`` is a value
                  that can be serialized directly or via ``Data.encoder``.
                - ``data_fields`` is a valid list of Data from the configuration schema.
            Postconditions:
                - The JSON file will contain the updated value in
                  ``payload['data'][data.name]`` (possibly encoded).
                - ``payload['last_modified']`` will reflect the write time in
                  ISO 8601 UTC format without microseconds.

        Args:
            data (Data): Descriptor of the field to update.
            new_value (Any): New value to store for the field.
            config_path (Path): Path to the JSON configuration file.
            version (str): Configuration schema version to store.
            data_fields (list[Data]): List of Data descriptors for fields to
                initialize with their default values.
            development (bool): If True, forces migration even if versions match.
            concurrency_unsafe (bool): If True, operations will be unsafe for
                concurrent access. Default is False. Data integrity may be compromised. Use with caution.

        Returns:
            None: Does not return a value; persists the new state to disk.

        Raises:
            KeyError: If the key described by ``data.name`` does not exist in the JSON file.
        """
        if not concurrency_unsafe:
            JSONBackend._acquire_lock(config_path)
        
        try:
            payload = JSONBackend._ensure_safe_state(config_path, version, data_fields, development)
            
            if data.name not in payload["data"]:
                # It shouldn't happen if ensure_safe_state works correctly
                # Since we access with an explicit existent key in the class that inherits StaticConfigBase, 
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

            JSONBackend._write_payload(payload, config_path)
        finally:
            if not concurrency_unsafe:
                JSONBackend._release_lock(config_path)

    @staticmethod
    def _ensure_safe_state(config_path: Path, version: str, data_fields: list[Data], development: bool) -> dict:
        """
        Ensure existence and integrity of the JSON configuration file, and check for
        required migrations if needed.

        Responsibility:
            - Create a new JSON configuration file from defaults when the file
              is missing.
            - Detect schema version changes and invoke migration if necessary.
            - If the file exists but is corrupted (not parseable as JSON), it is
                recreated with default values for all fields defined in
                ``data_fields``.
            - After checking or performing migration, return the current payload.
        Contracts:
            Preconditions:
                - ``config_path`` is a valid pathlib.Path, may or may not exist.
                - ``data_fields`` is a list of all the Data instances obtained from the
                  configuration schema.
                - This method is called under lock to ensure exclusive access.
            Postconditions:
                - Always leaves the file in a valid JSON state with the expected keys.

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

        def read_payload() -> dict:
            """
            Reads and returns the payload dictionary from the JSON configuration file.

            Returns:
                dict: Payload dictionary read from the JSON configuration file.
            """
            with config_path.open("r", encoding="utf-8") as json_file:
                return json.load(json_file)
        
        def migrate_payload(payload) -> dict:
            migrated_payload = ConfigPayloadMigrator.migrate_payload(payload, version, data_fields)
            JSONBackend._write_payload(migrated_payload, config_path)
            return migrated_payload

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
        
        payload = None
        try:
            if not config_path.exists():
                JSONBackend._write_payload(build_default_payload(), config_path)
                return
            payload = read_payload()
            if not is_operable_payload(payload): 
                payload = migrate_payload(payload)
                return
            file_version = payload.get("version")
            if file_version != version or development:
                payload = migrate_payload(payload)
            return
        except (json.JSONDecodeError, OSError): # .json corrupted, restore defaults
            # Backup the corrupted file before resetting
            try:
                if config_path.exists():
                    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
                    backup_name = f"{config_path.stem}_{timestamp}.json.corruptedbackup"
                    backup_path = config_path.with_name(backup_name)
                    shutil.copy2(config_path, backup_path)
            except Exception:
                # Best-effort backup: never block recovery
                pass

            JSONBackend._write_payload(build_default_payload(), config_path)
            payload = None
            warnings.warn(
                "Configuration file was corrupted and has been reset to defaults.",
                ConfigurationResetWarning,
                stacklevel=2,
            )
        finally:
            if payload is None:
                payload = read_payload()
            return payload

    @staticmethod   
    def _write_payload(payload: dict, config_path: Path) -> None: 
        """
        Write the payload dictionary to the JSON configuration file atomically.

        Responsibility:
            - Persist the provided payload to disk in an atomic manner by
              writing to a temporary file and replacing the target file.
            - Ensure target directory exists before attempting to write.

        Contracts:
            Preconditions:
                - ``payload`` is JSON-serializable (contains only serializable types).
                - ``config_path`` is a pathlib.Path pointing to the desired file location.
                - Caller holds exclusive access (this method does not acquire locks).
            Postconditions:
                - The file at ``config_path`` exists and contains the JSON
                  representation of ``payload`` if no exception is raised.
                - The write is performed atomically using a temporary file and
                  an atomic replace/rename operation.

        Args:
            payload (dict): Payload dictionary to write to the JSON file.
            config_path (Path): Path to the JSON configuration file.

        Returns:
            None: This method does not return a value.

        Example:
            >>> JSONBackend._write_payload({"version": "1.0", "data": {}}, Path("/tmp/config.json"))
        """
        config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = config_path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as json_file:
            json.dump(payload, json_file, indent=2)
        tmp_path.replace(config_path)

    @staticmethod
    def _acquire_lock(config_file: Path) -> None:
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
        lock_path = config_file.with_suffix(config_file.suffix + ".lock")
        pid = os.getpid()
        now = time.monotonic()

        payload = f"{pid}:{now}"
        os.makedirs(lock_path.parent, exist_ok=True)

        while True:
            try:
                fd = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
                with os.fdopen(fd, "w") as f:
                    f.write(payload)
                return  # lock adquirido

            except FileExistsError:
                if JSONBackend._is_lock_stale(lock_path):
                    if JSONBackend._break_stale_lock(lock_path):
                        continue

                time.sleep(JSONBackend._SLEEP_INTERVAL)

    @staticmethod
    def _release_lock(config_file: Path) -> None:
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
        lock_path = config_file.with_suffix(config_file.suffix + ".lock")
        pid = os.getpid()

        try:
            content = lock_path.read_text()
            owner_pid = int(content.split(":")[0])
        except FileNotFoundError:
            return
        except Exception:
            # lock corrupto, no tocar
            return

        if owner_pid != pid:
            raise RuntimeError(
                "Attempted to release a lock not owned by this process"
            )

        try:
            lock_path.unlink()
        except FileNotFoundError:
            raise RuntimeError(
                "Lock file disappeared before it could be released"
            )

    @staticmethod
    def _is_lock_stale(lock_path: Path) -> bool:
        """
        Determine whether the lock for ``config_file`` is stale.

        Responsibility:
            - Decide if the existing lock should be considered expired.
            - The principal criteria for staleness are:
                - The lock file's age exceeds a predefined TTL, via monotonic time.
                    This is the principal mechanism to avoid deadlocks.
                - The process that created the lock no longer exists.
                    We check via PID existence, as secondary mechanism.
            - As fallback, if the lock file is corrupt, we check its modification
              time against the TTL.

        Contracts:
            Preconditions:
                - ``lock_file`` is a Path to the lock file.
            Postconditions:
                - Returns True when the lock is expired or invalid; False when
                  it is still valid or absent.

        Args:
            lock_file (Path): JSON configuration file whose lock is evaluated.

        Returns:
            bool: True if the lock is stale; False otherwise.
        """
        try:
            content = lock_path.read_text()
            pid_str, created_str = content.split(":")
            pid = int(pid_str)
            created = float(created_str)
        except Exception:
            # NOTE:
            # In case of a corrupted lock file, we fall back to filesystem metadata (mtime).
            # This relies on wall-clock time and may theoretically misbehave if the system
            # clock is changed at the exact moment a corrupt lock appears.
            #
            # This scenario is considered astronomically unlikely and is preferable to
            # risking an unrecoverable deadlock. If a user ever encounters this edge case,
            # congratulations: you have won the concurrency lottery.
            age = time.time() - lock_path.stat().st_mtime
            return (age > JSONBackend._LOCK_TTL_SECONDS)
        
        lifetime = time.monotonic() - created
        if lifetime > JSONBackend._LOCK_TTL_SECONDS:
            return True

        if not JSONBackend._process_exists(pid):
            return True

        return False

    @staticmethod
    def _process_exists(pid: int) -> bool:
        """
        Check if a process with the given PID exists.

        Responsibility:
            - Determine if a process with the specified PID is currently running.
        Contracts:
            Preconditions:
                - ``pid`` is a positive integer representing a process ID.
            Postconditions:
                - Returns True if the process exists; False otherwise.

            Args:
                pid (int): Process ID to check.

            Returns:
                bool: True if the process exists; False otherwise.
            """
        try:
            return pid_exists(pid)
        except Exception:
            # Any unexpected failure → be conservative
            return True
        
    @staticmethod
    def _break_stale_lock(lock_path) -> bool:
        """
        Attempt to remove a stale lock file.

        Responsibility:
            - Delete the lock file if it exists, indicating the lock is no longer valid.

        Contracts:
            Preconditions:
                - The lock file path is provided.
            Postconditions:
                - The lock file is removed if it existed.

        Args:
            lock_path (Path): Path to the lock file.

        Returns:
            bool: True if the lock file was removed; False if it did not exist.
        """
        try:
            lock_path.unlink()
            return True
        except FileNotFoundError:
            return False

