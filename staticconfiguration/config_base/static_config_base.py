"""
Responsabilidad:
    - Proveer utilidades base para manejar configuraciones estáticas definidas
        mediante descriptors `Data` y persistidas mediante un backend JSON.

Contratos:
    - Se asume que las clases que hereden de `StaticConfigBase` declaran los
        atributos de configuración como instancias de `Data` y definen
        `__config_path__`, `__config_file__` y `__version__`.
    - Ninguna función modifica recursos fuera de la ruta proporcionada por
        `__config_path__` y `__config_file__`.
"""

from __future__ import annotations

from pathlib import Path
from os.path import expanduser

from .static_config_interface import StaticConfigInterface
from ..entities import Data
from ..json_backend.json_backend import JSONBackend

class StaticConfigBase(StaticConfigInterface):
    """
    Clase base para configuraciones estáticas con persistencia JSON.

    Responsabilidad:
        - Proporcionar métodos `get` y `set` reutilizables para acceder y
            modificar valores de configuración declarados como `Data` en las
            subclases, delegando la persistencia al backend JSON.

    Contratos:
        Invariantes:
            - Las subclases definen `__config_path__`, `__config_file__` y
                `__version__` como atributos de clase.
        Precondiciones:
            - Los campos de configuración están declarados como instancias
                de `Data` en la clase derivada.
        Postcondiciones:
            - `get` devuelve el valor almacenado o el `default` definido en
                el `Data` correspondiente.
            - `set` valida el tipo y persiste el nuevo valor usando el
                `JSONBackend`.
    """
    @classmethod
    def get(cls, data_name: str):
        """
        Recuperar el valor de configuración asociado a `data_name`.

        Responsabilidad:
            - Localizar el descriptor `Data` correspondiente al nombre dado,
              garantizar que el archivo de configuración existe e invocar el
              backend para obtener el valor almacenado.

        Contratos:
            Precondiciones:
                - `data_name` debe corresponder a un atributo `Data` en la
                  subclase (sensitivo a mayúsculas/minúsculas).
            Postcondiciones:
                - Devuelve el valor actual persistido o el valor por defecto
                  definido en el `Data` si no existe aún en el archivo.

        Args:
            data_name (str): Nombre del campo de datos a recuperar.

        Returns:
            Any: Valor del campo decodificado según las reglas del `Data`.

        Raises:
            KeyError: Si `data_name` no está definido en la clase.

        Ejemplo:
            >>> MyConfig.get('timeout')
            30
        """
        data_fields = cls._get_data_fields()

        if data_name not in data_fields:
            raise KeyError(f"Data field {data_name!r} is not defined.")

        data = data_fields[data_name]
        config_path = Path(expanduser(cls.__config_path__)) / cls.__config_file__

        backend = JSONBackend()
        backend.ensure_initialized(config_path, cls.__version__, list(data_fields.values()))

        return backend.read_value(data, config_path)
    
    @classmethod
    def set(cls, data_name: str, new_value: object):
        """
        Asignar y persistir un nuevo valor para el campo especificado.

        Responsabilidad:
            - Validar el tipo del `new_value` según `data.data_type`, asegurar
              la existencia del archivo de configuración y delegar la escritura
              al backend JSON.

        Contratos:
            Precondiciones:
                - `data_name` corresponde a un `Data` definido en la clase.
                - `new_value` es una instancia compatible con
                  `data.data_type`.
            Postcondiciones:
                - El nuevo valor queda persistido en el archivo de
                  configuración y `last_modified` se actualiza.

        Args:
            data_name (str): Nombre del campo a modificar.
            new_value (object): Valor nuevo que debe ser compatible con el
                tipo declarado en el `Data` correspondiente.

        Returns:
            None: Persiste el nuevo valor en disco.

        Raises:
            KeyError: Si `data_name` no está definido en la clase.
            TypeError: Si `new_value` no es del tipo esperado.

        Ejemplo:
            >>> MyConfig.set('timeout', 60)
        """
        data_fields = cls._get_data_fields()

        if data_name not in data_fields:
            raise KeyError(f"Data field {data_name!r} is not defined.")

        data = data_fields[data_name]

        if not isinstance(new_value, data.data_type):
            raise TypeError(f"Value for {data_name!r} must be of type {data.data_type.__name__}")

        config_path = Path(cls.__config_path__).expanduser() / cls.__config_file__

        backend = JSONBackend()
        backend.ensure_initialized(config_path, cls.__version__, list(data_fields.values()))
        backend.write_value(data, new_value, config_path)

    @classmethod
    def _get_data_fields(cls) -> dict[str, Data]:
        """
        Extraer los atributos `Data` definidos en la clase.

        Responsabilidad:
            - Construir un diccionario con los nombres de atributo y las
              instancias `Data` encontradas en la definición de la clase.

        Contratos:
            Precondiciones:
                - Ejecutarse sobre la clase o una subclase que pueda contener
                  atributos de tipo `Data`.
            Postcondiciones:
                - Devuelve un `dict` cuyos keys son los nombres de los campos
                  y los values son las instancias `Data` correspondientes.

        Args:
            None

        Returns:
            dict[str, Data]: Mapeo de nombre de campo a descriptor `Data`.

        Ejemplo:
            >>> MyConfig._get_data_fields()
            {'timeout': Data(...), 'retries': Data(...)}
        """
        return {
            name: value
            for name, value in cls.__dict__.items()
            if isinstance(value, Data)
        }