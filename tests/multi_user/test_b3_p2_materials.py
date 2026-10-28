"""Batch 3 / P2: teacher-path material availability and migration repairs.

B3-19 per-source availability, T10-M2 applied-revision repair before a
deferred migration, the B2-19 unsettled withdrawal counts and the public
lease read used inside the migration transaction (B2-14).
"""

from __future__ import annotations

from deeptutor.learning.storage import LearningStore


# ---- B2-14 leftover: the lease is read through a public transaction method -----


def test_transaction_reads_the_paths_lease(sara_instance, as_account):
    with as_account("sara"):
        store = LearningStore()
        assert store.mutate(sara_instance.path_id, lambda tx: tx.lease_turn_id())[1] is None
        store.acquire_path_lease(sara_instance.path_id, "s1", "t1", bind_session=False)
        assert store.mutate(sara_instance.path_id, lambda tx: tx.lease_turn_id())[1] == "t1"
