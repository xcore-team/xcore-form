"""
XFormStore — accès aux données XForm.
Même pattern que xflow/repositories/workflow.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, func, select

from ..models.orm import (
    XFormPipelineLogRecord,
    XFormRecord,
    XFormSubmissionRecord,
    XFormViewRecord,
)
from ..schemas.form import (
    FormAnalytics,
    FormDefinition,
    FormStatus,
    FormSubmission,
    PipelineLogEntry,
    SubmissionMeta,
    SubmissionStatus,
)

logger = logging.getLogger("xform.store")

FORM_CACHE_TTL = 3600
ANALYTICS_CACHE_TTL = 300


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class XFormStore:
    def __init__(self, db: Any, cache: Any | None = None) -> None:
        self._db = db
        self._cache = cache

    # ─────────────────────────────────────────────────────────
    # Cache helpers
    # ─────────────────────────────────────────────────────────

    def _form_key(self, form_id: str) -> str:
        return f"xform:form:{form_id}"

    def _slug_key(self, slug: str) -> str:
        return f"xform:slug:{slug}"

    def _analytics_key(self, form_id: str) -> str:
        return f"xform:analytics:{form_id}"

    async def _cache_form(self, form: FormDefinition) -> None:
        if not self._cache:
            return
        data = form.model_dump(mode="json")
        await self._cache.set(self._form_key(form.id), data, ttl=FORM_CACHE_TTL)
        if form.slug:
            await self._cache.set(self._slug_key(form.slug), data, ttl=FORM_CACHE_TTL)

    async def _invalidate_form(self, form: FormDefinition) -> None:
        if not self._cache:
            return
        await self._cache.delete(self._form_key(form.id))
        if form.slug:
            await self._cache.delete(self._slug_key(form.slug))
        await self._cache.delete(self._analytics_key(form.id))

    # ─────────────────────────────────────────────────────────
    # Formulaires
    # ─────────────────────────────────────────────────────────

    async def save_form(self, form: FormDefinition) -> FormDefinition:
        now = _utcnow()
        async with self._db.session() as session:
            record = await session.get(XFormRecord, form.id)
            if record is None:
                form.created_at = now
                form.updated_at = now
                record = XFormRecord(
                    id=form.id,
                    title=form.title,
                    description=form.description,
                    slug=form.slug or form.id,
                    owner_id=form.owner_id,
                    fields=[f.model_dump(mode="json") for f in form.fields],
                    steps=[s.model_dump(mode="json") for s in form.steps],
                    settings=form.settings.model_dump(mode="json"),
                    theme=form.theme.model_dump(mode="json"),
                    tags=form.tags,
                    status=form.status.value,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            else:
                form.updated_at = now
                record.title = form.title
                record.description = form.description
                record.slug = form.slug or record.slug
                record.fields = [f.model_dump(mode="json") for f in form.fields]
                record.steps = [s.model_dump(mode="json") for s in form.steps]
                record.settings = form.settings.model_dump(mode="json")
                record.theme = form.theme.model_dump(mode="json")
                record.tags = form.tags
                record.status = form.status.value
                record.updated_at = now
            await session.commit()

        await self._cache_form(form)
        logger.info("Form '%s' sauvegardé (slug=%s)", form.id, form.slug)
        return form

    async def get_form(self, form_id: str) -> Optional[FormDefinition]:
        # Cache first
        if self._cache:
            cached = await self._cache.get(self._form_key(form_id))
            if cached:
                return FormDefinition.model_validate(cached)

        async with self._db.session() as session:
            record = await session.get(XFormRecord, form_id)
            if record is None:
                return None
            form = self._record_to_form(record)

        await self._cache_form(form)
        return form

    async def get_form_by_slug(self, slug: str) -> Optional[FormDefinition]:
        # Cache first
        if self._cache:
            cached = await self._cache.get(self._slug_key(slug))
            if cached:
                return FormDefinition.model_validate(cached)

        async with self._db.session() as session:
            result = await session.execute(
                select(XFormRecord).where(XFormRecord.slug == slug)
            )
            record = result.scalar_one_or_none()
            if record is None:
                return None
            form = self._record_to_form(record)

        await self._cache_form(form)
        return form

    async def list_forms(
        self,
        owner_id: Optional[str] = None,
        status: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[FormDefinition]:
        async with self._db.session() as session:
            stmt = select(XFormRecord).order_by(desc(XFormRecord.created_at))
            if owner_id:
                stmt = stmt.where(XFormRecord.owner_id == owner_id)
            if status:
                stmt = stmt.where(XFormRecord.status == status)
            stmt = stmt.limit(limit).offset(offset)
            result = await session.execute(stmt)
            records = result.scalars().all()

        forms = [self._record_to_form(r) for r in records]
        if tags:
            forms = [f for f in forms if any(t in f.tags for t in tags)]
        return forms

    async def delete_form(self, form_id: str) -> bool:
        form = await self.get_form(form_id)
        if not form:
            return False
        async with self._db.session() as session:
            record = await session.get(XFormRecord, form_id)
            if record:
                await session.delete(record)
                await session.commit()
        await self._invalidate_form(form)
        return True

    async def slug_exists(self, slug: str, exclude_id: Optional[str] = None) -> bool:
        async with self._db.session() as session:
            stmt = select(XFormRecord.id).where(XFormRecord.slug == slug)
            if exclude_id:
                stmt = stmt.where(XFormRecord.id != exclude_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    # ─────────────────────────────────────────────────────────
    # Soumissions
    # ─────────────────────────────────────────────────────────

    async def save_submission(self, submission: FormSubmission) -> FormSubmission:
        now = _utcnow()
        submission.created_at = now
        async with self._db.session() as session:
            record = XFormSubmissionRecord(
                id=submission.id,
                form_id=submission.form_id,
                data=submission.data,
                meta=submission.meta.model_dump(mode="json"),
                status=submission.status.value,
                created_at=now,
            )
            session.add(record)
            await session.commit()
        return submission

    async def get_submission(self, submission_id: str) -> Optional[FormSubmission]:
        async with self._db.session() as session:
            record = await session.get(XFormSubmissionRecord, submission_id)
            if record is None:
                return None
            return self._record_to_submission(record)

    async def list_submissions(
        self,
        form_id: str,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[FormSubmission]:
        async with self._db.session() as session:
            stmt = (
                select(XFormSubmissionRecord)
                .where(XFormSubmissionRecord.form_id == form_id)
                .order_by(desc(XFormSubmissionRecord.created_at))
                .limit(limit)
                .offset(offset)
            )
            if status:
                stmt = stmt.where(XFormSubmissionRecord.status == status)
            result = await session.execute(stmt)
            records = result.scalars().all()
        return [self._record_to_submission(r) for r in records]

    async def update_submission_status(
        self, submission_id: str, status: SubmissionStatus
    ) -> None:
        async with self._db.session() as session:
            record = await session.get(XFormSubmissionRecord, submission_id)
            if record:
                record.status = status.value
                await session.commit()

    # ─────────────────────────────────────────────────────────
    # Vues (analytics)
    # ─────────────────────────────────────────────────────────

    async def track_view(self, form_id: str, ip: str, user_agent: str) -> None:
        async with self._db.session() as session:
            view = XFormViewRecord(form_id=form_id, ip=ip, user_agent=user_agent)
            session.add(view)
            await session.commit()

    async def get_analytics(self, form_id: str) -> FormAnalytics:
        if self._cache:
            cached = await self._cache.get(self._analytics_key(form_id))
            if cached:
                return FormAnalytics.model_validate(cached)

        async with self._db.session() as session:
            # Total views
            views_count = await session.scalar(
                select(func.count(XFormViewRecord.id)).where(
                    XFormViewRecord.form_id == form_id
                )
            )
            # Total submissions
            subs_count = await session.scalar(
                select(func.count(XFormSubmissionRecord.id)).where(
                    XFormSubmissionRecord.form_id == form_id
                )
            )
            # Last submission
            last_sub = await session.scalar(
                select(func.max(XFormSubmissionRecord.created_at)).where(
                    XFormSubmissionRecord.form_id == form_id
                )
            )

        total_views = views_count or 0
        total_subs = subs_count or 0
        rate = round(total_subs / total_views, 3) if total_views > 0 else 0.0

        analytics = FormAnalytics(
            form_id=form_id,
            total_views=total_views,
            total_submissions=total_subs,
            completion_rate=rate,
            last_submission=last_sub,
        )
        if self._cache:
            await self._cache.set(
                self._analytics_key(form_id),
                analytics.model_dump(mode="json"),
                ttl=ANALYTICS_CACHE_TTL,
            )
        return analytics

    # ─────────────────────────────────────────────────────────
    # Pipeline logs
    # ─────────────────────────────────────────────────────────

    async def log_pipeline_step(self, entry: PipelineLogEntry) -> None:
        async with self._db.session() as session:
            record = XFormPipelineLogRecord(
                submission_id=entry.submission_id,
                form_id=entry.form_id,
                step=entry.step,
                status=entry.status,
                payload=entry.payload,
                error=entry.error,
                executed_at=_utcnow(),
            )
            session.add(record)
            await session.commit()

    # ─────────────────────────────────────────────────────────
    # Helpers de conversion
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _record_to_form(record: XFormRecord) -> FormDefinition:
        from ..schemas.form import FormField, FormStep, FormSettings, FormTheme

        fields = [FormField.model_validate(f) for f in (record.fields or [])]
        steps = [FormStep.model_validate(s) for s in (record.steps or [])]

        return FormDefinition(
            id=record.id,
            title=record.title,
            description=record.description,
            slug=record.slug,
            owner_id=record.owner_id,
            fields=fields,
            steps=steps,
            settings=FormSettings.model_validate(record.settings or {}),
            theme=FormTheme.model_validate(record.theme or {}),
            tags=record.tags or [],
            status=FormStatus(record.status),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _record_to_submission(record: XFormSubmissionRecord) -> FormSubmission:
        return FormSubmission(
            id=record.id,
            form_id=record.form_id,
            data=record.data or {},
            meta=SubmissionMeta.model_validate(record.meta or {}),
            status=SubmissionStatus(record.status),
            created_at=record.created_at,
        )