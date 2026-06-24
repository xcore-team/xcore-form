"""
Domaine : analytics d'un formulaire.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel, Field


class FormAnalytics(BaseModel):
    form_id:            str
    total_views:        int = 0
    total_submissions:  int = 0
    completion_rate:    float = 0.0
    avg_duration_sec:   Optional[float] = None
    last_submission:    Optional[datetime] = None
    submissions_by_day: Dict[str, int] = Field(default_factory=dict)
