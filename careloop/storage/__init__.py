"""Storage backends. `build_repository` picks one from settings."""

from __future__ import annotations

from careloop.config import Settings
from careloop.storage.repository import Repository


def build_repository(settings: Settings) -> Repository:
    if settings.storage_backend == "firestore":
        # Imported lazily so the default SQLite setup needs no Google libraries.
        from careloop.storage.firestore_repository import FirestoreRepository

        return FirestoreRepository.from_project(settings.firestore_project)

    from careloop.storage.sqlite_repository import SqliteRepository

    return SqliteRepository(settings.sqlite_path)
