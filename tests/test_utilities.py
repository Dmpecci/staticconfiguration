from pathlib import Path

def remove_json(path: Path):
    """
    Remove a JSON file if it exists.

    Responsibility:
        - Safely delete the specified JSON file without raising an error if
          the file does not exist.
    """
    try:
        if path.suffix.lower() != '.json':
            return
        if path.exists() and not path.is_file():
            return
        path.unlink()
    except FileNotFoundError:
        pass