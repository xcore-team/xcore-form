"""
Domaine : définition d'un formulaire.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class FieldType(str, Enum):
    TEXT      = "text"
    EMAIL     = "email"
    NUMBER    = "number"
    DATE      = "date"
    TEXTAREA  = "textarea"
    SELECT    = "select"
    RADIO     = "radio"
    CHECKBOX  = "checkbox"
    FILE      = "file"
    SIGNATURE = "signature"
    PHONE     = "phone"
    URL       = "url"
    HIDDEN    = "hidden"
    SECTION   = "section"
    DIVIDER   = "divider"


class FormStatus(str, Enum):
    ACTIVE   = "active"
    PAUSED   = "paused"
    ARCHIVED = "archived"
    DRAFT    = "draft"


class LogicOperator(str, Enum):
    EQ        = "eq"
    NEQ       = "neq"
    GT        = "gt"
    GTE       = "gte"
    LT        = "lt"
    LTE       = "lte"
    CONTAINS  = "contains"
    NOT_EMPTY = "not_empty"
    IS_EMPTY  = "is_empty"


class ConditionalRule(BaseModel):
    field_id: str
    operator: LogicOperator
    value:    Optional[Any] = None
    action:   Literal["show", "hide"] = "show"


class FieldLogic(BaseModel):
    rules:     List[ConditionalRule] = Field(default_factory=list)
    match_all: bool = True


class FieldValidation(BaseModel):
    required:   bool = False
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    min_value:  Optional[float] = None
    max_value:  Optional[float] = None
    pattern:    Optional[str] = None
    custom_msg: Optional[str] = None


class FormField(BaseModel):
    id:            str = Field(default_factory=lambda: f"field_{uuid4().hex[:8]}")
    type:          FieldType
    label:         str
    name:          str
    placeholder:   Optional[str] = None
    help_text:     Optional[str] = None
    default_value: Optional[Any] = None
    options:       List[Dict[str, str]] = Field(default_factory=list)
    order:         int = 0
    width:         Literal["full", "half", "third"] = "full"
    validation:    FieldValidation = Field(default_factory=FieldValidation)
    logic:         Optional[FieldLogic] = None
    accept:        Optional[str] = None
    max_size_mb:   Optional[int] = None


class FormStep(BaseModel):
    id:          str = Field(default_factory=lambda: f"step_{uuid4().hex[:8]}")
    title:       str
    description: Optional[str] = None
    field_ids:   List[str] = Field(default_factory=list)
    order:       int = 0


class FormSettings(BaseModel):
    multi_step:              bool = False
    confirmation_email:      bool = False
    confirmation_message:    str = "Merci, votre réponse a bien été enregistrée."
    redirect_url:            Optional[str] = None
    workflow_name:           Optional[str] = None   # nom du workflow XFlow à déclencher
    create_ticket:           bool = False
    ticket_assignee:         Optional[str] = None
    notify_owner:            bool = True
    max_submissions:         Optional[int] = None
    close_after:             Optional[datetime] = None
    allow_edit:              bool = False
    one_submission_per_user: bool = False


class FormTheme(BaseModel):
    primary_color: str = "#3B82F6"
    bg_color:      str = "#FFFFFF"
    text_color:    str = "#111827"
    font:          str = "Inter"
    border_radius: str = "8px"
    logo_url:      Optional[str] = None
    cover_url:     Optional[str] = None


class FormDefinition(BaseModel):
    id:          str = Field(default_factory=lambda: uuid4().hex)
    title:       str
    description: Optional[str] = None
    slug:        Optional[str] = None
    owner_id:    str
    tenant_id:   Optional[str] = None   # tenant du propriétaire, propagé aux xflow runs
    fields:      List[FormField] = Field(default_factory=list)
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
