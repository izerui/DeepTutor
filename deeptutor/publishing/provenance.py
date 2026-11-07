"""Where an item a user sees comes from (spec 2026-11-02 §3.1).

One rule for every list: books, knowledge bases, Mastery Paths, practice
sets. The publisher's live role decides the label, so a learner who becomes
an administrator turns "shared" into "school" without touching the item.

* ``own``     — the viewer's own item ("我自己的");
* ``school``  — published by an administrator ("学校发布");
* ``shared``  — published by another learner, its author ("name 分享";
  spec 2026-11-05: no author label any more).
"""

from __future__ import annotations

from typing import Any

from deeptutor.multi_user.models import LOCAL_ADMIN_ID, CurrentUser


def provenance(owner_id: str, viewer: CurrentUser, *, accounts: dict[str, dict[str, Any]] | None = None) -> dict[str, str]:
    """``{"kind": ..., "name": ...}`` for an item owned by *owner_id*, as *viewer* sees it."""
    from deeptutor.multi_user.ownership import owns

    if owns(owner_id, viewer):
        return {"kind": "own", "name": ""}
    if owner_id == LOCAL_ADMIN_ID:
        return {"kind": "school", "name": ""}
    if accounts is None:
        from deeptutor.multi_user.accounts import accounts as _accounts

        accounts = _accounts()
    account = accounts.get(owner_id) or {}
    role = account.get("role")
    if role == "admin":
        return {"kind": "school", "name": ""}
    return {"kind": "shared", "name": str(account.get("username") or "")}


__all__ = ["provenance"]
