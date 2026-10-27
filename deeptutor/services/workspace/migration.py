"""Copy, verify and activate workspace locations without deleting source data."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any
import uuid


def _manifest(root: Path) -> dict[str, tuple[str, str]]:
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                result[relative] = ("link", os.readlink(path))
            elif path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                result[relative] = ("file", digest.hexdigest())
            elif path.is_dir():
                result[relative] = ("directory", "")
            else:
                raise ValueError(f"Cannot migrate special file: {relative}")
    return result


# (feature, table, id column) of every registry whose rows carry an absolute
# ``base_dir`` inside workspace folders. Mirrors data_migration's D7 snapshot.
_REGISTRIES = (
    ("knowledge_bases", "main.kb_registry", "kb_id"),
    ("book", "books.book_registry", "book_id"),
)


def _rows_under(conn, table: str, id_key: str, root: Path) -> list[dict]:
    prefix = str(root) + os.sep
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT {id_key} AS id, base_dir, dir_name AS name, created_by, created_at "  # nosec B608
            f"FROM {table} WHERE base_dir = ? OR substr(base_dir, 1, ?) = ? ORDER BY {id_key}",
            (str(root), len(prefix), prefix),
        )
    ]


def _registry_snapshot(sources: list[Path]) -> dict[str, dict[str, list[dict]]]:
    """Freeze registry identities/locations inside each source before copying."""
    from deeptutor.book.sharing import shared_write

    with shared_write() as conn:
        return {
            str(source): {
                feature: _rows_under(conn, table, id_key, source)
                for feature, table, id_key in _REGISTRIES
            }
            for source in sources
        }


def _relocate_registry_rows(conn, snapshot: dict, source: Path, destination: Path) -> None:
    """Move frozen KB/Book identities with their workspace in the joint writer.

    Same identity rules as data_migration's D7 relocation: the inventory under
    the source must be exactly the frozen one, and every row must still have
    its frozen location, directory name, owner and creation time.
    """
    from deeptutor.book import registry as books
    from deeptutor.knowledge import registry as kbs

    for feature, table, id_key in _REGISTRIES:
        saved_rows = snapshot[str(source)][feature]
        current = _rows_under(conn, table, id_key, source)
        if current != saved_rows:
            from deeptutor.services.workspace.models import WorkspaceError

            raise WorkspaceError(
                "Knowledge base or book registrations changed during migration. "
                "Retry the operation."
            )
        for saved in saved_rows:
            target = destination / Path(saved["base_dir"]).relative_to(source)
            if feature == "knowledge_bases":
                kbs._relocate_kb(conn, saved["id"], target)
            else:
                books._relocate_book(conn, saved["id"], target)


def migrate_locations(
    service: Any, moves: list[tuple[dict, Path]], *, new_root: Path | None = None
) -> None:
    import sqlite3

    from deeptutor.book.registry import BookRegistryError
    from deeptutor.book.sharing import shared_write
    from deeptutor.services.workspace.models import WorkspaceError

    prepared: list[tuple[dict, Path, Path]] = []
    owned_destinations: list[Path] = []
    staging: list[Path] = []
    try:
        sources = [Path(row["path"]).resolve() for row, _destination in moves]
        # Registry identities follow the workspace (Plan D global constraint);
        # freeze them before any copy so later registrations are not adopted.
        # Validate sources before touching registries — shared_write creates DB
        # files as a side effect of initialization, which is wrong for sources
        # that don't exist.
        for source in sources:
            if not source.is_dir():
                raise WorkspaceError("The source workspace folder does not exist.")
        try:
            snapshot = _registry_snapshot(sources)
        except sqlite3.OperationalError as exc:
            raise WorkspaceError(
                "The registry database is currently in use by another operation. "
                "Retry after active writes have finished."
            ) from exc
        for (row, destination), source in zip(moves, sources):
            destination = destination.expanduser().resolve()
            if source == destination:
                continue
            service._assert_allowed_root(destination)
            if not source.is_dir():
                raise WorkspaceError("The source workspace folder does not exist.")
            if destination.is_relative_to(source) or source.is_relative_to(destination):
                raise WorkspaceError("Source and destination folders cannot contain one another.")
            if destination.exists():
                raise WorkspaceError(
                    "The destination already exists. Choose a new folder to avoid overwriting files."
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.parent / f".{destination.name}.migrate-{uuid.uuid4().hex}"
            staging.append(temporary)
            before = _manifest(source)
            shutil.copytree(source, temporary, symlinks=True)
            if before != _manifest(temporary) or before != _manifest(source):
                raise WorkspaceError(
                    "Workspace files changed during migration. Retry when file writes have stopped."
                )
            # Renaming a fresh sibling directory activates the complete copy.
            temporary.rename(destination)
            staging.remove(temporary)
            owned_destinations.append(destination)
            prepared.append((row, source, destination))
        # All paths switch together, and the KB/Book registry locations switch
        # in the same crash-atomic SQLite transaction (catalog attached to D2's
        # joint writer). A failure before COMMIT leaves every old binding and
        # registry location valid; immutable published-item URLs are outside
        # these roots.
        with service._catalog_connection():
            pass  # Create the catalog schema before the joint writer attaches it.
        try:
            with shared_write(attach={"catalog": service._catalog_file()}) as conn:
                for row, source, destination in prepared:
                    _relocate_registry_rows(conn, snapshot, source, destination)
                for row, _source, destination in prepared:
                    record = conn.execute(
                        "SELECT payload FROM catalog.workspaces WHERE id = ?",
                        (row["workspace_id"],),
                    ).fetchone()
                    current = json.loads(record[0]) if record else None
                    if current is None or current["path"] != row["path"]:
                        raise WorkspaceError(
                            "Workspace location changed during migration. Retry the operation."
                        )
                    updated = {
                        **current,
                        "path": str(destination),
                        "follows_root": new_root is not None,
                        "previous_paths": [*row.get("previous_paths", []), row["path"]],
                    }
                    conn.execute(
                        "UPDATE catalog.workspaces SET payload = ? WHERE id = ?",
                        (json.dumps(updated, ensure_ascii=False), row["workspace_id"]),
                    )
                if new_root is not None:
                    conn.execute(
                        "INSERT OR REPLACE INTO catalog.metadata VALUES ('root', ?)",
                        (json.dumps(str(new_root)),),
                    )
        except sqlite3.IntegrityError as exc:
            raise WorkspaceError(
                "A knowledge base or book is already registered at the destination. "
                "Choose a new folder."
            ) from exc
        except sqlite3.OperationalError as exc:
            # A busy/locked database from a concurrent writer must surface as a
            # retryable 409, not an unhandled 500.
            raise WorkspaceError(
                "The registry database is currently in use by another operation. "
                "Retry after active writes have finished."
            ) from exc
        except BookRegistryError as exc:
            # e.g. a non-DELETE journal: registries cannot follow atomically,
            # so refuse rather than let the catalog and registries diverge.
            raise WorkspaceError(
                "Knowledge base and book registrations cannot be moved together with this "
                f"workspace: {exc}"
            ) from exc
    except BaseException:
        for path in staging + owned_destinations:
            if path.exists():
                shutil.rmtree(path)
        raise
