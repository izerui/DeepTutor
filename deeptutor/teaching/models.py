"""Shapes of teacher mastery templates, revisions and assignments."""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from deeptutor.learning.models import LearningModule, TopicSource

TemplateStatus = Literal["draft", "published", "archived"]
AssignmentStatus = Literal["active", "revoked"]
ChangeKind = Literal["unchanged", "reworded", "added", "removed", "redefined"]
DecisionKind = Literal["unchanged", "reworded", "redefined", "added"]
KbAccess = Literal["public", "reference_only"]
KbChoice = Literal["make_public", "reference_only"]


class MasteryTemplate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    owner_user_id: str
    title: str
    description: str = ""
    status: TemplateStatus = "draft"
    current_revision: int = 0
    #: The teacher's own path the template is edited and trialled in. Empty
    #: once the owning account is deleted and the template moves to an admin.
    draft_path_id: str = ""
    draft_workspace_id: str = ""
    #: Draft objective id -> stable template id, for objectives the teacher
    #: confirmed as a rewording or redefinition of an earlier one.
    kp_aliases: dict[str, str] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class TemplateOutline(BaseModel):
    """The curriculum of one revision: structure only, never learner state."""

    model_config = ConfigDict(extra="ignore")

    name: str
    goal: str = ""
    description: str = ""
    emoji: str = "🧭"
    modules: list[LearningModule] = Field(default_factory=list)


class TemplateDocRef(BaseModel):
    """One document of a knowledge base, as published (spec 4.2 "资料版本")."""

    model_config = ConfigDict(extra="ignore")

    doc_id: str
    rel_path: str
    version: int


class TemplateKbRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kb_id: str
    #: ``None`` means the whole knowledge base; a list narrows it to documents
    #: (plan B's fixed ``doc_id`` values, never paths).
    doc_ids: list[str] | None = None
    documents: list[TemplateDocRef] = Field(default_factory=list)
    access: KbAccess = "public"


class TemplateBookRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    book_id: str
    access: KbAccess = "public"
    #: Knowledge bases the book itself cites, readable while reading it.
    kb_ids: list[str] = Field(default_factory=list)


class TemplateSources(BaseModel):
    model_config = ConfigDict(extra="ignore")

    #: Topic sources as students see them, rewritten to ``kb:<id>`` / ``book:<id>``.
    sources: list[TopicSource] = Field(default_factory=list)
    kb_refs: list[TemplateKbRef] = Field(default_factory=list)
    book_refs: list[TemplateBookRef] = Field(default_factory=list)


class ChangeEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: ChangeKind
    previous_name: str = ""


class TemplateRevision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    template_id: str
    revision: int
    outline: TemplateOutline
    sources: TemplateSources = Field(default_factory=TemplateSources)
    #: Stable kp id -> what happened to it relative to the previous revision.
    change_log: dict[str, ChangeEntry] = Field(default_factory=dict)
    published_by: str
    published_at: float = Field(default_factory=time.time)


class MasteryAssignment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    template_id: str
    student_user_id: str
    assigned_by: str
    status: AssignmentStatus = "active"
    instance_path_id: str = ""
    applied_revision: int = 0
    assigned_at: float = Field(default_factory=time.time)
    revoked_at: float | None = None


class KpDecision(BaseModel):
    """The teacher's verdict on one knowledge point of a republished outline."""

    model_config = ConfigDict(extra="forbid")

    kind: DecisionKind
    #: For a point whose draft id is new: the removed stable id it continues.
    from_id: str = ""


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The draft path's revision the preview was computed from.
    draft_revision: int
    kp_decisions: dict[str, KpDecision] = Field(default_factory=dict)
    #: kb_id -> choice, for every private KB of the caller the path or one of
    #: its books cites.
    kb_choices: dict[str, KbChoice] = Field(default_factory=dict)
    #: book_id -> choice, for the caller's own private books the path cites.
    book_choices: dict[str, KbChoice] = Field(default_factory=dict)
    student_ids: list[str] = Field(default_factory=list, max_length=500)
