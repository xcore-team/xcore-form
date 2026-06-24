"""
Domaine : soumissions et pipeline logs.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class SubmissionStatus(str, Enum):
    PENDING   = "pending"
    PROCESSED = "processed"
    FAILED    = "failed"


class SubmissionMeta(BaseModel):
    ip:           Optional[str] = None
    user_agent:   Optional[str] = None
    referrer:     Optional[str] = None
    duration_sec: Optional[int] = None
    submitted_at: Optional[datetime] = None
    user_id:      Optional[str] = None


class FormSubmission(BaseModel):
    id:         str = Field(default_factory=lambda: uuid4().hex)
    form_id:    str
    data:       Dict[str, Any] = Field(default_factory=dict)
    meta:       SubmissionMeta = Field(default_factory=SubmissionMeta)
    status:     SubmissionStatus = SubmissionStatus.PENDING
    created_at: Optional[datetime] = None


class PipelineLogEntry(BaseModel):
    submission_id: str
    form_id:       str
    step:          str
    status:        Literal["success", "failed", "skipped"]
    payload:       Optional[Dict[str, Any]] = None
    error:         Optional[str] = None
    executed_at:   Optional[datetime] = None
