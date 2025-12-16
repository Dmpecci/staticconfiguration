"""
Responsibility:
    - Provide base utilities to manage static configurations defined via
      Data descriptors and persisted using a JSON backend.

Contracts:
    - Classes that inherit from StaticConfigBase must declare configuration
      attributes as Data instances and define __config_path__, __config_file__,
      and __version__.
    - No function modifies resources outside the path provided by
      __config_path__ and __config_file__.
"""

from __future__ import annotations

from pathlib import Path

from .static_config_interface import StaticConfigInterface
from ..entities import Data
from ..json_backend.json_backend import JSONBackend

class StaticConfigBase(StaticConfigInterface):
    """
    Base class for static configurations with JSON persistence.

    Responsibility:
        - Provide reusable get and set methods to access and modify configuration
          values declared as Data in subclasses, delegating persistence to the
          JSON backend.

    Contracts:
        Invariants:
            - Subclasses define __config_path__, __config_file__, and __version__
              as class attributes.
        Preconditions:
            - Configuration fields are declared as Data instances in the derived
              class.
        Postconditions:
            - get returns the stored value or the default defined in the
              corresponding Data.
            - set validates the type and persists the new value using JSONBackend.
    """
    @classmethod
    def get(cls, data_field: Data):
        """
        Retrieve the configuration value associated with ``data_field``.

        Responsibility:
            - Locate the Data descriptor corresponding to the provided field,
              ensure the configuration file exists, and invoke the backend
              to obtain the stored value.

        Contracts:
            Preconditions:
                - ``data_field`` must be a Data descriptor attribute in the
                  subclass.
            Postconditions:
                - Returns the currently persisted value or the default value
                  defined in the Data if it does not yet exist in the file.

        Args:
            data_field (Data): Data descriptor attribute to retrieve.

        Returns:
            Any: Field value decoded according to Data rules.

        Raises:
            TypeError: If ``data_field`` is not a Data descriptor.
            KeyError: If ``data_field`` is not defined in the class.

        Example:
            >>> MyConfig.get(MyConfig.timeout)
            30
        """
        data_fields = cls._get_data_fields()

        data = cls._resolve_data_field(data_field, data_fields)
        config_path = Path(cls.__config_path__).expanduser() / cls.__config_file__

        backend = JSONBackend()
        backend.ensure_initialized(config_path, cls.__version__, list(data_fields.values()))

        return backend.read_value(data, config_path)
    
    @classmethod
    def set(cls, data_field: Data, new_value: object):
        """
        Assign and persist a new value for the specified field.

        Responsibility:
            - Validate the type of ``new_value`` according to ``data.data_type``,
              ensure the configuration file exists, and delegate writing to the
              JSON backend.

        Contracts:
            Preconditions:
                - ``data_field`` corresponds to a Data defined in the class.
                - ``new_value`` is an instance compatible with ``data.data_type``.
            Postconditions:
                - The new value is persisted in the configuration file and
                  ``last_modified`` is updated.

        Args:
            data_field (Data): Data descriptor attribute to modify.
            new_value (object): New value that must be compatible with the type
                declared in the corresponding Data.

        Returns:
            None: Persists the new value to disk.

        Raises:
            TypeError: If ``data_field`` is not a Data descriptor or
                ``new_value`` is not of the expected type.
            KeyError: If ``data_field`` is not defined in the class.

        Example:
            >>> MyConfig.set(MyConfig.timeout, 60)
        """
        data_fields = cls._get_data_fields()

        data = cls._resolve_data_field(data_field, data_fields)

        if not isinstance(new_value, data.data_type):
            raise TypeError(f"Value for {data.name!r} must be of type {data.data_type.__name__}")

        config_path = Path(cls.__config_path__).expanduser() / cls.__config_file__

        backend = JSONBackend()
        backend.ensure_initialized(config_path, cls.__version__, list(data_fields.values()))
        backend.write_value(data, new_value, config_path)

    @classmethod
    def _get_data_fields(cls) -> dict[str, Data]:
        """
        Extract Data attributes defined in the class.

        Responsibility:
            - Build a dictionary with attribute names and Data instances found
              in the class definition.

        Contracts:
            Preconditions:
                - Execute on the class or a subclass that may contain Data
                  attributes.
            Postconditions:
                - Returns a dict whose keys are field names and values are the
                  corresponding Data instances.

        Returns:
            dict[str, Data]: Mapping of field name to Data descriptor.

        Example:
            >>> MyConfig._get_data_fields()
            {'timeout': Data(...), 'retries': Data(...)}
        """
        fields = {}
        for base in cls.__mro__:
            for name, value in base.__dict__.items():
                if isinstance(value, Data) and name not in fields:
                    fields[name] = value
        return fields

    @classmethod
    def _resolve_data_field(cls, data_field: Data, data_fields: dict[str, Data]) -> Data:
        """
        Validate that ``data_field`` belongs to the class and return it.
        """
        if not isinstance(data_field, Data):
            raise TypeError("data_field must be an instance of Data.")

        for field in data_fields.values():
            if field is data_field:
                return field

        field_name = getattr(data_field, "name", repr(data_field))
        raise KeyError(f"Data field {field_name!r} is not defined.")
