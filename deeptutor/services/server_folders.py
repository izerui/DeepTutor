"""No account chooses a server folder (spec 2026-10-29, P1c).

DeepTutor decides every storage location: each account's data lives under its
own directory inside the data root, and each conversation gets its own working
directory there. Routes that used to accept a server path refuse it.
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

SERVER_FOLDER_DISABLED = (
    "Choosing a server folder is not available. DeepTutor manages every storage location."
)


def refuse_server_folder() -> NoReturn:
    raise HTTPException(status_code=403, detail=SERVER_FOLDER_DISABLED)
