"""
SQLAlchemy ORM — tables XForm.
Suit exactement le même pattern que xflow/repositories/models.py
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class XFormRecord(Base):
    """Un formulaire."""
    __tablename__ = "xform_forms"

    id:          Mapped[str] = mapped_column(String(64), primary_key=True)
    title:       Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    slug:        Mapped[str] = mapped_column(String(255), unique=True, index=True)
    owner_id:    Mapped[str] = mapped_column(String(64), index=True)

    # Stockage JSON flexible (champs, étapes, settings, thème)
    fields:      Mapped[Dict[str, Any]] = mapped_column(JSON, default=list)
    steps:       Mapped[Dict[str, Any]] = mapped_column(JSON, default=list)
    settings:    Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    theme:       Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    tags:        Mapped[Dict[str, Any]] = mapped_column(JSON, default=list)

    status:      Mapped[str] = mapped_column(String(32), default="draft", index=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at:  Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    submissions: Mapped[list["XFormSubmissionRecord"]] = relationship(
        back_populates="form", cascade="all, delete-orphan"
    )
    views:       Mapped[list["XFormViewRecord"]] = relationship(
        back_populates="form", cascade="all, delete-orphan"
    )


class XFormSubmissionRecord(Base):
    """Une réponse soumise."""
    __tablename__ = "xform_submissions"

    id:           Mapped[str] = mapped_column(String(64), primary_key=True)
    form_id:      Mapped[str] = mapped_column(
        ForeignKey("xform_forms.id", ondelete="CASCADE"), index=True
    )
    data:         Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    meta:         Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    status:       Mapped[str] = mapped_column(String(32), default="pending", index=True)
    created_at:   Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )

    form: Mapped["XFormRecord"] = relationship(back_populates="submissions")
    pipeline_logs: Mapped[list["XFormPipelineLogRecord"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )


class XFormViewRecord(Base):
    """Enregistre chaque vue du formulaire (analytics)."""
    __tablename__ = "xform_views"

    id:         Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id:    Mapped[str] = mapped_column(
        ForeignKey("xform_forms.id", ondelete="CASCADE"), index=True
    )
    ip:         Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(512))
    viewed_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    form: Mapped["XFormRecord"] = relationship(back_populates="views")


class XFormPipelineLogRecord(Base):
    """Trace chaque étape du pipeline d'automatisation."""
    __tablename__ = "xform_pipeline_logs"

    id:            Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[str] = mapped_column(
        ForeignKey("xform_submissions.id", ondelete="CASCADE"), index=True
    )
    form_id:       Mapped[str] = mapped_column(String(64), index=True)
    step:          Mapped[str] = mapped_column(String(64))
    # email_confirmation | notify_owner | xflow_trigger | xdesk_ticket
    status:        Mapped[str] = mapped_column(String(32))
    # success | failed | skipped
    payload:       Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    error:         Mapped[Optional[str]] = mapped_column(Text)
    executed_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    submission: Mapped["XFormSubmissionRecord"] = relationship(back_populates="pipeline_logs")