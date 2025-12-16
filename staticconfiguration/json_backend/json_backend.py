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