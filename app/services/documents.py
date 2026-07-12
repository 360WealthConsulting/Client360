from pathlib import Path


DOCUMENT_ROOT = Path("documents")


def get_person_documents(person_id: int):
    """
    Return documents associated with a canonical person.

    Future versions will retrieve records from PostgreSQL.
    """

    person_folder = DOCUMENT_ROOT / str(person_id)

    if not person_folder.exists():
        return []

    documents = []

    for file in sorted(person_folder.iterdir()):
        if file.is_file():
            documents.append(
                {
                    "name": file.name,
                    "path": str(file),
                    "size": file.stat().st_size,
                }
            )

    return documents
