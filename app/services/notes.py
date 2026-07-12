from pathlib import Path


NOTES_ROOT = Path("notes")


def get_person_notes(person_id: int) -> str:
    NOTES_ROOT.mkdir(parents=True, exist_ok=True)

    note_file = NOTES_ROOT / f"{person_id}.txt"

    if not note_file.exists():
        return ""

    return note_file.read_text(encoding="utf-8")


def save_person_notes(person_id: int, notes: str) -> None:
    NOTES_ROOT.mkdir(parents=True, exist_ok=True)

    note_file = NOTES_ROOT / f"{person_id}.txt"

    note_file.write_text(
        notes,
        encoding="utf-8",
    )
