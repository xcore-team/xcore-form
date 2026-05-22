"""
Schémas Pydantic pour XForm — définitions, soumissions, analytics.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


# ─────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────

class FieldType(str, Enum):
    # Basiques
    TEXT       = "text"
    EMAIL      = "email"
    NUMBER     = "number"
    DATE       = "date"
    TEXTAREA   = "textarea"
    # Choix
    SELECT     = "select"
    RADIO      = "radio"
    CHECKBOX   = "checkbox"
    # Avancés
    FILE       = "file"
    SIGNATURE  = "signature"
    PHONE      = "phone"
    URL        = "url"
    HIDDEN     = "hidden"
    # Layout
    SECTION    = "section"
    DIVIDER    = "divider"


class FormStatus(str, Enum):
    ACTIVE   = "active"
    PAUSED   = "paused"
    ARCHIVED = "archived"
    DRAFT    = "draft"


class SubmissionStatus(str, Enum):
    PENDING   = "pending"
    PROCESSED = "processed"
    FAILED    = "failed"


class LogicOperator(str, Enum):
    EQ      = "eq"
    NEQ     = "neq"
    GT      = "gt"
    GTE     = "gte"
    LT      = "lt"
    LTE     = "lte"
    CONTAINS = "contains"
    NOT_EMPTY = "not_empty"
    IS_EMPTY  = "is_empty"


# ─────────────────────────────────────────────────────────────
# Logique conditionnelle
# ─────────────────────────────────────────────────────────────

class ConditionalRule(BaseModel):
    """Une règle de visibilité : show/hide selon la valeur d'un autre champ."""
    field_id:  str
    operator:  LogicOperator
    value:     Optional[Any] = None
    action:    Literal["show", "hide"] = "show"


class FieldLogic(BaseModel):
    """Logique conditionnelle sur un champ."""
    rules:     List[ConditionalRule] = Field(default_factory=list)
    match_all: bool = True   # AND vs OR entre les règles


# ─────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────

class FieldValidation(BaseModel):
    required:    bool = False
    min_length:  Optional[int] = None
    max_length:  Optional[int] = None
    min_value:   Optional[float] = None
    max_value:   Optional[float] = None
    pattern:     Optional[str] = None    # regex
    custom_msg:  Optional[str] = None    # message d'erreur custom


# ─────────────────────────────────────────────────────────────
# Champ de formulaire
# ─────────────────────────────────────────────────────────────

class FormField(BaseModel):
    id:           str = Field(default_factory=lambda: f"field_{uuid4().hex[:8]}")
    type:         FieldType
    label:        str
    name:         str                                   # clé dans les données soumises
    placeholder:  Optional[str] = None
    help_text:    Optional[str] = None
    default_value: Optional[Any] = None
    options:      List[Dict[str, str]] = Field(default_factory=list)
    # ex: [{"label": "Option A", "value": "a"}, ...]
    order:        int = 0
    width:        Literal["full", "half", "third"] = "full"
    validation:   FieldValidation = Field(default_factory=FieldValidation)
    logic:        Optional[FieldLogic] = None
    # Spécifique FILE
    accept:       Optional[str] = None  # ex: ".pdf,.jpg"
    max_size_mb:  Optional[int] = None


# ─────────────────────────────────────────────────────────────
# Étape (multi-step)
# ─────────────────────────────────────────────────────────────

class FormStep(BaseModel):
    id:          str = Field(default_factory=lambda: f"step_{uuid4().hex[:8]}")
    title:       str
    description: Optional[str] = None
    field_ids:   List[str] = Field(default_factory=list)
    order:       int = 0


# ─────────────────────────────────────────────────────────────
# Settings du formulaire
# ─────────────────────────────────────────────────────────────

class FormSettings(BaseModel):
    # Multi-step
    multi_step:              bool = False
    # Confirmations
    confirmation_email:      bool = False
    confirmation_message:    str = "Merci, votre réponse a bien été enregistrée."
    redirect_url:            Optional[str] = None
    # Automatisation xcore
    workflow_id:             Optional[str] = None   # XFlow
    create_ticket:           bool = False            # XDesk
    ticket_assignee:         Optional[str] = None
    notify_owner:            bool = True             # XPulse
    # Limites
    max_submissions:         Optional[int] = None
    close_after:             Optional[datetime] = None
    allow_edit:              bool = False
    one_submission_per_user: bool = False


# ─────────────────────────────────────────────────────────────
# Thème
# ─────────────────────────────────────────────────────────────

class FormTheme(BaseModel):
    primary_color: str = "#3B82F6"
    bg_color:      str = "#FFFFFF"
    text_color:    str = "#111827"
    font:          str = "Inter"
    border_radius: str = "8px"
    logo_url:      Optional[str] = None
    cover_url:     Optional[str] = None


# ─────────────────────────────────────────────────────────────
# Définition complète du formulaire
# ─────────────────────────────────────────────────────────────

class FormDefinition(BaseModel):
    id:          str = Field(default_factory=lambda: uuid4().hex)
    title:       str
    description: Optional[str] = None
    slug:        Optional[str] = None    # généré auto si absent
    owner_id:    str
    fields:      List[FormField] = Field(default_factory=list, min_length=1)
    steps:       List[FormStep] = Field(default_factory=list)
    settings:    FormSettings = Field(default_factory=FormSettings)
    theme:       FormTheme = Field(default_factory=FormTheme)
    status:      FormStatus = FormStatus.DRAFT
    tags:        List[str] = Field(default_factory=list)
    created_at:  Optional[datetime] = None
    updated_at:  Optional[datetime] = None

    @field_validator("slug", mode="before")
    @classmethod
    def clean_slug(cls, v: Optional[str]) -> Optional[str]:
        if v:
            return v.lower().strip().replace(" ", "-")
        return v

    def get_field(self, field_id: str) -> Optional[FormField]:
        return next((f for f in self.fields if f.id == field_id), None)

    def get_field_by_name(self, name: str) -> Optional[FormField]:
        return next((f for f in self.fields if f.name == name), None)


# ─────────────────────────────────────────────────────────────
# Soumission
# ─────────────────────────────────────────────────────────────

class SubmissionMeta(BaseModel):
    ip:            Optional[str] = None
    user_agent:    Optional[str] = None
    referrer:      Optional[str] = None
    duration_sec:  Optional[int] = None   # temps passé sur le formulaire
    submitted_at:  Optional[datetime] = None
    user_id:       Optional[str] = None   # si utilisateur connecté


class FormSubmission(BaseModel):
    id:          str = Field(default_factory=lambda: uuid4().hex)
    form_id:     str
    data:        Dict[str, Any] = Field(default_factory=dict)
    meta:        SubmissionMeta = Field(default_factory=SubmissionMeta)
    status:      SubmissionStatus = SubmissionStatus.PENDING
    created_at:  Optional[datetime] = None


# ─────────────────────────────────────────────────────────────
# Analytics
# ─────────────────────────────────────────────────────────────

class FormAnalytics(BaseModel):
    form_id:          str
    total_views:      int = 0
    total_submissions: int = 0
    completion_rate:  float = 0.0   # soumissions / vues
    avg_duration_sec: Optional[float] = None
    last_submission:  Optional[datetime] = None
    submissions_by_day: Dict[str, int] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────
# Payloads IPC
# ─────────────────────────────────────────────────────────────

class CreateFormPayload(BaseModel):
    title:       str
    description: Optional[str] = None
    owner_id:    str
    fields:      List[Dict[str, Any]]
    steps:       List[Dict[str, Any]] = Field(default_factory=list)
    settings:    Dict[str, Any] = Field(default_factory=dict)
    theme:       Dict[str, Any] = Field(default_factory=dict)
    tags:        List[str] = Field(default_factory=list)


class UpdateFormPayload(BaseModel):
    form_id:     str
    title:       Optional[str] = None
    description: Optional[str] = None
    fields:      Optional[List[Dict[str, Any]]] = None
    steps:       Optional[List[Dict[str, Any]]] = None
    settings:    Optional[Dict[str, Any]] = None
    theme:       Optional[Dict[str, Any]] = None
    status:      Optional[str] = None
    tags:        Optional[List[str]] = None


class SubmitFormPayload(BaseModel):
    slug:        str
    data:        Dict[str, Any]
    meta:        Dict[str, Any] = Field(default_factory=dict)


class GetFormPayload(BaseModel):
    form_id:     Optional[str] = None
    slug:        Optional[str] = None


class ListFormsPayload(BaseModel):
    owner_id:    Optional[str] = None
    status:      Optional[str] = None
    tags:        Optional[List[str]] = None
    limit:       int = 50
    offset:      int = 0


class ListSubmissionsPayload(BaseModel):
    form_id:     str
    status:      Optional[str] = None
    limit:       int = 50
    offset:      int = 0


class ExportPayload(BaseModel):
    form_id:     str
    format:      Literal["xlsx", "csv", "json"] = "xlsx"


class PipelineLogEntry(BaseModel):
    submission_id: str
    form_id:       str
    step:          str
    status:        Literal["success", "failed", "skipped"]
    payload:       Optional[Dict[str, Any]] = None
    error:         Optional[str] = None
    executed_at:   Optional[datetime] = None