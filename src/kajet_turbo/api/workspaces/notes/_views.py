from pathlib import Path

from kajet_turbo.workspace import note_filepath


def enrich_note_item(ws_path: str, note: dict) -> dict:
    filepath = note_filepath(ws_path, note["folder"], note["title"])
    try:
        size_bytes = Path(filepath).stat().st_size
    except OSError:
        size_bytes = 0
    return {**note, "size_bytes": size_bytes}


def enrich_note_items(ws_path: str, notes: list[dict]) -> list[dict]:
    return [enrich_note_item(ws_path, note) for note in notes]
