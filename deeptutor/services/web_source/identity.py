"""Compatibility identities for registered web-source job state."""

from pathlib import Path

from deeptutor.knowledge.registry import KbRecord


def legacy_job_owners(record: KbRecord) -> list[str]:
    from deeptutor.knowledge import registry
    from deeptutor.multi_user.identity import load_users
    from deeptutor.multi_user.models import LOCAL_ADMIN_ID
    from deeptutor.multi_user.paths import USERS_ROOT, get_admin_path_service

    owners = [record.created_by]
    candidates = []
    root = Path(record.base_dir).resolve()
    # Transfers/adoption preserve the physical directory of the former owner.
    if root.is_relative_to(USERS_ROOT):
        candidates.append(root.relative_to(USERS_ROOT).parts[0])
    partners = get_admin_path_service().workspace_root / "partners"
    if root.is_relative_to(partners):
        candidates.append(f"partner_{root.relative_to(partners).parts[0]}")
    # Older routes keyed administrator actions by the actor, not the creator.
    administrators = [LOCAL_ADMIN_ID, *(
        str(user["id"]) for user in load_users().values()
        if user.get("role") == "admin" and user.get("id")
    )]
    candidates.extend(administrators)
    records = registry.list_records()
    for candidate in candidates:
        # An actor-keyed legacy row may instead belong to the admin's own KB.
        # Never import that KB's pairing data into another account's source.
        if not any(
            other.kb_id != record.kb_id
            and other.dir_name == record.dir_name
            and other.created_by != record.created_by
            for other in records
        ):
            owners.append(candidate)
    return list(dict.fromkeys(owners))
