# Copyright (c) 2025 David Muñoz Pecci
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Configuration payload migration and normalization utilities.

Responsibility:
    - Provide deterministic migration of configuration payloads between schema versions.
    - Normalize payload structure by reconstructing the data dictionary from a field schema.
    - Ensure timestamp consistency and validation for creation and modification tracking.
    - Handle type coercion and default value application during field resolution.

Contracts:
    - All methods in this module are stateless and do not mutate global state.
    - Output timestamps are always normalized to ISO 8601 format with UTC timezone (Z suffix).
    - Field values are resolved according to their declared data types and encoders.
    - Invalid or missing timestamps are normalized to the current UTC time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from staticconfiguration.entities import Data

class ConfigPayloadMigrator:
    """Migrate and normalize configuration payloads according to schema definitions.

    Responsibility:
        - Provide static methods for deterministic payload migration.
        - Reconstruct payload data from field schemas to ensure consistency.
        - Validate and normalize timestamps for tracking creation and modifications.
        - Apply type coercion and encoding rules to field values.

    Contracts:
        Invariants:
            - All methods are static and operate without instance state.
            - Migrations are deterministic given the same input payload and schema.
        Preconditions:
            - Input payloads may be partial, malformed, or from older schema versions.
            - Data fields must be valid `Data` instances with type and default information.
        Postconditions:
            - Output payloads conform to the current schema structure.
            - All required fields are present with appropriate values or defaults.
            - Timestamps are valid ISO 8601 strings with UTC timezone.
    """

    @staticmethod
    def migrate_payload(payload: dict, version: str, data_fields: list[Data]) -> dict:
        """
        Migrate a configuration payload to a new schema version with normalized structure.

        Responsibility:
            - Reconstruct the data dictionary deterministically from the field schema.
            - Fields not present in the schema are dropped during reconstruction.
            - Apply type coercion and encoding to each field value.
            - Preserve the original creation timestamp if valid, otherwise use current time.
            - Update the last modified timestamp to the current time.
            - Handle missing fields by applying their default values.

        Contracts:
            Preconditions:
                - `payload` is a dictionary that may contain "data", "created", and other keys.
                - `version` is a non-empty string representing the target schema version.
                - `data_fields` is a list of `Data` instances defining the complete schema.
            Postconditions:
                - Returns a dictionary with keys: "version", "created", "last_modified", "data".
                - The "data" dictionary contains all fields from `data_fields` with resolved values.
                - Timestamps are valid ISO 8601 strings with UTC timezone.

        Args:
            payload (dict): The input configuration payload to migrate.
            version (str): The target schema version string.
            data_fields (list[Data]): List of `Data` field descriptors defining the schema.

        Returns:
            dict: A normalized payload dictionary with keys: "version", "created",
                "last_modified", and "data". The "data" key contains all fields with
                resolved values according to their schema definitions.

        Example:
            >>> fields = [Data(name="count", data_type=int, default=0)]
            >>> payload = {"data": {"count": "5"}, "created": "2024-01-01T00:00:00Z"}
            >>> result = ConfigPayloadMigrator.migrate_payload(payload, "1.0", fields)
            >>> result["version"]
            '1.0'
            >>> result["data"]["count"]
            5
        """
        raw_data = payload.get("data")
        if not isinstance(raw_data, dict):
            raw_data = {}

        # Build "data" deterministically from schema.
        new_data: dict[str, Any] = {}
        for field in data_fields:
            if field.name in raw_data:
                raw_value = raw_data[field.name]
                new_data[field.name] = ConfigPayloadMigrator._resolve_field_value(raw_value, field)
            else:
                new_data[field.name] = field.encoder(field.default) if (field.encoder and field.default is not None) else field.default

        now_ts = ConfigPayloadMigrator._now_timestamp()

        # Preserve created only if it is present AND valid; otherwise normalize to now.
        created = payload.get("created")
        if not ConfigPayloadMigrator._is_valid_timestamp(created):
            created = now_ts

        return {
            "version": version,
            "created": created,
            "last_modified": now_ts,
            "data": new_data,
        }

    @staticmethod
    def _resolve_field_value(raw_value: Any, field: Data) -> Any:
        """Resolve and type-coerce a field value according to its schema definition.

        Responsibility:
            - Preserve explicit `None` values as JSON null semantics.
            - Apply type coercion when the raw value does not match the expected type.
            - Fall back to the field's default value if type coercion fails.
            - Apply the field's encoder function to the resolved value if present.

        Contracts:
            Preconditions:
                - `raw_value` can be any type, including `None`.
                - `field` is a valid `Data` instance with `data_type`, `default`, and `encoder` attributes.
            Postconditions:
                - Returns the resolved value (possibly encoded), or None if the value is null.
                - If `encoder` is defined and the value is not `None`, returns the encoded value.

        Args:
            raw_value (Any): The raw value from the payload to resolve.
            field (Data): The field descriptor containing type, default, and encoder information.

        Returns:
            Any: The resolved and optionally encoded value, or `None` if the value is null.
        """
        # Explicit null: preserve
        if raw_value is None:
            return None

        # Exact type match (avoids bool/int subclass surprises).
        if type(raw_value) is field.data_type:
            value = raw_value
        else:
            try:
                value = field.data_type(raw_value)
            except Exception:
                value = field.default

        # If default is None, preserve JSON null semantics.
        if value is None:
            return None

        return field.encoder(value) if field.encoder else value

    @staticmethod
    def _now_timestamp() -> str:
        """Generate the current UTC timestamp in ISO 8601 format with Z suffix.

        Responsibility:
            - Generate a timestamp string representing the current moment in UTC.
            - Remove microseconds for simplified timestamp precision.
            - Format the timestamp with 'Z' suffix instead of '+00:00' for UTC.

        Contracts:
            Postconditions:
                - Returns a string in ISO 8601 format: YYYY-MM-DDTHH:MM:SSZ.
                - The timestamp represents the current UTC time without microseconds.

        Returns:
            str: Current UTC timestamp string in ISO 8601 format with Z suffix.

        Example:
            >>> timestamp = ConfigPayloadMigrator._now_timestamp()
            >>> timestamp.endswith('Z')
            True
        """
        return (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _is_valid_timestamp(value: Any) -> bool:
        """Validate whether a value is a valid ISO 8601 timestamp string.

        Responsibility:
            - Check if the value is a non-empty string.
            - Normalize 'Z' suffix to '+00:00' for parsing compatibility.
            - Attempt to parse the string as an ISO 8601 datetime.

        Contracts:
            Preconditions:
                - `value` can be of any type.
            Postconditions:
                - Returns `True` if the value is a parseable ISO 8601 timestamp.
                - Returns `False` for non-string, empty, or invalid timestamp values.

        Args:
            value (Any): The value to validate as a timestamp.

        Returns:
            bool: `True` if the value is a valid ISO 8601 timestamp string, `False` otherwise.

        Example:
            >>> ConfigPayloadMigrator._is_valid_timestamp("2024-01-01T00:00:00Z")
            True
            >>> ConfigPayloadMigrator._is_valid_timestamp("invalid")
            False
            >>> ConfigPayloadMigrator._is_valid_timestamp(12345)
            False
        """
        if not isinstance(value, str):
            return False

        s = value.strip()
        if not s:
            return False

        # Normalize 'Z' to '+00:00' for fromisoformat.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"

        try:
            datetime.fromisoformat(s)
            return True
        except ValueError:
            return False
