"""Admission checks shared by note companion turns and replay."""

from __future__ import annotations

import copy

from deeptutor.co_writer.storage import get_co_writer_storage
from deeptutor.notes.blocks import walk_blocks


def validate_note_binding(note_document_id: str, generation: dict | None = None) -> dict | None:
    """Resolve only within the active account/workspace, never from a client path."""
    if not note_document_id:
        if generation:
            raise ValueError("Generating a learning block requires an open notebook page.")
        return None
    doc = get_co_writer_storage().load_document(note_document_id)
    if doc is None:
        raise ValueError("The notebook page is unavailable in this workspace.")
    if not generation:
        return None
    target = next(
        (block for block in walk_blocks(doc.blocks or [])
         if block["id"] == generation.get("block_id")),
        None,
    )
    if target is None or target.get("type") != "learning":
        raise ValueError("The learning block is unavailable. Reload the notebook page.")
    if generation.get("kind") and generation["kind"] != (target.get("props") or {}).get("kind"):
        raise ValueError("The learning block changed. Reload the notebook page.")
    return copy.deepcopy(target)
