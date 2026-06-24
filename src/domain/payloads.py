"""
Domaine : payloads IPC (schémas de validation des commandes).
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


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


class GetFormPayload(BaseModel):
    form_id: Optional[str] = None
    slug:    Optional[str] = None


class ListFormsPayload(BaseModel):
    owner_id: Optional[str] = None
    status:   Optional[str] = None
    tags:     Optional[List[str]] = None
    limit:    int = 50
    offset:   int = 0


class SubmitFormPayload(BaseModel):
    slug:   str
    data:   Dict[str, Any]
    meta:   Dict[str, Any] = Field(default_factory=dict)


class ListSubmissionsPayload(BaseModel):
    form_id: str
    status:  Optional[str] = None
    limit:   int = 50
    offset:  int = 0


class ExportPayload(BaseModel):
    form_id: str
    format:  Literal["xlsx", "csv", "json"] = "xlsx"
