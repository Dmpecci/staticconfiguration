"""
Responsabilidad:
    - Proveer un backend simple basado en archivos JSON para almacenar y recuperar
        valores de configuración estructurados. Agrupa las operaciones necesarias
        para inicializar, leer y escribir el archivo de configuración.

Contratos:
    - El backend asume que el sistema de archivos está disponible y que el
        proceso tiene permisos de lectura/escritura sobre la ruta objetivo.
    - Los métodos esperan recibir objetos de tipo `pathlib.Path` válidos y
        objetos `Data` definidos en `staticconfiguration.entities`.
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from staticconfiguration.entities import Data

class JSONBackend:
    """
    Backend para persistencia en JSON de configuraciones estáticas.

    Responsabilidad:
        - Manejar la creación inicial del archivo de configuración JSON,
            así como la lectura y escritura de valores individuales.

    Contratos:
        Invariantes:
            - Ningún método modifica rutas distintas a las proporcionadas por
                sus argumentos.
        Precondiciones:
            - `config_path` debe ser un `pathlib.Path` apuntando al archivo
                o a la ubicación donde se creará.
            - `data_fields` es una lista de `Data` válida que describe campos
                y posibles `encoder`/`decoder` asociados.
        Postcondiciones:
            - Tras `ensure_initialized`, existe un archivo JSON con claves
                `version`, `created`, `last_modified` y `data` cuando las
                precondiciones se cumplen.
    """

    def ensure_initialized(self, config_path: Path, version: str, data_fields: list[Data]) -> None:
        """
        Asegurar la existencia inicial del archivo de configuración JSON.

        Responsabilidad:
            - Crear el archivo JSON de configuración con una estructura mínima
                (metadatos y valores por defecto) si no existe.

        Contratos:
            Precondiciones:
                - `config_path` es un `pathlib.Path` válido y apunta al archivo
                    destino (no a un directorio inexistente sin permisos).
                - `version` es una cadena no vacía que representa la versión
                    del esquema de configuración.
                - `data_fields` es una lista de instancias `Data` donde cada
                    elemento tiene atributos `name`, `data_type` y `default`.
            Postcondiciones:
                - Si el archivo no existía, queda creado con claves
                    `version`, `created`, `last_modified` y `data` que contiene
                    los valores por defecto (posiblemente codificados mediante
                    `Data.encoder` si está presente).

        Args:
            config_path (Path): Ruta al archivo JSON de configuración.
            version (str): Versión del esquema de configuración a almacenar.
            data_fields (list[Data]): Lista de descriptores `Data` para los
                campos a inicializar con sus valores por defecto.

        Returns:
            None: No devuelve valor; garantiza la creación del archivo si
                éste no existía.

        Raises:
            AssertionError: Si las precondiciones no se cumplen (modo debug).
            OSError: Si no es posible crear el directorio o escribir el archivo
                por problemas del sistema de archivos.
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
        Leer y decodificar el valor de una clave de configuración desde el JSON.

        Responsabilidad:
            - Recuperar el valor almacenado para el `Data` proporcionado desde
            el archivo JSON y devolverlo en su representación de dominio
            (aplicando `Data.decoder` si está disponible o realizando la
            conversión mediante `data.data_type`).

        Contratos:
            Precondiciones:
                - `config_path` existe y es legible.
                - `data` es una instancia válida de `Data` con atributo
                `name` definido.
            Postcondiciones:
                - Devuelve `None` sólo si el valor es `null` en JSON o si
                `data.default` es `None` y no existe la clave.
                - Si `data.decoder` está presente, la salida es el resultado
                de `data.decoder(raw_value)`.

        Args:
            data (Data): Descriptor del campo cuya clave se desea leer.
            config_path (Path): Ruta al archivo JSON de configuración.

        Returns:
            object | None: Valor decodificado correspondiente al campo `data`,
                o `None` si no hay valor y el `default` es `None`.

        Raises:
            FileNotFoundError: Si `config_path` no existe.
            json.JSONDecodeError: Si el contenido JSON está mal formado.
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
        Escribir o actualizar el valor de un campo en el archivo de configuración JSON.

        Responsabilidad:
            - Persistir `new_value` para la clave descrita por `data`, actualizando
            la marca temporal `last_modified` y aplicando `Data.encoder` si
            está presente.

        Contratos:
            Precondiciones:
                - `config_path` existe y es escribible (o el proceso puede
                crear/reescribir el archivo temporalmente en la misma
                ubicación).
                - `data` es una instancia válida de `Data` y new_value es un valor 
                que puede ser serializado directamente o mediante Data.encoder.
            Postcondiciones:
                - El archivo JSON contendrá el valor actualizado en
                `payload['data'][data.name]` (posiblemente codificado).
                - `payload['last_modified']` reflejará la hora de la escritura
                en formato ISO 8601 UTC sin microsegundos.

        Args:
            data (Data): Descriptor del campo a actualizar.
            new_value (Any): Nuevo valor a almacenar para el campo.
            config_path (Path): Ruta al archivo JSON de configuración.

        Returns:
            None: No devuelve valor; persiste el nuevo estado en disco.

        Raises:
            FileNotFoundError: Si `config_path` no existe al intentar leerlo.
            json.JSONDecodeError: Si el archivo JSON está corrupto.
            OSError: Si falla la escritura/renombrado del archivo temporal.
            AssertionError: Si las precondiciones no se cumplen (modo debug).
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