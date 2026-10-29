"""P3a: classes API — public invite preview, QR code, error mapping."""

from __future__ import annotations

import asyncio

from fastapi import HTTPException
import pytest

from deeptutor.api.routers import classes as router


def test_qr_code_renders_svg_for_web_links_only():
    response = asyncio.run(router.qr_code("https://example.com/join/abc"))
    assert response.media_type == "image/svg+xml"
    assert b"<svg" in response.body
    with pytest.raises(HTTPException) as refused:
        asyncio.run(router.qr_code("javascript:alert(1)"))
    assert refused.value.status_code == 400


def test_public_preview_names_the_class_and_maps_errors(as_user, seed_user):
    from deeptutor.classes.service import ClassService

    seed_user("boss", role="admin")
    tina = seed_user("tina", role="teacher")
    with as_user(tina["id"], role="teacher", username="tina"):
        cls = ClassService().create("Physics 1")
        token = ClassService().regenerate_link(cls["id"])["token"]
    assert asyncio.run(router.invite_link_info(token))["class_name"] == "Physics 1"
    with pytest.raises(HTTPException) as missing:
        asyncio.run(router.invite_link_info("nope"))
    assert missing.value.status_code == 404


def test_students_reach_classes_under_a_learning_policy():
    from deeptutor.api.routers.auth import _learning_surface_for_path

    assert _learning_surface_for_path("/api/classes/join", "POST") == "chat"
    assert _learning_surface_for_path("/api/classes/invitations/mine") == "chat"
