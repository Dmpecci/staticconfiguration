Quickstart
==========

This quickstart shows the minimal steps required to define a static
configuration schema and read or write configuration values.

``staticconfiguration`` is designed for a *small* set of values that are
genuinely global, persistent, and shared across unrelated parts of an
application.

If dependency injection works well for a value, it should be preferred.

---

Defining a configuration class
------------------------------

Configuration is declared using a regular Python class decorated with
``@staticconfig``. Each configuration field is defined using a ``Data``
descriptor.

.. code-block:: python

	from staticconfiguration import staticconfig, Data

	@staticconfig
	class AppSettings:
		__config_path__ = "~/.config/myapp"
		__config_file__ = "settings.json"
		__version__ = "1.0.0"
		__development__ = False

		api_url = Data(
			name="api_url",
			data_type=str,
			default="https://api.example.com"
		)

		timeout = Data(
			name="timeout",
			data_type=int,
			default=30
		)

		debug_mode = Data(
			name="debug_mode",
			data_type=bool,
			default=False
		)


Required class attributes:

__config_path__: directory where the configuration file is stored.

__config_file__: JSON file name.

__version__: schema version string.

__development__: enable aggressive migration during development.

Reading configuration values

Configuration values are accessed statically using the get method.

.. code-block:: python

	timeout = AppSettings.get(AppSettings.timeout)
	print(timeout)  # 30 (or the persisted value)


Each call reads directly from disk. The library does not cache values in
memory.

This is a deliberate design choice to avoid reintroducing implicit global
state.

Writing configuration values

Values are updated using the set method.

.. code-block:: python

	AppSettings.set(AppSettings.timeout, 60)


The value is validated, persisted to disk, and becomes immediately visible
to other processes.

Type safety

Values are validated against the declared data_type.

.. code-block:: python

	# Raises TypeError
	AppSettings.set(AppSettings.timeout, "not an int")


If a stored value has an incompatible type during migration, the default
value defined in the schema is used instead.

Custom encoders and decoders

For types that are not directly JSON-serializable, custom encoder and
decoder functions can be provided.

.. code-block:: python

	from datetime import datetime

	def encode_datetime(value: datetime) -> str:
		return value.isoformat()

	def decode_datetime(value: str) -> datetime:
		return datetime.fromisoformat(value)

	@staticconfig
	class AppSettings:
		__config_path__ = "~/.config/myapp"
		__config_file__ = "settings.json"
		__version__ = "1.0.0"
		__development__ = False

		last_sync = Data(
			name="last_sync",
			data_type=datetime,
			default=datetime(2024, 1, 1),
			encoder=encode_datetime,
			decoder=decode_datetime,
		)


Encoders are applied when writing values to JSON.
Decoders are applied when reading values from JSON.

Next steps

After completing the quickstart, you may want to read:

Core concepts: design philosophy and guarantees.

Concurrency model: how locking and safety are handled.

Schema migration: how configuration evolves over time.

Corruption recovery: what happens when the configuration file is unreadable.
