"""
Domaine : métadonnées des fichiers uploadés.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class FileEntry(BaseModel):
    file_id:       str
    form_id:       str
    submission_id: Optional[str] = None
    field_name:    str
    original_name: str
    stored_name:   str
    size_bytes:    int
    mime_type:     str
    uploaded_at:   Optional[datetime] = None
